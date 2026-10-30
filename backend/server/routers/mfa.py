"""Two-factor sign-in and its management (server.mfa, server.passkeys).

Sign-in:
  POST /api/auth/login              password; may answer {mfa_required} or
                                    {mfa_setup_required} with an mfa_token
  POST /api/auth/login/2fa          mfa_token + TOTP or recovery code -> session
  POST /api/auth/login/passkey/options  username -> WebAuthn request options
  POST /api/auth/login/passkey      assertion -> session (no password, no TOTP)

Your own account (or, with an mfa_setup token, the account signing in):
  GET  /api/auth/2fa                what's set up
  POST /api/auth/2fa/totp/setup     new secret + QR (not saved yet)
  POST /api/auth/2fa/totp/enable    confirm a code -> saved (+ recovery codes)
  POST /api/auth/2fa/totp/disable   password
  POST /api/auth/2fa/recovery-codes password -> a new set
  POST /api/auth/2fa/passkeys/options
  POST /api/auth/2fa/passkeys       register (+ recovery codes if first)
  POST /api/auth/2fa/passkeys/{id}/delete  password
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from server import mfa, passkeys
from server.config import get_settings
from server.deps import CurrentUser, DbSession, resolve_user_from_token
from server.models import User, WebAuthnCredential
from server.net import client_ip
from server.schemas import (
    MfaCodeIn,
    MfaEnrolIn,
    PasskeyLoginIn,
    PasskeyOptionsIn,
    PasskeyRegisterIn,
    PasswordConfirmIn,
    TokenResponse,
    TotpEnableIn,
)
from server.security import decrypt_secret, encrypt_secret, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Wrong second factors per account, across sign-ins and source addresses: past
# this many in the window, the account's second step is refused for a while.
# Each sign-in already allows only mfa.MAX_ATTEMPTS, and each needs the password.
_ACCOUNT_FAILURES = 10
_ACCOUNT_WINDOW = 15 * 60
_failures: dict[int, list[float]] = {}
_failures_lock = threading.Lock()


def _auth():
    from server.routers import auth

    return auth


def _account_locked(user_id: int) -> bool:
    now = time.monotonic()
    with _failures_lock:
        recent = [t for t in _failures.get(user_id, []) if now - t < _ACCOUNT_WINDOW]
        _failures[user_id] = recent
        return len(recent) >= _ACCOUNT_FAILURES


def _account_failed(user_id: int) -> None:
    with _failures_lock:
        _failures.setdefault(user_id, []).append(time.monotonic())


_NO_CTX = object()


def _sign_in(db, request: Request, user: User, how: str, extra: Optional[dict] = None, ctx=_NO_CTX) -> JSONResponse:
    auth = _auth()
    auth._require_lan(db, user, request)
    if ctx is _NO_CTX:
        # Before recording anything: a revoked kiosk binding refuses here.
        ctx = auth._device_ctx(request, db, user)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    auth._record_audit(db, "login.success", detail=how, user=user, ip=client_ip(request))
    tokens = auth._issue_tokens(user, ctx)
    resp = JSONResponse(content={**TokenResponse(access_token=tokens[0]).model_dump(), **(extra or {})})
    auth._set_auth_cookies(resp, user, tokens=tokens)
    return resp


def first_step_response(db, request: Request, user: User) -> Optional[JSONResponse]:
    """What /login answers instead of a session when 2FA applies, else None."""
    if not mfa.applies_to(user):
        return None
    if user.has_2fa:
        return JSONResponse({
            "mfa_required": True,
            "mfa_token": mfa.issue("mfa", user, mfa.MFA_TOKEN_MINUTES),
            "methods": mfa.methods(user),
        })
    if mfa.required(db, user):
        return JSONResponse({
            "mfa_setup_required": True,
            "mfa_token": mfa.issue("mfa_setup", user, mfa.SETUP_TOKEN_MINUTES),
        })
    return None


def _session_user(request: Request, db) -> Optional[User]:
    authz = request.headers.get("authorization") or ""
    token = authz[7:].strip() if authz.lower().startswith("bearer ") else None
    token = token or request.cookies.get(get_settings().cookie_session_name)
    return resolve_user_from_token(db, token) if token else None


def _enrolling(request: Request, db, mfa_token: Optional[str]) -> tuple[User, Optional[dict]]:
    """The account being set up: the signed-in one, or the one finishing a
    required 2FA setup during sign-in (then also its token's claims)."""
    if mfa_token:
        if get_settings().oidc_only_active:
            raise HTTPException(status_code=403, detail="Local login is disabled (OIDC only)")
        claims = mfa.read(mfa_token, "mfa_setup")
        user = mfa.user_for(db, claims) if claims else None
        # A setup sign-in is for an account with no second factor yet. Once it
        # has one, a password alone mustn't add another (an attacker who knows
        # the password and signed in first could otherwise add their own key).
        if user is None or user.has_2fa:
            raise HTTPException(status_code=401, detail="This sign-in expired. Sign in again.")
    else:
        claims = None
        user = _session_user(request, db)
        if user is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
    _auth()._require_lan(db, user, request)
    if not mfa.applies_to(user):
        raise HTTPException(
            status_code=400, detail="Two-factor sign-in for SSO accounts is set up at your identity provider."
        )
    return user, claims


def _confirm_password(request: Request, user: User, password: str) -> None:
    if not _auth()._check_rate_limit(client_ip(request), "pwchange"):
        raise HTTPException(status_code=429, detail="Too many requests")
    if not mfa.applies_to(user) or not verify_password(password or "", user.password_hash or ""):
        # 403, not 401: the session is fine, and the SPA treats a 401 as an
        # expired session (refresh, then sign out).
        raise HTTPException(status_code=403, detail="Password is incorrect")


@contextmanager
def _enrolment(db, user: User, claims: Optional[dict]):
    """Hold the account's lock while a factor is added, and yield whether it's
    the first one. Re-checks the setup sign-in under the lock, so two racing
    requests can't both use it."""
    with mfa.user_lock(user.id):
        db.expire(user)  # see what a request before us committed
        if claims is not None and (mfa.used(claims) or mfa.user_for(db, claims) is None or user.has_2fa):
            raise HTTPException(status_code=401, detail="This sign-in expired. Sign in again.")
        yield not user.has_2fa


def _finish_enrolment(
    db, request: Request, user: User, claims: Optional[dict], how: str, body: dict, first: bool
) -> JSONResponse:
    auth = _auth()
    # Kiosk checks first, so a refusal doesn't leave a factor saved whose
    # recovery codes nobody saw.
    if claims is not None:
        ctx = auth._device_ctx(request, db, user)
    elif first:
        ctx = auth._device_ctx(request, db, user, auth._token_payload(request))
    if first:
        # Every session and pending sign-in so far came from the password
        # alone: end them. This browser gets a fresh session below.
        user.tokens_valid_from = datetime.now(timezone.utc)
    if not user.recovery_codes or mfa.recovery_codes_left(user) == 0:
        body["recovery_codes"] = mfa.new_recovery_codes(db, user)
    db.commit()
    auth._record_audit(db, f"2fa.{how}.add", detail=user.username, user=user, ip=client_ip(request))
    if claims is not None:
        mfa.consume(claims)
        return _sign_in(db, request, user, f"{how} (2FA set up at sign-in)", extra=body, ctx=ctx)
    if first:
        tokens = auth._issue_tokens(user, ctx)
        resp = JSONResponse(content={**body, "access_token": tokens[0]})
        auth._set_auth_cookies(resp, user, tokens=tokens)
        return resp
    return JSONResponse(body)


# ── Sign-in, second step ─────────────────────────────────────────────────────


@router.post("/login/2fa")
def login_second_factor(body: MfaCodeIn, request: Request, db: DbSession):
    auth = _auth()
    ip = client_ip(request)
    if not auth._check_rate_limit(ip, "mfa"):
        raise HTTPException(status_code=429, detail="Too many attempts")
    if get_settings().oidc_only_active:
        raise HTTPException(status_code=403, detail="Local login is disabled (OIDC only)")
    claims = mfa.read(body.mfa_token, "mfa")
    user = mfa.user_for(db, claims) if claims else None
    if user is None:
        raise HTTPException(status_code=401, detail="This sign-in expired. Sign in again.")
    # One code check per account at a time: a recovery code or TOTP step can't
    # be used by two racing requests, and a burst can't outrun the limits.
    with mfa.user_lock(user.id):
        db.expire(user)
        if mfa.used(claims) or mfa.user_for(db, claims) is None:
            raise HTTPException(status_code=401, detail="This sign-in expired. Sign in again.")
        if _account_locked(user.id):
            raise HTTPException(status_code=429, detail="Too many wrong codes for this account. Try again later.")
        recovery = mfa.looks_like_recovery_code(body.code)
        ok = mfa.use_recovery_code(db, user, body.code) if recovery else mfa.check_totp(user, body.code)
        if not ok:
            db.rollback()
            _account_failed(user.id)
            dead = mfa.note_failure(claims)
            auth._record_audit(db, "login.2fa_fail", detail=user.username, user=user, ip=ip)
            raise HTTPException(
                status_code=401,
                detail="Too many wrong codes. Sign in again." if dead else "That code didn't work.",
            )
        mfa.consume(claims)
        extra = {"recovery_codes_left": mfa.recovery_codes_left(user)} if recovery else None
        return _sign_in(db, request, user, "recovery code" if recovery else "authenticator app", extra=extra)


@router.post("/login/passkey/options")
def passkey_login_options(body: PasskeyOptionsIn, request: Request, db: DbSession):
    if get_settings().oidc_only_active:
        raise HTTPException(status_code=403, detail="Local login is disabled (OIDC only)")
    if not _auth()._check_rate_limit(client_ip(request), "passkey"):
        raise HTTPException(status_code=429, detail="Too many attempts")
    return passkeys.authentication_options(db, request, body.username.strip())


@router.post("/login/passkey")
def passkey_login(body: PasskeyLoginIn, request: Request, db: DbSession):
    if get_settings().oidc_only_active:
        raise HTTPException(status_code=403, detail="Local login is disabled (OIDC only)")
    ip = client_ip(request)
    if not _auth()._check_rate_limit(ip, "passkey"):
        raise HTTPException(status_code=429, detail="Too many attempts")
    user = passkeys.authenticate(db, body.state, body.credential)
    if user is None:
        db.rollback()
        _auth()._record_audit(db, "login.fail", detail="passkey", ip=ip)
        raise HTTPException(status_code=401, detail="That security key or passkey isn't set up for this account.")
    db.commit()  # sign count, last used
    return _sign_in(db, request, user, "passkey")


# ── Managing your own 2FA ────────────────────────────────────────────────────


@router.get("/2fa")
def two_factor_status(user: CurrentUser, db: DbSession):
    return {
        "available": mfa.applies_to(user),
        "passkeys_enabled": passkeys.enabled(),
        "required": mfa.required(db, user),
        "totp": user.totp_enabled,
        "recovery_codes_left": mfa.recovery_codes_left(user),
        "passkeys": [
            {"id": p.id, "name": p.name, "created_at": p.created_at, "last_used_at": p.last_used_at,
             "transports": (p.transports or "").split(",") if p.transports else []}
            for p in user.passkeys
        ],
    }


@router.post("/2fa/totp/setup")
def totp_setup(body: MfaEnrolIn, request: Request, db: DbSession):
    user, _ = _enrolling(request, db, body.mfa_token)
    if user.totp_enabled:
        raise HTTPException(status_code=409, detail="An authenticator app is already set up. Remove it first.")
    secret = mfa.new_secret()
    uri = mfa.provisioning_uri(secret, user.username, passkeys.site_name(request))
    token = mfa.issue("totp_setup", user, mfa.SETUP_TOKEN_MINUTES, sec=encrypt_secret(secret))
    return {"secret": secret, "uri": uri, "qr": mfa.qr_svg_data_url(uri), "setup_token": token}


@router.post("/2fa/totp/enable")
def totp_enable(body: TotpEnableIn, request: Request, db: DbSession):
    user, claims = _enrolling(request, db, body.mfa_token)
    if claims is None:
        _confirm_password(request, user, body.password or "")
    with _enrolment(db, user, claims) as first:
        setup = mfa.read(body.setup_token, "totp_setup")
        if setup is None or str(setup.get("sub")) != str(user.id):
            raise HTTPException(status_code=400, detail="This setup expired. Start again.")
        if user.totp_enabled:
            raise HTTPException(status_code=409, detail="An authenticator app is already set up.")
        secret = decrypt_secret(setup.get("sec"))
        step = mfa.match_totp(secret or "", body.code)
        if step is None:
            raise HTTPException(
                status_code=400, detail="That code didn't match. Check the time on your phone and try the next code."
            )
        mfa.consume(setup)
        mfa.enable_totp(user, secret, step)
        return _finish_enrolment(db, request, user, claims, "totp", {"totp": True}, first)


def _would_strand(db, user: User, removing_totp: bool = False, removing_passkey: bool = False) -> bool:
    left_totp = user.totp_enabled and not removing_totp
    left_passkeys = len(user.passkeys) - (1 if removing_passkey else 0)
    return mfa.required(db, user) and not left_totp and left_passkeys <= 0


@router.post("/2fa/totp/disable")
def totp_disable(body: PasswordConfirmIn, request: Request, user: CurrentUser, db: DbSession):
    _confirm_password(request, user, body.password)
    if not user.totp_enabled:
        raise HTTPException(status_code=404, detail="No authenticator app is set up.")
    if _would_strand(db, user, removing_totp=True):
        raise HTTPException(status_code=400, detail="Your account requires two-factor sign-in. Add a passkey first.")
    mfa.disable_totp(user)
    if not user.passkeys:
        mfa.reset(db, user)  # no second factor left: the recovery codes go too
    db.commit()
    _auth()._record_audit(db, "2fa.totp.remove", detail=user.username, user=user, ip=client_ip(request))
    return {"ok": True}


@router.post("/2fa/recovery-codes")
def regenerate_recovery_codes(body: PasswordConfirmIn, request: Request, user: CurrentUser, db: DbSession):
    _confirm_password(request, user, body.password)
    if not user.has_2fa:
        raise HTTPException(status_code=400, detail="Set up an authenticator app or a passkey first.")
    codes = mfa.new_recovery_codes(db, user)
    db.commit()
    _auth()._record_audit(db, "2fa.recovery_codes.new", detail=user.username, user=user, ip=client_ip(request))
    return {"recovery_codes": codes}


@router.post("/2fa/passkeys/options")
def passkey_register_options(body: MfaEnrolIn, request: Request, db: DbSession):
    user, _ = _enrolling(request, db, body.mfa_token)
    return passkeys.registration_options(request, user)


@router.post("/2fa/passkeys")
def passkey_register(body: PasskeyRegisterIn, request: Request, db: DbSession):
    user, claims = _enrolling(request, db, body.mfa_token)
    if claims is None:
        _confirm_password(request, user, body.password or "")
    with _enrolment(db, user, claims) as first:
        row = passkeys.register(db, request, user, body.state, body.credential, body.name or passkeys.new_name())
        db.flush()
        return _finish_enrolment(
            db, request, user, claims, "passkey", {"passkey": {"id": row.id, "name": row.name}}, first
        )


@router.post("/2fa/passkeys/{passkey_id}/delete")
def passkey_delete(passkey_id: int, body: PasswordConfirmIn, request: Request, user: CurrentUser, db: DbSession):
    _confirm_password(request, user, body.password)
    row = db.get(WebAuthnCredential, passkey_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Passkey not found")
    if _would_strand(db, user, removing_passkey=True):
        raise HTTPException(
            status_code=400,
            detail="Your account requires two-factor sign-in. Add another passkey or an authenticator app first.",
        )
    name = row.name
    user.passkeys.remove(row)
    if not user.has_2fa:
        mfa.reset(db, user)
    db.commit()
    _auth()._record_audit(db, "2fa.passkey.remove", detail=f"{user.username}: {name}", user=user, ip=client_ip(request))
    return {"ok": True}
