"""Offline, deterministic unit tests for server.oidc.verify_id_token.

We craft an RSA keypair, publish its public JWK via a monkeypatched
fetch_jwks/fetch_discovery, and assert that good tokens verify while
bad-signature / aud-mismatch / issuer-mismatch tokens are rejected.
"""

import asyncio

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt
from jose.exceptions import JWTError

from server import oidc as oidc_module

_ISSUER = "https://idp.example.com"
_AUD = "client-x"
_KID = "test-kid"


def _make_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    pub_jwk = jwk.construct(pub_pem, algorithm="RS256").to_dict()
    pub_jwk["kid"] = _KID
    return priv_pem, pub_jwk


@pytest.fixture
def oidc_setup(monkeypatch):
    priv_pem, pub_jwk = _make_keypair()

    async def fake_discovery():
        return {"issuer": _ISSUER, "id_token_signing_alg_values_supported": ["RS256"]}

    async def fake_jwks():
        return {"keys": [pub_jwk]}

    monkeypatch.setattr(oidc_module, "fetch_discovery", fake_discovery)
    monkeypatch.setattr(oidc_module, "fetch_jwks", fake_jwks)
    # verify_id_token reads settings.oidc_client_id for the audience check.
    from server.config import get_settings

    monkeypatch.setattr(get_settings(), "oidc_client_id", _AUD)
    return priv_pem


def _encode(priv_pem, claims, kid=_KID):
    return jwt.encode(claims, priv_pem, algorithm="RS256", headers={"kid": kid})


def test_valid_token_verifies(oidc_setup):
    token = _encode(oidc_setup, {"sub": "abc", "aud": _AUD, "iss": _ISSUER})
    claims = asyncio.run(oidc_module.verify_id_token(token))
    assert claims["sub"] == "abc"


def test_bad_signature_rejected(oidc_setup):
    # Sign with a DIFFERENT key -> signature won't match the published JWK.
    other_priv, _ = _make_keypair()
    token = _encode(other_priv, {"sub": "abc", "aud": _AUD, "iss": _ISSUER})
    with pytest.raises(JWTError):
        asyncio.run(oidc_module.verify_id_token(token))


def test_aud_mismatch_rejected(oidc_setup):
    token = _encode(oidc_setup, {"sub": "abc", "aud": "wrong-aud", "iss": _ISSUER})
    with pytest.raises(JWTError):
        asyncio.run(oidc_module.verify_id_token(token))


def test_issuer_mismatch_rejected(oidc_setup):
    token = _encode(oidc_setup, {"sub": "abc", "aud": _AUD, "iss": "https://evil.example.com"})
    with pytest.raises(JWTError):
        asyncio.run(oidc_module.verify_id_token(token))


def test_unknown_kid_rejected(oidc_setup):
    token = _encode(oidc_setup, {"sub": "abc", "aud": _AUD, "iss": _ISSUER}, kid="other-kid")
    with pytest.raises(JWTError):
        asyncio.run(oidc_module.verify_id_token(token))


def test_matching_nonce_accepted(oidc_setup):
    token = _encode(oidc_setup, {"sub": "abc", "aud": _AUD, "iss": _ISSUER, "nonce": "n-123"})
    claims = asyncio.run(oidc_module.verify_id_token(token, nonce="n-123"))
    assert claims["sub"] == "abc"


def test_nonce_mismatch_rejected(oidc_setup):
    token = _encode(oidc_setup, {"sub": "abc", "aud": _AUD, "iss": _ISSUER, "nonce": "n-123"})
    with pytest.raises(JWTError):
        asyncio.run(oidc_module.verify_id_token(token, nonce="different"))


def test_missing_nonce_claim_rejected_when_expected(oidc_setup):
    # We sent a nonce but the token carries none -> reject.
    token = _encode(oidc_setup, {"sub": "abc", "aud": _AUD, "iss": _ISSUER})
    with pytest.raises(JWTError):
        asyncio.run(oidc_module.verify_id_token(token, nonce="n-123"))
