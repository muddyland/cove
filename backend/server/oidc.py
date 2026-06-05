import re
import secrets
from typing import Optional
from urllib.parse import urlencode

import httpx
from jose import jwt
from jose.exceptions import JWTError

from server.config import get_settings

# Reuse the canonical username charset for sanitizing OIDC-derived usernames.
_USERNAME_CHARSET_RE = re.compile(r"[^a-zA-Z0-9._-]")

_discovery: Optional[dict] = None
_jwks: Optional[dict] = None


async def fetch_discovery() -> dict:
    global _discovery
    if _discovery:
        return _discovery
    settings = get_settings()
    url = settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=10)
        resp.raise_for_status()
        _discovery = resp.json()
    return _discovery


async def fetch_jwks() -> dict:
    global _jwks
    if _jwks:
        return _jwks
    discovery = await fetch_discovery()
    async with httpx.AsyncClient() as client:
        resp = await client.get(discovery["jwks_uri"], timeout=10)
        resp.raise_for_status()
        _jwks = resp.json()
    return _jwks


def build_auth_url(redirect_uri: str, state: str) -> str:
    settings = get_settings()
    # Discovery is cached; we build synchronously from cached data if available
    # For the redirect, we need the authorization endpoint
    if not _discovery:
        raise RuntimeError("OIDC discovery not loaded — call fetch_discovery() at startup")
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect_uri,
        "scope": settings.oidc_scopes,
        "state": state,
    }
    return _discovery["authorization_endpoint"] + "?" + urlencode(params)


async def exchange_code(code: str, redirect_uri: str) -> dict:
    settings = get_settings()
    discovery = await fetch_discovery()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            discovery["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": settings.oidc_client_id,
                "client_secret": settings.oidc_client_secret,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()


def _select_signing_key(jwks: dict, kid: Optional[str]) -> dict:
    """Return the JWK matching `kid`, or the sole key if only one is present."""
    keys = jwks.get("keys", [])
    if not keys:
        raise JWTError("JWKS contains no keys")
    if kid:
        for key in keys:
            if key.get("kid") == kid:
                return key
        raise JWTError(f"No JWKS key matches token kid={kid!r}")
    if len(keys) == 1:
        return keys[0]
    raise JWTError("Token has no kid and JWKS has multiple keys")


async def verify_id_token(id_token: str) -> dict:
    """Verify an id_token's signature and standard claims; return the claims.

    Fetches JWKS + discovery, selects the signing key by the token header `kid`,
    and verifies signature, audience (oidc_client_id) and issuer. Raises
    jose.JWTError (or related) on any verification failure.
    """
    settings = get_settings()
    discovery = await fetch_discovery()
    jwks = await fetch_jwks()

    header = jwt.get_unverified_header(id_token)
    key = _select_signing_key(jwks, header.get("kid"))

    algorithms = discovery.get("id_token_signing_alg_values_supported") or ["RS256"]

    return jwt.decode(
        id_token,
        key=key,
        algorithms=algorithms,
        audience=settings.oidc_client_id,
        issuer=discovery.get("issuer"),
        options={"verify_aud": True},
    )


def generate_state() -> str:
    return secrets.token_urlsafe(32)


def extract_username(claims: dict) -> str:
    """Derive a username from OIDC claims, sanitized to the allowed charset.

    Disallowed characters are replaced with '-' and the result is trimmed to
    64 chars. Falls back to a safe value ("user") if nothing usable remains.
    """
    raw = (
        claims.get("preferred_username")
        or claims.get("email", "").split("@")[0]
        or claims.get("sub", "")[:32]
        or ""
    )
    return sanitize_username(raw)


def sanitize_username(raw: str) -> str:
    """Coerce an arbitrary string into a valid Cove username."""
    cleaned = _USERNAME_CHARSET_RE.sub("-", raw or "").strip("-")[:64]
    if not cleaned or cleaned in (".", ".."):
        return "user"
    return cleaned


def is_admin_from_claims(claims: dict) -> bool:
    settings = get_settings()
    if not settings.oidc_admin_group:
        return False
    groups = claims.get("groups", [])
    return settings.oidc_admin_group in groups
