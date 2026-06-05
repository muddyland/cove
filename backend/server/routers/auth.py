import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server import oidc as oidc_module
from server.config import get_settings
from server.deps import CurrentUser, DbSession, resolve_user_from_token
from server.models import User, Workspace
from server.net import client_ip
from server.schemas import (
    AuthConfig,
    ChangePasswordRequest,
    LoginRequest,
    SetupRequest,
    TokenResponse,
    UserOut,
)
from server.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    sign_state,
    validate_username,
    verify_password,
    verify_state,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_STREAM_RE = re.compile(r"^/workspace/([^/]+)/")

# Module-level sliding-window rate limiter for login attempts, keyed by client IP.
_login_attempts: dict[str, list[float]] = {}


def _record_audit(db: Session, action: str, *, detail=None, user=None, ip=None) -> None:
    # Import lazily to avoid a circular import at module load time.
    from server.main import record_audit

    record_audit(db, action, detail=detail, user=user, ip=ip)


def _set_auth_cookies(resp: Response, user: User) -> None:
    settings = get_settings()
    access = create_access_token(user.id, user.is_admin)
    refresh = create_refresh_token(user.id)
    domain = settings.cookie_domain or None
    resp.set_cookie(
        settings.cookie_session_name,
        access,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        domain=domain,
        max_age=settings.access_token_minutes * 60,
    )
    resp.set_cookie(
        settings.cookie_refresh_name,
        refresh,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/auth",
        domain=domain,
        max_age=settings.refresh_token_days * 86400,
    )


def _clear_auth_cookies(resp: Response) -> None:
    settings = get_settings()
    domain = settings.cookie_domain or None
    resp.delete_cookie(settings.cookie_session_name, path="/", domain=domain)
    resp.delete_cookie(settings.cookie_refresh_name, path="/api/auth", domain=domain)


def _check_rate_limit(ip: str) -> bool:
    """Return True if the request is allowed, False if the limit is exceeded."""
    settings = get_settings()
    now = time.monotonic()
    window = settings.login_rate_window_seconds
    bucket = [t for t in _login_attempts.get(ip, []) if now - t < window]
    if len(bucket) >= settings.login_rate_limit:
        _login_attempts[ip] = bucket
        return False
    bucket.append(now)
    _login_attempts[ip] = bucket
    return True


def _needs_setup(db: Session) -> bool:
    return db.scalar(select(func.count()).select_from(User)) == 0


@router.get("/config", response_model=AuthConfig)
def get_auth_config(db: DbSession):
    settings = get_settings()
    return AuthConfig(
        oidc_enabled=settings.oidc_enabled,
        oidc_provider_name=settings.oidc_provider_name,
        # In OIDC-only mode there are no local users to set up.
        needs_setup=_needs_setup(db) and not settings.oidc_only_active,
        oidc_only=settings.oidc_only_active,
    )


@router.post("/setup", status_code=status.HTTP_201_CREATED)
def setup(body: SetupRequest, request: Request, db: DbSession):
    if get_settings().oidc_only_active:
        raise HTTPException(status_code=403, detail="Local accounts are disabled (OIDC only)")
    if not _needs_setup(db):
        raise HTTPException(status_code=410, detail="Setup already complete")
    validate_username(body.username)
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        auth_provider="local",
        is_admin=True,
        last_login_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    _record_audit(db, "setup", user=user, ip=client_ip(request))
    resp = JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=TokenResponse(access_token=create_access_token(user.id, user.is_admin)).model_dump(),
    )
    _set_auth_cookies(resp, user)
    return resp


@router.post("/login")
def login(body: LoginRequest, request: Request, db: DbSession):
    if get_settings().oidc_only_active:
        raise HTTPException(status_code=403, detail="Local login is disabled (OIDC only)")
    ip = client_ip(request)
    if not _check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Too many login attempts")

    user = db.scalar(select(User).where(User.username == body.username))
    if (
        not user
        or user.auth_provider != "local"
        or not user.password_hash
        or not verify_password(body.password, user.password_hash)
    ):
        _record_audit(db, "login.fail", detail=body.username, user=user, ip=ip)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    _record_audit(db, "login.success", user=user, ip=ip)
    resp = JSONResponse(
        content=TokenResponse(access_token=create_access_token(user.id, user.is_admin)).model_dump()
    )
    _set_auth_cookies(resp, user)
    return resp


@router.post("/refresh")
def refresh(request: Request, db: DbSession):
    settings = get_settings()
    token = request.cookies.get(settings.cookie_refresh_name)
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")
    payload = decode_token(token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    sub = payload.get("sub")
    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    # Enforce revocation: iat must be >= tokens_valid_from.
    if user.tokens_valid_from:
        issued_at = datetime.fromtimestamp(payload.get("iat", 0), tz=timezone.utc)
        if issued_at < user.tokens_valid_from.replace(tzinfo=timezone.utc):
            raise HTTPException(status_code=401, detail="Refresh token revoked")

    resp = JSONResponse(
        content=TokenResponse(access_token=create_access_token(user.id, user.is_admin)).model_dump()
    )
    # Set fresh session cookie + rotate refresh cookie.
    _set_auth_cookies(resp, user)
    return resp


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


@router.post("/logout")
def logout(user: CurrentUser, request: Request, db: DbSession):
    user.tokens_valid_from = datetime.now(timezone.utc)
    db.commit()
    _record_audit(db, "logout", user=user, ip=client_ip(request))
    resp = JSONResponse(content={"ok": True})
    _clear_auth_cookies(resp)
    return resp


@router.post("/change-password")
def change_password(body: ChangePasswordRequest, user: CurrentUser, db: DbSession):
    if user.auth_provider != "local":
        raise HTTPException(status_code=400, detail="Cannot change password for SSO accounts")
    if not verify_password(body.current_password, user.password_hash or ""):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    user.password_hash = hash_password(body.new_password)
    user.tokens_valid_from = datetime.now(timezone.utc)
    db.commit()
    resp = JSONResponse(
        content=TokenResponse(access_token=create_access_token(user.id, user.is_admin)).model_dump()
    )
    _set_auth_cookies(resp, user)
    return resp


@router.get("/oidc/login")
async def oidc_login(request: Request):
    settings = get_settings()
    if not settings.oidc_enabled:
        raise HTTPException(status_code=404, detail="OIDC not configured")
    await oidc_module.fetch_discovery()
    nonce = oidc_module.generate_state()
    signed = sign_state(nonce)
    redirect_uri = str(request.base_url).rstrip("/") + "/api/auth/oidc/callback"
    # The state param sent to the IdP is the signed value.
    auth_url = oidc_module.build_auth_url(redirect_uri=redirect_uri, state=signed)
    resp = RedirectResponse(url=auth_url)
    resp.set_cookie(
        "oidc_state",
        signed,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=300,
        path="/",
    )
    return resp


@router.get("/oidc/callback")
async def oidc_callback(
    request: Request,
    code: str,
    state: str,
    db: DbSession,
):
    settings = get_settings()
    if not settings.oidc_enabled:
        raise HTTPException(status_code=404, detail="OIDC not configured")

    cookie_state = request.cookies.get("oidc_state")
    if (
        not cookie_state
        or state != cookie_state
        or verify_state(cookie_state) is None
    ):
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    redirect_uri = str(request.base_url).rstrip("/") + "/api/auth/oidc/callback"
    token_response = await oidc_module.exchange_code(code=code, redirect_uri=redirect_uri)
    try:
        claims = await oidc_module.verify_id_token(token_response["id_token"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid id_token") from exc

    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=400, detail="No subject claim in id_token")

    user = db.scalar(select(User).where(User.oidc_sub == sub))
    if not user:
        username = oidc_module.extract_username(claims)
        base = username
        suffix = 0
        while db.scalar(select(User).where(User.username == username)):
            suffix += 1
            username = f"{base}{suffix}"
        user = User(
            username=username,
            auth_provider="oidc",
            oidc_sub=sub,
            is_admin=oidc_module.is_admin_from_claims(claims),
        )
        db.add(user)
    else:
        if settings.oidc_admin_group:
            user.is_admin = oidc_module.is_admin_from_claims(claims)

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    _record_audit(db, "login.oidc", user=user, ip=client_ip(request))
    resp = RedirectResponse(url="/")
    resp.delete_cookie("oidc_state", path="/")
    _set_auth_cookies(resp, user)
    return resp


@router.get("/forward")
def forward_auth(request: Request, db: DbSession):
    """Traefik ForwardAuth endpoint. Validates the session cookie itself.

    Robust: any missing piece results in 401.
    """
    settings = get_settings()
    try:
        token = request.cookies.get(settings.cookie_session_name)
        if not token:
            return Response(status_code=401)
        user = resolve_user_from_token(db, token)
        if not user:
            return Response(status_code=401)

        uri = request.headers.get("X-Forwarded-Uri") or request.headers.get("X-Forwarded-Path")

        public_id = None
        detail = uri
        # Subdomain mode: resolve from the original host's leading label.
        if settings.workspace_domain:
            host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host")
            if host:
                host = host.split(":", 1)[0]  # strip any :port
                suffix = f".{settings.workspace_domain}"
                if host.endswith(suffix):
                    label = host[: -len(suffix)]
                    if label and "." not in label:
                        public_id = label
                        detail = host

        # Fall back to the subpath route on X-Forwarded-Uri.
        if public_id is None and uri:
            match = _STREAM_RE.match(uri)
            if match:
                public_id = match.group(1)

        if not public_id:
            return Response(status_code=401)

        ws = db.scalar(select(Workspace).where(Workspace.public_id == public_id))
        if not ws:
            _record_audit(db, "stream.deny", detail=detail, user=user, ip=client_ip(request))
            return Response(status_code=401)

        if ws.user_id == user.id or user.is_admin:
            return Response(status_code=200, headers={"X-Cove-User": user.username})

        _record_audit(db, "stream.deny", detail=detail, user=user, ip=client_ip(request))
        return Response(status_code=401)
    except Exception:
        return Response(status_code=401)
