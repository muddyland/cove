"""A minimal software FIDO2 authenticator for tests: makes registration and
assertion responses the real py_webauthn verification accepts."""

import base64
import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


UP, UV, AT = 0x01, 0x04, 0x40


class SoftKey:
    def __init__(self, origin="http://testserver", rp_id="testserver", counts=True):
        self.origin = origin
        self.rp_id = rp_id
        # Many passkeys (synced ones especially) always report a counter of 0.
        self.counts = counts
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(32)
        self.count = 0

    def _cose(self) -> bytes:
        n = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})

    def _client_data(self, kind: str, challenge: str, origin=None) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin or self.origin,
                           "crossOrigin": False}).encode()

    def register(self, options: dict, uv=True) -> dict:
        rp_hash = hashlib.sha256(options["rp"]["id"].encode()).digest()
        flags = UP | AT | (UV if uv else 0)
        attested = b"\0" * 16 + struct.pack(">H", len(self.cred_id)) + self.cred_id + self._cose()
        auth_data = rp_hash + bytes([flags]) + struct.pack(">I", self.count) + attested
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        cd = self._client_data("webauthn.create", options["challenge"])
        return {
            "id": b64u(self.cred_id), "rawId": b64u(self.cred_id), "type": "public-key",
            "response": {"clientDataJSON": b64u(cd), "attestationObject": b64u(att), "transports": ["usb"]},
            "clientExtensionResults": {},
        }

    def assert_(self, options: dict, uv=True, origin=None, cred_id=None) -> dict:
        if self.counts:
            self.count += 1
        rp_hash = hashlib.sha256(options.get("rpId", self.rp_id).encode()).digest()
        auth_data = rp_hash + bytes([UP | (UV if uv else 0)]) + struct.pack(">I", self.count)
        cd = self._client_data("webauthn.get", options["challenge"], origin)
        sig = self.key.sign(auth_data + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        cid = cred_id or self.cred_id
        return {
            "id": b64u(cid), "rawId": b64u(cid), "type": "public-key",
            "response": {"clientDataJSON": b64u(cd), "authenticatorData": b64u(auth_data),
                         "signature": b64u(sig), "userHandle": None},
            "clientExtensionResults": {},
        }
