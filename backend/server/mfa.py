"""Two-factor sign-in for local accounts: TOTP, recovery codes, and the
short-lived tokens that carry a sign-in between its steps.

Sign-in with a password and 2FA is two requests. The first checks the password
and, instead of a session, returns an ``mfa`` token (5 minutes, single use)
naming the account. The second presents that token with a TOTP code or a
recovery code (or a passkey) and gets the session. An account that must have
2FA but has none gets an ``mfa_setup`` token instead, which can only enrol a
first factor; enrolling with it signs the account in.

A passkey is two factors on its own (something you have, plus the key's PIN or
fingerprint, which Cove requires), so signing in with one never asks for TOTP.

SSO accounts are out of scope: their identity provider does 2FA.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
import struct
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from server import settings_store
from server.config import get_settings
from server.models import RecoveryCode, User
from server.security import decrypt_secret, encrypt_secret

TOTP_PERIOD = 30
TOTP_DIGITS = 6
# Accept the previous and next 30-second step too, for clock drift.
TOTP_WINDOW = 1
RECOVERY_CODE_COUNT = 10
# Wrong codes one sign-in may try before it has to start over with the password.
MAX_ATTEMPTS = 5
MFA_TOKEN_MINUTES = 5
SETUP_TOKEN_MINUTES = 10

ISSUER = "Cove"


# ── Policy ───────────────────────────────────────────────────────────────────


def applies_to(user: User) -> bool:
    """2FA is for local accounts; SSO accounts use their identity provider's."""
    return user.auth_provider == "local"


def required(db: Session, user: User) -> bool:
    return applies_to(user) and (user.require_2fa or settings_store.get_require_2fa_all(db))


def methods(user: User) -> list[str]:
    out = []
    if user.totp_enabled:
        out.append("totp")
    if user.passkeys:
        out.append("passkey")
    if user.totp_enabled or user.passkeys:
        out.append("recovery")
    return out


# ── TOTP (RFC 6238, SHA-1, 6 digits, 30 s: what every authenticator app does) ──


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**TOTP_DIGITS).zfill(TOTP_DIGITS)


def totp_now(secret_b32: str, at: Optional[float] = None) -> str:
    return _hotp(secret_b32, int((time.time() if at is None else at) // TOTP_PERIOD))


def match_totp(
    secret_b32: str, code: str, after_step: Optional[int] = None, at: Optional[float] = None
) -> Optional[int]:
    """The time step ``code`` is valid for, or None. Steps at or before
    ``after_step`` are refused, so a code (or an older one) can't be replayed."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != TOTP_DIGITS:
        return None
    now = int((time.time() if at is None else at) // TOTP_PERIOD)
    found = None
    for step in range(now - TOTP_WINDOW, now + TOTP_WINDOW + 1):
        # Compare every candidate (no early exit) so timing doesn't say which matched.
        if hmac.compare_digest(_hotp(secret_b32, step), code) and found is None:
            found = step
    if found is None or (after_step is not None and found <= after_step):
        return None
    return found


def provisioning_uri(secret_b32: str, username: str, issuer_host: str) -> str:
    label = quote(f"{ISSUER} ({issuer_host}):{username}", safe="")
    issuer = quote(f"{ISSUER} ({issuer_host})", safe="")
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={issuer}&period={TOTP_PERIOD}&digits={TOTP_DIGITS}"


def qr_svg_data_url(text: str) -> str:
    import segno

    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=5, border=2, dark="#000", light="#fff", xmldecl=False)
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()


def enable_totp(user: User, secret_b32: str, step: int) -> None:
    user.totp_secret = encrypt_secret(secret_b32)
    user.totp_last_step = step


def disable_totp(user: User) -> None:
    user.totp_secret = None
    user.totp_last_step = None


def check_totp(user: User, code: str) -> bool:
    """Verify a code against the account's secret, consuming its time step."""
    secret = decrypt_secret(user.totp_secret) if user.totp_secret else None
    if not secret:
        return False
    step = match_totp(secret, code, after_step=user.totp_last_step)
    if step is None:
        return False
    user.totp_last_step = step
    return True


# ── Recovery codes ───────────────────────────────────────────────────────────

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I


def _normalise(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def _hash_code(code: str) -> str:
    return hashlib.sha256(_normalise(code).encode()).hexdigest()


def new_recovery_codes(db: Session, user: User) -> list[str]:
    """Replace the account's recovery codes; returns them (shown once)."""
    user.recovery_codes.clear()  # delete-orphan removes the rows
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(16))  # 80 bits
        codes.append("-".join(raw[i:i + 4] for i in range(0, 16, 4)))
        user.recovery_codes.append(RecoveryCode(code_hash=_hash_code(raw)))
    return codes


def recovery_codes_left(user: User) -> int:
    return sum(1 for rc in user.recovery_codes if rc.used_at is None)


def use_recovery_code(db: Session, user: User, code: str) -> bool:
    if len(_normalise(code)) != 16:
        return False
    wanted = _hash_code(code)
    for rc in user.recovery_codes:
        if rc.used_at is None and hmac.compare_digest(rc.code_hash, wanted):
            rc.used_at = datetime.now(timezone.utc)
            return True
    return False


def looks_like_recovery_code(code: str) -> bool:
    return len(_normalise(code)) == 16


def reset(db: Session, user: User) -> None:
    """Remove every second factor (admin reset, or a user's last one going)."""
    disable_totp(user)
    user.recovery_codes.clear()
    user.passkeys.clear()


# ── One account at a time ────────────────────────────────────────────────────

_user_locks: dict[int, threading.Lock] = {}
_user_locks_guard = threading.Lock()


@contextmanager
def user_lock(user_id: int):
    """Serialise second-factor checks and changes for one account.

    Endpoints run in a thread pool, so without this two requests could both see
    a recovery code (or a TOTP step) as unused, or a burst of wrong codes could
    all pass the attempt limits before any failure is counted. Cove runs one
    uvicorn process, so an in-process lock covers every request (as with
    workspace limits). Callers re-read the account (``db.expire``) once they
    hold it, to see what the request before them committed.
    """
    with _user_locks_guard:
        lock = _user_locks.setdefault(int(user_id), threading.Lock())
    with lock:
        yield


# ── Tokens between sign-in steps ─────────────────────────────────────────────

_used_jti: dict[str, float] = {}
_attempts: dict[str, tuple[int, float]] = {}  # jti -> (wrong codes, expiry)
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def issue(kind: str, user: User, minutes: int, **extra) -> str:
    s = get_settings()
    payload = {
        "type": kind, "sub": str(user.id), "jti": uuid.uuid4().hex,
        "iat": _now().timestamp(), "exp": _now() + timedelta(minutes=minutes), **extra,
    }
    return jwt.encode(payload, s.get_secret_key(), algorithm=s.jwt_algorithm)


def _decode(token: Optional[str], kind: str) -> Optional[dict]:
    if not token:
        return None
    s = get_settings()
    try:
        payload = jwt.decode(token, s.get_secret_key(), algorithms=[s.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != kind or not payload.get("jti"):
        return None
    return payload


def read(token: Optional[str], kind: str) -> Optional[dict]:
    """The claims of a live, unused token of ``kind``, or None."""
    payload = _decode(token, kind)
    if payload is None or used(payload):
        return None
    return payload


def take(token: Optional[str], kind: str) -> Optional[dict]:
    """``read`` and ``consume`` in one step, for single-use tokens (WebAuthn
    challenges): of two racing requests only the first gets the claims."""
    payload = _decode(token, kind)
    if payload is None:
        return None
    with _lock:
        if payload["jti"] in _used_jti:
            return None
        _mark_used(payload)
    return payload


def used(payload: dict) -> bool:
    with _lock:
        return payload["jti"] in _used_jti


def user_for(db: Session, payload: dict) -> Optional[User]:
    try:
        user = db.get(User, int(payload["sub"]))
    except (KeyError, TypeError, ValueError):
        return None
    # A password change, sign-out-everywhere or 2FA reset after the first step
    # voids the pending sign-in too.
    if user is not None and user.tokens_valid_from is not None:
        if float(payload.get("iat", 0)) < user.tokens_valid_from.replace(tzinfo=timezone.utc).timestamp():
            return None
    return user


def _prune(now: float) -> None:
    # Caller holds _lock. Expired tokens fail to decode anyway, so their
    # entries can go.
    if len(_used_jti) + len(_attempts) > 4096:
        for k in [k for k, exp in _used_jti.items() if exp < now]:
            _used_jti.pop(k, None)
        for k in [k for k, (_, exp) in _attempts.items() if exp < now]:
            _attempts.pop(k, None)


def _mark_used(payload: dict) -> None:
    # Caller holds _lock.
    now = time.time()
    _prune(now)
    _used_jti[payload["jti"]] = float(payload.get("exp", now + 3600))
    _attempts.pop(payload["jti"], None)


def consume(payload: dict) -> None:
    with _lock:
        _mark_used(payload)


def note_failure(payload: dict) -> bool:
    """Count a wrong code; True when the token has no tries left (and is now dead)."""
    with _lock:
        now = time.time()
        _prune(now)
        n = _attempts.get(payload["jti"], (0, 0.0))[0] + 1
        _attempts[payload["jti"]] = (n, float(payload.get("exp", now + 3600)))
        if n >= MAX_ATTEMPTS:
            _mark_used(payload)
            return True
    return False


def find_user(db: Session, username: str) -> Optional[User]:
    return db.scalar(select(User).where(User.username == username))
