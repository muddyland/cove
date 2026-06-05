import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx

from server.config import get_settings

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


def decode_id_token_claims(id_token: str) -> dict:
    """Decode without full signature verification for claim extraction.
    We trust the token because it came directly from the IdP token endpoint over TLS.
    """
    import base64
    import json

    parts = id_token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid id_token format")
    # Add padding
    payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


def generate_state() -> str:
    return secrets.token_urlsafe(32)


def extract_username(claims: dict) -> str:
    return (
        claims.get("preferred_username")
        or claims.get("email", "").split("@")[0]
        or claims.get("sub", "")[:32]
    )


def is_admin_from_claims(claims: dict) -> bool:
    settings = get_settings()
    if not settings.oidc_admin_group:
        return False
    groups = claims.get("groups", [])
    return settings.oidc_admin_group in groups
