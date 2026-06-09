"""SSH key helpers: generate keypairs, validate uploaded private keys, and derive
the OpenSSH public key / type / fingerprint.

Private keys are sensitive and stored encrypted at rest (see security.encrypt_secret);
public keys are not secret and are stored/displayed in the clear so a user can copy
their public key into authorized_keys / GitHub / etc.
"""

import base64
import hashlib
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed25519, rsa


@dataclass
class ParsedKey:
    private_key: str  # OpenSSH/PEM private key text (to be encrypted before storage)
    public_key: str   # OpenSSH public key line ("ssh-ed25519 AAAA... comment")
    key_type: str     # "ed25519" | "rsa" | "ecdsa" | "dsa"


def _key_type(obj) -> str:
    if isinstance(obj, ed25519.Ed25519PrivateKey):
        return "ed25519"
    if isinstance(obj, rsa.RSAPrivateKey):
        return "rsa"
    if isinstance(obj, ec.EllipticCurvePrivateKey):
        return "ecdsa"
    if isinstance(obj, dsa.DSAPrivateKey):
        return "dsa"
    raise ValueError("Unsupported SSH key type")


def _public_openssh(obj, comment: str) -> str:
    pub = obj.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode()
    return f"{pub} {comment}".strip() if comment else pub


def key_filename(key_type: str) -> str:
    """The conventional ~/.ssh filename for a key of this type (what ssh probes)."""
    return {
        "ed25519": "id_ed25519",
        "rsa": "id_rsa",
        "ecdsa": "id_ecdsa",
        "dsa": "id_dsa",
    }.get(key_type, "id_ed25519")


def fingerprint(public_key: str) -> str:
    """SHA256 fingerprint of an OpenSSH public key line (``SHA256:base64``)."""
    try:
        blob = base64.b64decode(public_key.split()[1])
    except (IndexError, ValueError, base64.binascii.Error):
        return ""
    digest = hashlib.sha256(blob).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def generate_keypair(comment: str = "") -> ParsedKey:
    """Generate a fresh Ed25519 keypair (modern, small, widely supported)."""
    obj = ed25519.Ed25519PrivateKey.generate()
    private = obj.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return ParsedKey(
        private_key=private,
        public_key=_public_openssh(obj, comment),
        key_type="ed25519",
    )


def parse_private_key(text: str, comment: str = "") -> ParsedKey:
    """Validate an uploaded private key and derive its public key + type.

    Raises ValueError with a user-facing message on anything we can't use: a key
    we can't parse, or one protected by a passphrase (we can't use it
    non-interactively, so reject rather than store something that won't work).
    """
    data = (text or "").strip()
    if not data:
        raise ValueError("No private key provided")
    raw = data.encode()

    obj = None
    needs_passphrase = False
    for loader in (
        serialization.load_ssh_private_key,
        serialization.load_pem_private_key,
    ):
        try:
            obj = loader(raw, password=None)
            break
        except (TypeError, ValueError) as exc:
            # A passphrase-protected key surfaces as TypeError ("Password was not
            # given but private key is encrypted") or ValueError ("Key is
            # password-protected."). Both mention "password" — match only that, so
            # an unrelated parse failure (whose message may say "encrypted with an
            # unsupported algorithm") isn't misreported as passphrase-protected.
            if "password" in str(exc).lower():
                needs_passphrase = True
            continue

    if obj is None:
        if needs_passphrase:
            raise ValueError(
                "Passphrase-protected keys are not supported — upload an "
                "unencrypted key (or generate a new one)"
            )
        raise ValueError("Could not parse that as an SSH/PEM private key")

    key_type = _key_type(obj)
    # Re-serialize to a normalized OpenSSH private key so what we store is clean
    # and uniform regardless of the uploaded encoding (PEM/PKCS8/OpenSSH).
    private = obj.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return ParsedKey(
        private_key=private,
        public_key=_public_openssh(obj, comment),
        key_type=key_type,
    )
