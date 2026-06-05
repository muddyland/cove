import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt

from server.config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(user_id: int, is_admin: bool) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_minutes)
    payload = {
        "sub": str(user_id),
        "adm": bool(is_admin),
        "type": "access",
        "iat": now,
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
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(payload, settings.get_secret_key(), algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> Optional[dict]:
    """Verify signature + expiry; return claims or None."""
    settings = get_settings()
    try:
        return jwt.decode(token, settings.get_secret_key(), algorithms=[settings.jwt_algorithm])
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
