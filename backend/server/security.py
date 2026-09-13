import base64
import hashlib
import hmac
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt

from server.config import get_settings

# Allowed username charset: alphanumerics plus dot, underscore, hyphen, 1–64 chars.
# "." and ".." are rejected outright (path-like / reserved) even though they
# otherwise match the charset.
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")


# Usernames double as per-user storage directory names under the storage root,
# which also holds Cove's own reserved staging directories (``.cove-scripts``,
# ``.cove-ssh``, ``.cove-gluetun`` — root-executed init scripts and decrypted
# secrets). No username may start with a dot, an underscore, or ``cove-``, so an
# account can never be a file browser onto those.
_RESERVED_USERNAME_PREFIXES = (".", "_", "cove-")


def is_reserved_username(username: str) -> bool:
    return username.lower().startswith(_RESERVED_USERNAME_PREFIXES)


def is_valid_username(username: str) -> bool:
    """Return True if `username` is a syntactically valid Cove username."""
    if not isinstance(username, str):
        return False
    if username in (".", ".."):
        return False
    if is_reserved_username(username):
        return False
    return bool(_USERNAME_RE.match(username))


def validate_username(username: str) -> str:
    """Validate a username, raising HTTP 400 on failure. Returns it on success."""
    from fastapi import HTTPException

    if not is_valid_username(username):
        raise HTTPException(
            status_code=400,
            detail="Invalid username: must be 1-64 chars of [a-zA-Z0-9._-], not '.' or '..', "
            "and not start with '.', '_' or 'cove-'",
        )
    return username


# ── Secret encryption at rest (Fernet, keyed off the app secret) ───────────────

# Marks a value as encrypted by encrypt_secret so legacy plaintext is detectable
# (and the migration is idempotent).
_SECRET_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    """A Fernet built from a stable 32-byte key derived from the app secret."""
    digest = hashlib.sha256(get_settings().get_secret_key().encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a sensitive value (e.g. a Tailscale pre-auth key) for storage."""
    token = _fernet().encrypt(plaintext.encode()).decode()
    return _SECRET_PREFIX + token


def decrypt_secret(value: Optional[str]) -> Optional[str]:
    """Inverse of encrypt_secret.

    Returns the value unchanged if it isn't a recognized encrypted token (legacy
    plaintext written before encryption existed), and None if it is an encrypted
    token that fails to decrypt (e.g. the secret key changed).
    """
    if not value or not value.startswith(_SECRET_PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(_SECRET_PREFIX):].encode()).decode()
    except InvalidToken:
        return None


# bcrypt ignores everything past 72 bytes; bcrypt>=4.1 raises instead of
# truncating silently. Refuse such passwords up front (a 400, not a 500).
MAX_PASSWORD_BYTES = 72


def validate_password(password: str) -> str:
    """Enforce the length envelope every password must satisfy; raises HTTP 400."""
    from fastapi import HTTPException

    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        raise HTTPException(status_code=400, detail="Password must be at most 72 bytes")
    return password


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:MAX_PASSWORD_BYTES], bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode()[:MAX_PASSWORD_BYTES], hashed.encode())
    except ValueError:
        return False


def create_access_token(user_id: int, is_admin: bool) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_minutes)
    payload = {
        "sub": str(user_id),
        "adm": bool(is_admin),
        "type": "access",
        "iat": now.timestamp(),
        "exp": expire,
    }
    return jwt.encode(payload, settings.get_secret_key(), algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: int) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=settings.refresh_token_days)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "iat": now.timestamp(),
        "exp": expire,
    }
    return jwt.encode(payload, settings.get_secret_key(), algorithm=settings.jwt_algorithm)


def create_stream_token(user_id: int, public_id: str) -> str:
    """Mint a per-workspace stream token (subdomain mode).

    Scoped to a single workspace (``ws``) and the requesting user (``sub``) so a
    hostile workspace origin that captures it gains nothing beyond access to the
    very workspace the user already owns. Carries ``iat`` so it is revoked by the
    same ``tokens_valid_from`` mechanism as access tokens (logout/password change).
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.stream_token_minutes)
    payload = {
        "sub": str(user_id),
        "ws": public_id,
        "type": "stream",
        "iat": now.timestamp(),
        "exp": expire,
    }
    return jwt.encode(
        payload, settings.get_stream_signing_key(), algorithm=settings.jwt_algorithm
    )


def create_stream_bootstrap_token(user_id: int, public_id: str) -> str:
    """Mint a one-time bootstrap token for the ``?__cove_t`` URL (subdomain mode).

    Short-lived and carries a unique ``jti`` so the ForwardAuth endpoint can
    consume it exactly once before swapping it for a fresh stream *cookie* token.
    Distinct ``type`` so it can never be presented as a cookie session token.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.stream_bootstrap_minutes)
    payload = {
        "sub": str(user_id),
        "ws": public_id,
        "type": "stream_bootstrap",
        "jti": uuid.uuid4().hex,
        "iat": now.timestamp(),
        "exp": expire,
    }
    return jwt.encode(
        payload, settings.get_stream_signing_key(), algorithm=settings.jwt_algorithm
    )


# ``iat`` is minted as a float (fractional seconds) so a token created right
# after a revocation in the same wall-clock second sorts after it; 1.0.x tokens
# carry an integer iat and compare exactly as before.


def decode_token(token: str) -> Optional[dict]:
    """Verify signature + expiry; return claims or None."""
    settings = get_settings()
    try:
        return jwt.decode(token, settings.get_secret_key(), algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


def decode_stream_token(token: str) -> Optional[dict]:
    """Verify a stream/stream_bootstrap token against the dedicated stream-signing
    key; return claims or None. Used by both the control plane and the zone agent
    (which holds only this key, never the app secret)."""
    settings = get_settings()
    try:
        return jwt.decode(
            token, settings.get_stream_signing_key(), algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        return None


# Canonical name is decode_token; keep alias for convenience/back-compat.
decode_access_token = decode_token


def sign_state(nonce: str) -> str:
    """Return 'nonce.hexsig' where hexsig is HMAC-SHA256 of nonce using the secret key."""
    settings = get_settings()
    sig = hmac.new(
        settings.get_secret_key().encode(), nonce.encode(), hashlib.sha256
    ).hexdigest()
    return f"{nonce}.{sig}"


def verify_state(value: str) -> Optional[str]:
    """Verify a signed state value; return the nonce if valid, else None."""
    if not value or "." not in value:
        return None
    nonce, _, sig = value.rpartition(".")
    if not nonce or not sig:
        return None
    settings = get_settings()
    expected = hmac.new(
        settings.get_secret_key().encode(), nonce.encode(), hashlib.sha256
    ).hexdigest()
    if hmac.compare_digest(expected, sig):
        return nonce
    return None
