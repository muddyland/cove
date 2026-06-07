import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server import oidc as oidc_module
from server.config import get_settings
from server.deps import CurrentUser, DbSession, _check_revocation, resolve_user_from_token
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
    create_stream_token,
    decode_token,
    hash_password,
    sign_state,
    validate_username,
    verify_password,
    verify_state,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_STREAM_RE = re.compile(r"^/workspace/([^/]+)/")

# Module-level sliding-window rate limiter for sensitive auth endpoints, keyed by
# "<scope>:<client IP>" so each endpoint family throttles independently.
_rate_buckets: dict[str, list[float]] = {}

# A throwaway bcrypt hash used to equalize response timing on the login path when
# the account doesn't exist (or isn't a local account). Verifying against it costs
# the same as a real wrong-password check, so the absence of an account can't be
# inferred from how fast the request fails.
_DUMMY_PASSWORD_HASH = hash_password("cove-login-timing-equalizer")


def _record_audit(db: Session, action: str, *, detail=None, user=None, ip=None) -> None:
    # Import lazily to avoid a circular import at module load time.
    from server.main import record_audit

    record_audit(db, action, detail=detail, user=user, ip=ip)


def _set_auth_cookies(resp: Response, user: User) -> None:
    settings = get_settings()
    access = create_access_token(user.id, user.is_admin)
    refresh = create_refresh_token(user.id)
    # Host-only (no Domain): the session/refresh cookies must NOT be sent to
    # workspace origins ({id}.{domain}). Workspace streams authenticate with a
    # separate, per-workspace ``cove_stream`` token instead (see forward_auth).
    resp.set_cookie(
        settings.cookie_session_name,
        access,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        max_age=settings.access_token_minutes * 60,
    )
    resp.set_cookie(
        settings.cookie_refresh_name,
        refresh,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/auth",
        max_age=settings.refresh_token_days * 86400,
    )


def _clear_auth_cookies(resp: Response) -> None:
    settings = get_settings()
    resp.delete_cookie(settings.cookie_session_name, path="/")
    resp.delete_cookie(settings.cookie_refresh_name, path="/api/auth")


def _check_rate_limit(ip: str, scope: str = "login") -> bool:
    """Return True if the request is allowed, False if the limit is exceeded.

    ``scope`` partitions the limiter per endpoint family (login, refresh, pwchange,
    oidc) so one doesn't starve another; the same per-IP limit/window applies.
    """
    settings = get_settings()
    now = time.monotonic()
    window = settings.login_rate_window_seconds
    key = f"{scope}:{ip}"
    # Bound memory: periodically evict buckets whose attempts have all aged out,
    # so a flood of distinct source IPs can't grow the map without limit.
    if len(_rate_buckets) > 512:
        for stale_key in [
            k for k, ts in _rate_buckets.items() if all(now - t >= window for t in ts)
        ]:
            del _rate_buckets[stale_key]
    bucket = [t for t in _rate_buckets.get(key, []) if now - t < window]
    if len(bucket) >= settings.login_rate_limit:
        _rate_buckets[key] = bucket
        return False
    bucket.append(now)
    _rate_buckets[key] = bucket
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
    if user and user.auth_provider == "local" and user.password_hash:
        password_ok = verify_password(body.password, user.password_hash)
    else:
        # No (local) account: still run a bcrypt verify against a dummy hash so the
        # timing matches the wrong-password path and doesn't leak account existence.
        verify_password(body.password, _DUMMY_PASSWORD_HASH)
        password_ok = False

    if not password_ok:
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
    if not _check_rate_limit(client_ip(request), "refresh"):
        raise HTTPException(status_code=429, detail="Too many requests")
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
def change_password(
    body: ChangePasswordRequest, user: CurrentUser, db: DbSession, request: Request
):
    # Throttle the bcrypt verify below: it both resists online guessing of the
    # current password (for a hijacked session) and caps the CPU-DoS amplification.
    if not _check_rate_limit(client_ip(request), "pwchange"):
        raise HTTPException(status_code=429, detail="Too many requests")
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
    if not _check_rate_limit(client_ip(request), "oidc"):
        raise HTTPException(status_code=429, detail="Too many requests")
    await oidc_module.fetch_discovery()
    signed = sign_state(oidc_module.generate_state())
    # A distinct nonce, sent to the IdP and echoed back in the id_token, binds
    # that token to this login attempt (defeats token replay/injection).
    nonce = oidc_module.generate_state()
    redirect_uri = str(request.base_url).rstrip("/") + "/api/auth/oidc/callback"
    # The state param sent to the IdP is the signed value.
    auth_url = oidc_module.build_auth_url(redirect_uri=redirect_uri, state=signed, nonce=nonce)
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
    resp.set_cookie(
        "oidc_nonce",
        nonce,
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
    if not _check_rate_limit(client_ip(request), "oidc"):
        raise HTTPException(status_code=429, detail="Too many requests")

    cookie_state = request.cookies.get("oidc_state")
    if (
        not cookie_state
        or state != cookie_state
        or verify_state(cookie_state) is None
    ):
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    cookie_nonce = request.cookies.get("oidc_nonce")
    if not cookie_nonce:
        raise HTTPException(status_code=400, detail="Missing nonce")

    redirect_uri = str(request.base_url).rstrip("/") + "/api/auth/oidc/callback"
    token_response = await oidc_module.exchange_code(code=code, redirect_uri=redirect_uri)
    try:
        claims = await oidc_module.verify_id_token(token_response["id_token"], nonce=cookie_nonce)
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
    resp.delete_cookie("oidc_nonce", path="/")
    _set_auth_cookies(resp, user)
    return resp


def _public_id_from_host(host: str | None, settings) -> str | None:
    """Extract the workspace public_id from a ``{public_id}.{domain}`` host."""
    if not host:
        return None
    host = host.split(":", 1)[0]  # strip any :port
    suffix = f".{settings.workspace_domain}"
    if host.endswith(suffix):
        label = host[: -len(suffix)]
        if label and "." not in label:
            return label
    return None


def _authorize_ws(db: Session, public_id: str, user: User) -> bool:
    ws = db.scalar(select(Workspace).where(Workspace.public_id == public_id))
    if not ws:
        return False
    # Bind to the live workspace: a stopped/errored workspace's token or cookie
    # must not authenticate (a captured credential dies when the workspace does).
    if ws.status != "running":
        return False
    return ws.user_id == user.id or user.is_admin


def _resolve_stream_user(db: Session, token: str, public_id: str, *, kind: str = "stream") -> User | None:
    """Validate a stream token (``kind``) and confirm it is scoped to this workspace.

    ``kind`` is the required ``type`` claim: "stream" for the cookie token,
    "stream_bootstrap" for the one-time ``?__cove_t`` URL token.
    """
    payload = decode_token(token)
    if not payload or payload.get("type") != kind:
        return None
    if payload.get("ws") != public_id:
        return None
    sub = payload.get("sub")
    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        return None
    user = db.get(User, user_id)
    if not user or not _check_revocation(user, payload):
        return None
    return user


# One-time-use ledger for bootstrap-token jti values: {jti: expiry_monotonic}.
# Best-effort (process-local, cleared on restart) — paired with the token's short
# lifetime, a leaked ?__cove_t URL can't be replayed to mint a stream cookie.
_used_bootstrap_jti: dict[str, float] = {}


def _consume_bootstrap_jti(jti: str | None) -> bool:
    """Return True if this jti is fresh (and record it); False if already used."""
    if not jti:
        return False
    now = time.monotonic()
    if len(_used_bootstrap_jti) > 1024:
        for k in [k for k, exp in _used_bootstrap_jti.items() if exp <= now]:
            del _used_bootstrap_jti[k]
    if jti in _used_bootstrap_jti and _used_bootstrap_jti[jti] > now:
        return False
    _used_bootstrap_jti[jti] = now + get_settings().stream_bootstrap_minutes * 60
    return True


def _stream_token_from_uri(uri: str | None) -> str | None:
    if not uri:
        return None
    values = parse_qs(urlsplit(uri).query).get("__cove_t")
    return values[0] if values else None


def _clean_stream_url(request: Request, settings, uri: str | None) -> str:
    """Rebuild the absolute workspace URL with the one-time token stripped."""
    proto = request.headers.get("X-Forwarded-Proto") or (
        "https" if settings.cookie_secure else "http"
    )
    host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or ""
    parts = urlsplit(uri or "/")
    params = {k: v for k, v in parse_qs(parts.query).items() if k != "__cove_t"}
    query = urlencode({k: v[0] for k, v in params.items()})
    url = f"{proto}://{host}{parts.path or '/'}"
    return f"{url}?{query}" if query else url


def _forward_auth_subdomain(request, db, settings, public_id: str, uri: str | None):
    """Authenticate a workspace stream on its own origin (subdomain mode).

    The session cookie is host-only on the SPA and is NOT sent here, so we use a
    per-workspace ``cove_stream`` cookie. First request carries a one-time
    ``?__cove_t`` token minted by the SPA; we validate it, set the host-only
    stream cookie, and 302 to the clean URL (Traefik relays this non-2xx auth
    response — including Set-Cookie — to the browser).
    """
    ip = client_ip(request)

    cookie = request.cookies.get(settings.cookie_stream_name)
    if cookie:
        user = _resolve_stream_user(db, cookie, public_id)
        if user and _authorize_ws(db, public_id, user):
            return Response(status_code=200, headers={"X-Cove-User": user.username})

    token = _stream_token_from_uri(uri)
    if token:
        payload = decode_token(token)
        user = _resolve_stream_user(db, token, public_id, kind="stream_bootstrap")
        # Single-use: consume the jti so a leaked ?__cove_t URL can't be replayed.
        if (
            user
            and _consume_bootstrap_jti(payload.get("jti") if payload else None)
            and _authorize_ws(db, public_id, user)
        ):
            # Mint a FRESH, longer-lived cookie token — the short-lived bootstrap
            # token is never persisted as the session cookie.
            cookie_token = create_stream_token(user.id, public_id)
            resp = Response(
                status_code=302,
                headers={"Location": _clean_stream_url(request, settings, uri)},
            )
            resp.set_cookie(
                settings.cookie_stream_name,
                cookie_token,
                httponly=True,
                secure=settings.cookie_secure,
                samesite="lax",
                path="/",
                max_age=settings.stream_token_minutes * 60,
            )
            return resp

    _record_audit(db, "stream.deny", detail=public_id, ip=ip)
    return Response(status_code=401)


def _forward_auth_subpath(request, db, uri: str | None):
    """Authenticate a workspace stream served same-origin under /workspace/{id}/.

    Same origin as the SPA, so the host-only session cookie is available here.
    """
    token = request.cookies.get(get_settings().cookie_session_name)
    if not token:
        return Response(status_code=401)
    user = resolve_user_from_token(db, token)
    if not user:
        return Response(status_code=401)

    public_id = None
    if uri:
        match = _STREAM_RE.match(uri)
        if match:
            public_id = match.group(1)
    if not public_id:
        return Response(status_code=401)

    if _authorize_ws(db, public_id, user):
        return Response(status_code=200, headers={"X-Cove-User": user.username})

    _record_audit(db, "stream.deny", detail=uri, user=user, ip=client_ip(request))
    return Response(status_code=401)


@router.get("/forward")
def forward_auth(request: Request, db: DbSession):
    """Traefik ForwardAuth endpoint for workspace streams.

    Robust: any missing piece results in 401.
    """
    settings = get_settings()
    # Only Traefik's internal ForwardAuth subrequest may reach this endpoint. That
    # subrequest carries the internal authority (e.g. "cove:8080") as its Host;
    # a public request routed through Traefik arrives with the public Host, so it
    # is rejected here — closing the endpoint as an external enumeration surface.
    if settings.forward_auth_host:
        if request.headers.get("host") != settings.forward_auth_host:
            return Response(status_code=404)
    try:
        uri = request.headers.get("X-Forwarded-Uri") or request.headers.get("X-Forwarded-Path")

        # Subdomain mode: resolve the workspace from the host and authenticate
        # via the per-workspace stream cookie. A subpath X-Forwarded-Uri may
        # still arrive (mixed routing), so fall through to subpath handling when
        # the host doesn't resolve to a workspace.
        if settings.workspace_domain:
            host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host")
            public_id = _public_id_from_host(host, settings)
            if public_id:
                return _forward_auth_subdomain(request, db, settings, public_id, uri)

        return _forward_auth_subpath(request, db, uri)
    except Exception:
        return Response(status_code=401)
