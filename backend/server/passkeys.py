"""Passkeys and security keys (WebAuthn) for local accounts.

Sign-in is username first: the browser is told which credentials the account
has (``allowCredentials``), so any FIDO2 security key works, including ones
that can't store passkeys. An unknown account, or one without passkeys, gets
plausible made-up credential ids derived from the username, so the options
don't reveal which accounts exist or use passkeys.

User verification (the key's PIN or fingerprint) is required, which is what
lets a passkey sign in on its own, without a password or TOTP.

The relying party is Cove's public host: ``COVE_APP_ORIGIN`` when set, else
the host the browser used. WebAuthn needs a domain name: it doesn't work when
Cove is opened by IP address, and passkeys made on one host name don't work on
another.

Challenges travel in a signed, single-use state token rather than server-side
storage.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from server import mfa
from server.config import get_settings
from server.models import User, WebAuthnCredential

RP_NAME = "Cove"
TIMEOUT_MS = 120_000
STATE_MINUTES = 5


def _webauthn():
    import webauthn  # heavy-ish import (cbor2, pyOpenSSL); only when used

    return webauthn


def _configured() -> Optional[tuple[str, str]]:
    configured = get_settings().app_origin
    if not configured:
        return None
    parts = urlsplit(configured)
    rp_id = parts.hostname or ""
    if not rp_id or _is_ip(rp_id):
        return None
    return rp_id, f"{parts.scheme}://{parts.netloc}"


def _is_ip(host: str) -> bool:
    import ipaddress

    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def enabled() -> bool:
    """Passkeys work only with COVE_APP_ORIGIN set to a domain name."""
    return _configured() is not None


def relying_party(request: Request) -> tuple[str, str]:
    """(rp_id, origin) for passkeys: always COVE_APP_ORIGIN, never the request's
    Host header. A proxy that reaches Cove under another name (a phishing site)
    could otherwise get passkeys made for its own domain, and a passkey counts
    as both factors because it can't be phished."""
    rp = _configured()
    if rp is None:
        raise HTTPException(
            status_code=400,
            detail="Passkeys aren't available: Cove needs COVE_APP_ORIGIN set to its address by domain name.",
        )
    return rp


def site_name(request: Request) -> str:
    """The name authenticator apps show next to the account."""
    rp = _configured()
    if rp:
        return rp[0]
    return urlsplit(str(request.base_url)).hostname or "cove"


def _hints(request: Request) -> list[str]:
    """A thin client has no passkey store of its own: steer Chromium straight to
    the USB security key prompt."""
    from server import thin_clients

    return ["security-key"] if request.cookies.get(thin_clients.BIND_COOKIE) else []


def _b64(data: bytes) -> str:
    from webauthn.helpers import bytes_to_base64url

    return bytes_to_base64url(data)


def _state(kind: str, challenge: bytes, rp_id: str, origin: str, user: Optional[User], **extra) -> str:
    claims = {"ch": _b64(challenge), "rp": rp_id, "origin": origin, **extra}
    if user is None:
        # Login for an account that may not exist: no subject, the token is
        # still single use and signed.
        fake = User(id=0)
        return mfa.issue(kind, fake, STATE_MINUTES, **claims)
    return mfa.issue(kind, user, STATE_MINUTES, **claims)


def _options_json(options) -> dict:
    return json.loads(_webauthn().options_to_json(options))


# ── Registration ─────────────────────────────────────────────────────────────


def registration_options(request: Request, user: User) -> dict:
    wa = _webauthn()
    from webauthn.helpers import structs

    rp_id, origin = relying_party(request)
    options = wa.generate_registration_options(
        rp_id=rp_id,
        rp_name=RP_NAME,
        user_name=user.username,
        user_id=hashlib.sha256(f"cove-user:{user.id}".encode()).digest()[:16],
        user_display_name=user.username,
        timeout=TIMEOUT_MS,
        authenticator_selection=structs.AuthenticatorSelectionCriteria(
            resident_key=structs.ResidentKeyRequirement.PREFERRED,
            user_verification=structs.UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            structs.PublicKeyCredentialDescriptor(id=wa.base64url_to_bytes(c.credential_id)) for c in user.passkeys
        ],
        hints=[structs.PublicKeyCredentialHint(h) for h in _hints(request)] or None,
    )
    return {
        "options": _options_json(options),
        "state": _state("webauthn_reg", options.challenge, rp_id, origin, user),
    }


def register(db: Session, request: Request, user: User, state: str, credential: dict, name: str) -> WebAuthnCredential:
    claims = mfa.take(state, "webauthn_reg")
    if claims is None or str(claims.get("sub")) != str(user.id):
        raise HTTPException(status_code=400, detail="This passkey request expired. Try again.")
    wa = _webauthn()
    try:
        verified = wa.verify_registration_response(
            credential=credential,
            expected_challenge=wa.base64url_to_bytes(claims["ch"]),
            expected_rp_id=claims["rp"],
            expected_origin=claims["origin"],
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"The passkey couldn't be verified: {exc}") from exc
    cred_id = _b64(verified.credential_id)
    if db.scalar(select(WebAuthnCredential).where(WebAuthnCredential.credential_id == cred_id)):
        raise HTTPException(status_code=409, detail="That passkey is already registered.")
    transports = (credential.get("response") or {}).get("transports") or []
    row = WebAuthnCredential(
        credential_id=cred_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=",".join(str(t) for t in transports if isinstance(t, str))[:128] or None,
        name=(name or "").strip()[:64] or "Passkey",
    )
    user.passkeys.append(row)
    return row


# ── Sign-in ──────────────────────────────────────────────────────────────────


# Transport lists as browsers report them (sorted): a USB key, a USB+NFC key,
# a phone or laptop passkey.
_FAKE_TRANSPORTS = (["usb"], ["nfc", "usb"], ["hybrid", "internal"], ["internal"])
_FAKE_ID_LENGTHS = (16, 32, 48, 64)


def _fake_credentials(username: str) -> list[tuple[bytes, list[str]]]:
    """Stable made-up (credential id, transports) for an account without
    passkeys, shaped like real ones: one or two, with differing id lengths and
    transports, so the options don't tell which accounts have passkeys."""
    key = get_settings().get_secret_key().encode()
    seed = hmac.new(key, f"cove-fake-passkey:{username.lower()}".encode(), hashlib.sha256).digest()
    fakes = []
    for i in range(1 + seed[0] % 2):
        length = _FAKE_ID_LENGTHS[seed[1 + i] % len(_FAKE_ID_LENGTHS)]
        cred_id = b"".join(hashlib.sha256(seed + bytes([i, j])).digest() for j in range(2))[:length]
        fakes.append((cred_id, _FAKE_TRANSPORTS[seed[3 + i] % len(_FAKE_TRANSPORTS)]))
    return fakes


def authentication_options(db: Session, request: Request, username: str) -> dict:
    wa = _webauthn()
    from webauthn.helpers import structs

    rp_id, origin = relying_party(request)
    user = mfa.find_user(db, username)
    usable = user is not None and mfa.applies_to(user) and bool(user.passkeys)
    if usable:
        allow = [
            structs.PublicKeyCredentialDescriptor(
                id=wa.base64url_to_bytes(c.credential_id),
                transports=[structs.AuthenticatorTransport(t) for t in (c.transports or "").split(",")
                            if t in {x.value for x in structs.AuthenticatorTransport}] or None,
            )
            for c in user.passkeys
        ]
    else:
        allow = [
            structs.PublicKeyCredentialDescriptor(id=i, transports=[structs.AuthenticatorTransport(t) for t in ts])
            for i, ts in _fake_credentials(username)
        ]
    options = wa.generate_authentication_options(
        rp_id=rp_id,
        timeout=TIMEOUT_MS,
        allow_credentials=allow,
        user_verification=structs.UserVerificationRequirement.REQUIRED,
    )
    body = _options_json(options)
    hints = _hints(request)
    if hints:
        body["hints"] = hints
    return {
        "options": body,
        "state": _state("webauthn_auth", options.challenge, rp_id, origin, user if usable else None,
                        uname=username),
    }


def authenticate(db: Session, state: str, credential: dict) -> Optional[User]:
    """The account a valid assertion signs in, or None. Wrong key, wrong account,
    replayed or expired requests all come back as None."""
    # Single use, atomically: two requests replaying one signed challenge can't
    # both get past this.
    claims = mfa.take(state, "webauthn_auth")
    if claims is None:
        return None
    cred_id = str(credential.get("id") or credential.get("rawId") or "")
    row = db.scalar(select(WebAuthnCredential).where(WebAuthnCredential.credential_id == cred_id))
    if row is None or str(row.user_id) != str(claims.get("sub")):
        return None
    wa = _webauthn()
    try:
        verified = wa.verify_authentication_response(
            credential=credential,
            expected_challenge=wa.base64url_to_bytes(claims["ch"]),
            expected_rp_id=claims["rp"],
            expected_origin=claims["origin"],
            credential_public_key=row.public_key,
            credential_current_sign_count=row.sign_count,
            require_user_verification=True,
        )
    except Exception:
        return None
    row.sign_count = verified.new_sign_count
    row.last_used_at = datetime.now(timezone.utc)
    user = db.get(User, row.user_id)
    if user is None or not mfa.applies_to(user):
        return None
    return user


def new_name() -> str:
    return f"Passkey {secrets.token_hex(2)}"
