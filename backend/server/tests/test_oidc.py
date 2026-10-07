"""Offline, deterministic unit tests for server.oidc.verify_id_token.

We craft an RSA keypair, publish its public JWK via a monkeypatched
fetch_jwks/fetch_discovery, and assert that good tokens verify while
bad-signature / aud-mismatch / issuer-mismatch tokens are rejected.
"""

import asyncio

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWTError as JWTError
from jwt.algorithms import RSAAlgorithm

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
    pub_jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    pub_jwk["alg"] = "RS256"
    pub_jwk["kid"] = _KID
    return priv_pem, pub_jwk


@pytest.fixture
def oidc_setup(monkeypatch):
    priv_pem, pub_jwk = _make_keypair()

    async def fake_discovery(force=False):
        return {"issuer": _ISSUER, "id_token_signing_alg_values_supported": ["RS256"]}

    # `force` mirrors the real signature: verify_id_token refetches the JWKS once
    # on an unknown kid so provider key rotation doesn't require a restart.
    async def fake_jwks(force=False):
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


def test_id_token_algs_pinned_even_if_discovery_advertises_hs256(monkeypatch):
    """Discovery advertising HS256/none must NOT widen our accepted algorithms —
    otherwise an attacker could forge an HS256 token using the public JWKS key as
    the HMAC secret (RS/HS confusion)."""
    priv_pem, pub_jwk = _make_keypair()

    async def fake_discovery():
        return {
            "issuer": _ISSUER,
            "id_token_signing_alg_values_supported": ["RS256", "HS256", "none"],
        }

    async def fake_jwks():
        return {"keys": [pub_jwk]}

    monkeypatch.setattr(oidc_module, "fetch_discovery", fake_discovery)
    monkeypatch.setattr(oidc_module, "fetch_jwks", fake_jwks)
    from server.config import get_settings

    monkeypatch.setattr(get_settings(), "oidc_client_id", _AUD)

    captured = {}
    real_decode = oidc_module.jwt.decode

    def spy_decode(token, key, algorithms, **kw):
        captured["algorithms"] = algorithms
        return real_decode(token, key=key, algorithms=algorithms, **kw)

    monkeypatch.setattr(oidc_module.jwt, "decode", spy_decode)

    token = _encode(priv_pem, {"sub": "abc", "aud": _AUD, "iss": _ISSUER})
    asyncio.run(oidc_module.verify_id_token(token))
    # HS256/none are filtered out; only the asymmetric alg survives.
    assert captured["algorithms"] == ["RS256"]


# --- Provider-agnostic behaviour -------------------------------------------
#
# The cases below pin the differences that actually break SSO when swapping
# IdPs: PKCE, token-endpoint client auth, and the three shapes providers use
# for group membership and usernames.


@pytest.fixture
def oidc_settings(monkeypatch):
    """Hand back the live Settings object with OIDC defaults restored."""
    from server.config import get_settings

    s = get_settings()
    for field, value in (
        ("oidc_issuer", _ISSUER),
        ("oidc_client_id", _AUD),
        ("oidc_client_secret", "secret"),
        ("oidc_scopes", "openid email profile"),
        ("oidc_groups_scope", "groups"),
        ("oidc_groups_claim", "groups"),
        ("oidc_admin_group", None),
        ("oidc_username_claims", "preferred_username,email,sub"),
        ("oidc_username_strip_domain", True),
        ("oidc_pkce", "auto"),
        ("oidc_token_auth_method", "auto"),
    ):
        monkeypatch.setattr(s, field, value)
    return s


def test_pkce_challenge_is_unpadded_base64url_sha256(oidc_settings):
    import base64
    import hashlib

    verifier = oidc_module.generate_pkce_verifier()
    assert 43 <= len(verifier) <= 128
    challenge = oidc_module.pkce_challenge(verifier)
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert challenge == expected.decode()
    assert "=" not in challenge


def test_pkce_auto_follows_discovery(oidc_settings):
    assert oidc_module.pkce_enabled({"code_challenge_methods_supported": ["S256"]}) is True
    assert oidc_module.pkce_enabled({}) is False


def test_pkce_can_be_forced_for_providers_that_do_not_advertise(oidc_settings, monkeypatch):
    monkeypatch.setattr(oidc_settings, "oidc_pkce", "true")
    assert oidc_module.pkce_enabled({}) is True
    monkeypatch.setattr(oidc_settings, "oidc_pkce", "false")
    assert oidc_module.pkce_enabled({"code_challenge_methods_supported": ["S256"]}) is False


def test_auth_url_carries_challenge_not_verifier(oidc_settings, monkeypatch):
    monkeypatch.setattr(
        oidc_module,
        "_discovery",
        {
            "authorization_endpoint": "https://idp.example.com/authorize",
            "code_challenge_methods_supported": ["S256"],
        },
    )
    verifier = oidc_module.generate_pkce_verifier()
    url = oidc_module.build_auth_url("https://cove.example.com/cb", "state", "nonce", verifier)
    assert "code_challenge_method=S256" in url
    assert oidc_module.pkce_challenge(verifier) in url.replace("%3D", "=")
    # The verifier itself must never leave the server via the front channel.
    assert verifier not in url


def test_token_auth_defaults_to_basic_when_unadvertised(oidc_settings):
    # Kanidm accepts only HTTP Basic, and OIDC Core makes it the default, so an
    # empty/absent list must not fall back to posting the secret in the body.
    assert oidc_module.token_auth_method({}) == "client_secret_basic"
    assert (
        oidc_module.token_auth_method({"token_endpoint_auth_methods_supported": ["client_secret_post"]})
        == "client_secret_post"
    )
    assert (
        oidc_module.token_auth_method(
            {"token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"]}
        )
        == "client_secret_basic"
    )


def test_groups_scope_only_requested_when_admin_group_set(oidc_settings, monkeypatch):
    assert oidc_module.effective_scopes() == "openid email profile"
    monkeypatch.setattr(oidc_settings, "oidc_admin_group", "cove-admins")
    assert oidc_module.effective_scopes() == "openid email profile groups"
    # Kanidm's name-only variant, for providers that split the scope.
    monkeypatch.setattr(oidc_settings, "oidc_groups_scope", "groups_name")
    assert oidc_module.effective_scopes().endswith("groups_name")


def test_openid_scope_always_present(oidc_settings, monkeypatch):
    monkeypatch.setattr(oidc_settings, "oidc_scopes", "email profile")
    assert oidc_module.effective_scopes().split()[0] == "openid"


@pytest.mark.parametrize(
    "groups",
    [
        ["cove-admins"],                                  # Authentik, Okta: bare name
        ["cove-admins@idm.example.com"],                  # Kanidm: SPN
        ["/cove-admins"],                                 # Keycloak: group path
        ["COVE-Admins"],                                  # case difference
        ["other", "cove-admins@idm.example.com"],         # mixed membership
        ["8a1f...uuid", "cove-admins@idm.example.com"],   # Kanidm `groups`: uuid + spn
        "cove-admins other",                              # provider sent a string
        [{"name": "cove-admins"}],                        # object-shaped entries
    ],
)
def test_admin_group_matches_every_provider_spelling(oidc_settings, monkeypatch, groups):
    monkeypatch.setattr(oidc_settings, "oidc_admin_group", "cove-admins")
    assert oidc_module.is_admin_from_claims({"groups": groups}) is True


def test_admin_group_does_not_substring_match(oidc_settings, monkeypatch):
    monkeypatch.setattr(oidc_settings, "oidc_admin_group", "admin")
    assert oidc_module.is_admin_from_claims({"groups": ["not-admins"]}) is False
    assert oidc_module.is_admin_from_claims({"groups": "not-admins"}) is False


def test_admin_group_accepts_a_list(oidc_settings, monkeypatch):
    monkeypatch.setattr(oidc_settings, "oidc_admin_group", "ops, cove-admins")
    assert oidc_module.is_admin_from_claims({"groups": ["cove-admins"]}) is True
    assert oidc_module.is_admin_from_claims({"groups": ["nobody"]}) is False


def test_nested_groups_claim_path(oidc_settings, monkeypatch):
    # Keycloak keeps realm roles one level down.
    monkeypatch.setattr(oidc_settings, "oidc_admin_group", "cove-admins")
    monkeypatch.setattr(oidc_settings, "oidc_groups_claim", "realm_access.roles")
    claims = {"realm_access": {"roles": ["cove-admins"]}}
    assert oidc_module.is_admin_from_claims(claims) is True
    assert oidc_module.is_admin_from_claims({"groups": ["cove-admins"]}) is False


def test_no_admin_group_configured_never_grants_admin(oidc_settings):
    assert oidc_module.is_admin_from_claims({"groups": ["cove-admins"]}) is False


def test_username_strips_spn_domain(oidc_settings):
    # Kanidm's preferred_username is an SPN unless prefer-short-username is set;
    # without stripping this becomes "alice-idm-example-com".
    assert oidc_module.extract_username({"preferred_username": "alice@idm.example.com"}) == "alice"


def test_username_falls_back_through_claim_list(oidc_settings):
    assert oidc_module.extract_username({"email": "bob@example.com"}) == "bob"
    assert oidc_module.extract_username({"sub": "abc123"}) == "abc123"
    assert oidc_module.extract_username({}) == "user"


def test_username_claim_order_is_configurable(oidc_settings, monkeypatch):
    claims = {"preferred_username": "spn-name", "email": "wanted@example.com"}
    monkeypatch.setattr(oidc_settings, "oidc_username_claims", "email,sub")
    assert oidc_module.extract_username(claims) == "wanted"


def test_username_domain_stripping_can_be_disabled(oidc_settings, monkeypatch):
    monkeypatch.setattr(oidc_settings, "oidc_username_strip_domain", False)
    assert oidc_module.extract_username({"preferred_username": "alice@idm.example.com"}) == (
        "alice-idm.example.com"
    )
