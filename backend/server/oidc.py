"""Provider-agnostic OpenID Connect client.

Everything provider-specific is derived from the issuer's discovery document
(RFC 8414 / OIDC Discovery) rather than hardcoded, so Cove works against any
spec-compliant IdP — Kanidm, Authentik, Keycloak, Entra ID, Okta, Google — with
only issuer/client credentials configured. The escape hatches (`COVE_OIDC_PKCE`,
`COVE_OIDC_TOKEN_AUTH_METHOD`, `COVE_OIDC_*_CLAIM`) exist for providers whose
metadata under-reports what they actually support.
"""

import base64
import hashlib
import re
import secrets
import time
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWK
from jwt import PyJWTError as JWTError

from server.config import get_settings

# Reuse the canonical username charset for sanitizing OIDC-derived usernames.
_USERNAME_CHARSET_RE = re.compile(r"[^a-zA-Z0-9._-]")

_discovery: Optional[dict] = None
_discovery_at: float = 0.0
_jwks: Optional[dict] = None
_jwks_at: float = 0.0

# Asymmetric signing algorithms we accept for ID tokens. The allowed set is a
# verifier-side policy decision and is NEVER taken from the (network-fetched,
# cacheable) discovery document: trusting that list would let an IdP — or a
# poisoned/misconfigured metadata response — advertise HS256 or "none" and open
# an RS/HS key-confusion forgery (sign with the public JWKS key as the HMAC
# secret). We intersect discovery's advertised algs with this allowlist so real
# IdPs keep working, but symmetric/none can never slip in.
_ALLOWED_ID_TOKEN_ALGS = frozenset(
    {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512"}
)

# Client authentication at the token endpoint. OIDC Core says client_secret_basic
# is the default when a provider advertises nothing, and some providers (Kanidm)
# accept *only* Basic — so Basic is what we prefer and fall back to.
_AUTH_BASIC = "client_secret_basic"
_AUTH_POST = "client_secret_post"


def _norm_issuer(value: str) -> str:
    """Issuers differ only by a trailing slash across providers; compare without it."""
    return (value or "").rstrip("/")


def reset_cache() -> None:
    """Drop cached discovery/JWKS (used by tests and after a config change)."""
    global _discovery, _discovery_at, _jwks, _jwks_at
    _discovery = None
    _discovery_at = 0.0
    _jwks = None
    _jwks_at = 0.0


async def fetch_discovery(force: bool = False) -> dict:
    """Fetch (and cache) the issuer's OIDC discovery document.

    Cached for `oidc_metadata_ttl_seconds` rather than forever: a provider that
    rotates endpoints or signing algorithms must not need a Cove restart.
    """
    global _discovery, _discovery_at
    settings = get_settings()
    ttl = settings.oidc_metadata_ttl_seconds
    if _discovery and not force and (time.monotonic() - _discovery_at) < ttl:
        return _discovery

    url = settings.oidc_discovery_url or (
        _norm_issuer(settings.oidc_issuer) + "/.well-known/openid-configuration"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=10)
        resp.raise_for_status()
        doc = resp.json()

    # The issuer in the metadata is the value tokens are signed with. If it does
    # not match what the operator configured, fail loudly at discovery time
    # rather than silently accepting either value at token-verification time.
    advertised = _norm_issuer(doc.get("issuer", ""))
    configured = _norm_issuer(settings.oidc_issuer)
    if advertised and configured and advertised != configured:
        raise RuntimeError(
            f"OIDC issuer mismatch: configured COVE_OIDC_ISSUER={configured!r} but "
            f"the discovery document at {url} declares issuer={advertised!r}. "
            "Set COVE_OIDC_ISSUER to the advertised value."
        )

    _discovery = doc
    _discovery_at = time.monotonic()
    return _discovery


async def fetch_jwks(force: bool = False) -> dict:
    """Fetch (and cache) the issuer's JWKS, honoring the metadata TTL."""
    global _jwks, _jwks_at
    ttl = get_settings().oidc_metadata_ttl_seconds
    if _jwks and not force and (time.monotonic() - _jwks_at) < ttl:
        return _jwks
    discovery = await fetch_discovery()
    jwks_uri = discovery.get("jwks_uri")
    if not jwks_uri:
        raise RuntimeError("OIDC discovery document has no jwks_uri")
    async with httpx.AsyncClient() as client:
        resp = await client.get(jwks_uri, timeout=10)
        resp.raise_for_status()
        _jwks = resp.json()
    _jwks_at = time.monotonic()
    return _jwks


def effective_scopes() -> str:
    """The scope string to request.

    The groups scope is appended only when an admin group is configured: a
    provider that rejects unknown scopes (`invalid_scope`) must not fail login
    just because Cove asked for group data it has no use for. Its name is
    configurable because providers disagree — Kanidm alone offers `groups`,
    `groups_name` and `groups_spn`.
    """
    settings = get_settings()
    scopes = [s for s in (settings.oidc_scopes or "").split() if s]
    if "openid" not in scopes:
        scopes.insert(0, "openid")
    groups_scope = (settings.oidc_groups_scope or "").strip()
    if settings.oidc_admin_group and groups_scope and groups_scope not in scopes:
        scopes.append(groups_scope)
    return " ".join(scopes)


def generate_state() -> str:
    return secrets.token_urlsafe(32)


def generate_pkce_verifier() -> str:
    """RFC 7636 code_verifier: 43-128 chars from the unreserved set."""
    return secrets.token_urlsafe(64)[:128]


def pkce_challenge(verifier: str) -> str:
    """S256 challenge: base64url(sha256(verifier)), unpadded."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def pkce_enabled(discovery: dict) -> bool:
    """Whether to use PKCE for this provider.

    "auto" follows the discovery document, which is what makes Cove work
    unattended against providers that *require* PKCE (Kanidm rejects an
    authorization request without a code_challenge). Providers that support
    PKCE without advertising it need COVE_OIDC_PKCE=true.
    """
    setting = (get_settings().oidc_pkce or "auto").strip().lower()
    if setting in ("true", "yes", "1", "on"):
        return True
    if setting in ("false", "no", "0", "off"):
        return False
    return "S256" in (discovery.get("code_challenge_methods_supported") or [])


def token_auth_method(discovery: dict) -> str:
    """Pick client authentication for the token endpoint.

    Basic is preferred and is the fallback when a provider advertises nothing,
    matching the OIDC Core default; Kanidm accepts only Basic, while Authentik
    and Keycloak accept both.
    """
    setting = (get_settings().oidc_token_auth_method or "auto").strip().lower()
    if setting in (_AUTH_BASIC, _AUTH_POST):
        return setting
    supported = discovery.get("token_endpoint_auth_methods_supported") or []
    if _AUTH_BASIC in supported:
        return _AUTH_BASIC
    if _AUTH_POST in supported:
        return _AUTH_POST
    return _AUTH_BASIC


def build_auth_url(
    redirect_uri: str,
    state: str,
    nonce: Optional[str] = None,
    code_verifier: Optional[str] = None,
) -> str:
    settings = get_settings()
    # Discovery is cached; we build synchronously from cached data if available
    # For the redirect, we need the authorization endpoint
    if not _discovery:
        raise RuntimeError("OIDC discovery not loaded — call fetch_discovery() at startup")
    endpoint = _discovery.get("authorization_endpoint")
    if not endpoint:
        raise RuntimeError("OIDC discovery document has no authorization_endpoint")
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect_uri,
        "scope": effective_scopes(),
        "state": state,
    }
    if nonce:
        # Binds the id_token to this login attempt (defeats token replay/injection).
        params["nonce"] = nonce
    if code_verifier and pkce_enabled(_discovery):
        params["code_challenge"] = pkce_challenge(code_verifier)
        params["code_challenge_method"] = "S256"
    return endpoint + "?" + urlencode(params)


async def exchange_code(
    code: str, redirect_uri: str, code_verifier: Optional[str] = None
) -> dict:
    settings = get_settings()
    discovery = await fetch_discovery()
    endpoint = discovery.get("token_endpoint")
    if not endpoint:
        raise RuntimeError("OIDC discovery document has no token_endpoint")

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        # Sent even under Basic auth: harmless, and some providers want it.
        "client_id": settings.oidc_client_id,
    }
    if code_verifier and pkce_enabled(discovery):
        data["code_verifier"] = code_verifier

    auth = None
    if token_auth_method(discovery) == _AUTH_BASIC:
        auth = (settings.oidc_client_id or "", settings.oidc_client_secret or "")
    else:
        data["client_secret"] = settings.oidc_client_secret

    async with httpx.AsyncClient() as client:
        resp = await client.post(endpoint, data=data, auth=auth, timeout=15)
        resp.raise_for_status()
        return resp.json()


async def fetch_userinfo(access_token: str) -> dict:
    """Fetch the userinfo claims, or {} if unavailable.

    Providers disagree about what lands in the id_token: several put group
    membership (and sometimes the username) only in userinfo. Failures are
    non-fatal — the id_token alone still authenticates the login.
    """
    discovery = await fetch_discovery()
    endpoint = discovery.get("userinfo_endpoint")
    if not endpoint:
        return {}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


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


async def verify_id_token(id_token: str, nonce: Optional[str] = None) -> dict:
    """Verify an id_token's signature and standard claims; return the claims.

    Fetches JWKS + discovery, selects the signing key by the token header `kid`,
    and verifies signature, audience (oidc_client_id) and issuer. When ``nonce``
    is supplied, the token's ``nonce`` claim must match it (binds the token to
    this login attempt). Raises jwt.PyJWTError (or a subclass) on any failure.

    An unknown `kid` forces one JWKS refetch before failing, so a provider that
    rotates signing keys mid-cache does not lock everyone out until restart.
    """
    settings = get_settings()
    discovery = await fetch_discovery()
    jwks = await fetch_jwks()

    header = jwt.get_unverified_header(id_token)
    try:
        key = _select_signing_key(jwks, header.get("kid"))
    except JWTError:
        jwks = await fetch_jwks(force=True)
        key = _select_signing_key(jwks, header.get("kid"))

    # Pin to our asymmetric allowlist; never honor HS*/none even if advertised.
    advertised = discovery.get("id_token_signing_alg_values_supported") or []
    algorithms = [a for a in advertised if a in _ALLOWED_ID_TOKEN_ALGS] or ["RS256"]

    # Issuer equality with the configured value is enforced in fetch_discovery();
    # verify against the issuer the provider actually signs with, and accept the
    # trailing-slash variant since providers are inconsistent about it.
    issuer = _norm_issuer(discovery.get("issuer") or settings.oidc_issuer or "")
    accepted_issuers = [i for i in {issuer, issuer + "/"} if i]

    claims = jwt.decode(
        id_token,
        # PyJWK turns the JWK dict into a cryptography public key; the accepted
        # algorithms still come only from our allowlist, never from the key or header.
        key=PyJWK(key).key,
        algorithms=algorithms,
        audience=settings.oidc_client_id,
        issuer=accepted_issuers,
        options={"verify_aud": True},
    )

    if nonce is not None and claims.get("nonce") != nonce:
        raise JWTError("id_token nonce mismatch")

    return claims


def _claim_at_path(claims: dict, path: str) -> Any:
    """Look up a claim by dotted path, so nested claims work.

    Keycloak puts roles at `realm_access.roles`; a flat `.get()` cannot reach them.
    """
    current: Any = claims
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def extract_username(claims: dict) -> str:
    """Derive a username from OIDC claims, sanitized to the allowed charset.

    Which claims to try, and in what order, is configurable because providers
    populate different ones (Google has no `preferred_username`; Kanidm's is an
    SPN). Disallowed characters are replaced with '-' and the result is trimmed
    to 64 chars. Falls back to a safe value ("user") if nothing usable remains.
    """
    settings = get_settings()
    candidates = [
        c.strip()
        for c in (settings.oidc_username_claims or "preferred_username,email,sub").split(",")
        if c.strip()
    ]
    for claim in candidates:
        raw = _claim_at_path(claims, claim)
        if not isinstance(raw, str) or not raw.strip():
            continue
        raw = raw.strip()
        # An SPN (`user@idm.example.com`) or an email would otherwise become
        # "user-idm-example-com" once sanitized.
        if settings.oidc_username_strip_domain and "@" in raw:
            raw = raw.split("@", 1)[0]
        cleaned = sanitize_username(raw)
        if cleaned:
            return cleaned
    return "user"


def sanitize_username(raw: str) -> str:
    """Coerce an arbitrary string into a valid Cove username."""
    from server.security import is_reserved_username

    cleaned = _USERNAME_CHARSET_RE.sub("-", raw or "").strip("-")[:64]
    if not cleaned or cleaned in (".", ".."):
        return "user"
    # An IdP-chosen name must not land on a reserved storage directory.
    while cleaned and is_reserved_username(cleaned):
        cleaned = cleaned[1:].lstrip("-") if not cleaned.lower().startswith("cove-") else cleaned[5:].lstrip("-")
    return cleaned or "user"


def _group_variants(entry: Any) -> set[str]:
    """Every spelling of one group entry an operator might reasonably configure.

    Providers return group membership in mutually incompatible shapes: bare names
    (Authentik, Okta), SPNs (Kanidm `groups`/`groups_spn`), path-prefixed names
    (Keycloak `/cove-admins`), opaque UUIDs (Kanidm `groups`, Entra ID), and
    sometimes objects rather than strings. Matching any spelling means the admin
    group can be written the obvious way regardless of provider.
    """
    if isinstance(entry, dict):
        entry = entry.get("name") or entry.get("id") or entry.get("value") or ""
    if not isinstance(entry, str):
        return set()
    value = entry.strip()
    if not value:
        return set()
    variants = {value}
    if "/" in value:
        variants.add(value.rsplit("/", 1)[-1])
    for variant in list(variants):
        if "@" in variant:
            variants.add(variant.split("@", 1)[0])
    return {v.casefold() for v in variants if v}


def is_admin_from_claims(claims: dict) -> bool:
    """Whether these claims grant admin, per COVE_OIDC_ADMIN_GROUP.

    The claim holding groups is configurable (dotted paths supported) and the
    setting accepts a comma-separated list — membership of any one grants admin.
    """
    settings = get_settings()
    if not settings.oidc_admin_group:
        return False

    groups = _claim_at_path(claims, settings.oidc_groups_claim or "groups")
    # A string here would make ``in`` a substring test ("admin" in "not-admins"),
    # but a provider returning a single space/comma-separated string is real, so
    # split it rather than discarding it.
    if isinstance(groups, str):
        groups = re.split(r"[,\s]+", groups)
    if not isinstance(groups, (list, tuple, set)):
        return False

    held: set[str] = set()
    for entry in groups:
        held |= _group_variants(entry)

    wanted = {
        g.strip().casefold()
        for g in settings.oidc_admin_group.split(",")
        if g.strip()
    }
    return bool(wanted & held)
