"""Unit tests for server.security (no DB, no network)."""

from server import security


def test_password_hash_roundtrip():
    hashed = security.hash_password("s3cret-pass")
    assert hashed != "s3cret-pass"
    assert security.verify_password("s3cret-pass", hashed) is True


def test_password_wrong_password():
    hashed = security.hash_password("s3cret-pass")
    assert security.verify_password("wrong", hashed) is False


def test_access_token_decodes_with_correct_claims():
    token = security.create_access_token(user_id=42, is_admin=True)
    payload = security.decode_token(token)
    assert payload is not None
    assert payload["type"] == "access"
    assert payload["sub"] == "42"
    assert payload["adm"] is True


def test_refresh_token_decodes_with_correct_claims():
    token = security.create_refresh_token(user_id=7)
    payload = security.decode_token(token)
    assert payload is not None
    assert payload["type"] == "refresh"
    assert payload["sub"] == "7"


def test_decode_token_returns_none_on_garbage():
    assert security.decode_token("not-a-jwt") is None
    assert security.decode_token("a.b.c") is None


def test_decode_token_returns_none_on_tampered_token():
    token = security.create_access_token(user_id=1, is_admin=False)
    # Tamper with the *payload* segment so the signature no longer matches.
    # (Flipping a single trailing base64url char of the signature can be a
    # no-op because the final char only carries a couple of significant bits.)
    head, body, sig = token.split(".")
    tampered_char = "A" if body[5] != "A" else "B"
    tampered = f"{head}.{body[:5]}{tampered_char}{body[6:]}.{sig}"
    assert security.decode_token(tampered) is None


def test_sign_verify_state_roundtrip():
    signed = security.sign_state("nonce-123")
    assert signed.startswith("nonce-123.")
    assert security.verify_state(signed) == "nonce-123"


def test_verify_state_tampered_returns_none():
    signed = security.sign_state("nonce-abc")
    nonce, _, sig = signed.rpartition(".")
    tampered = f"{nonce}.{sig[:-1]}{'0' if sig[-1] != '0' else '1'}"
    assert security.verify_state(tampered) is None


def test_verify_state_malformed_returns_none():
    assert security.verify_state("") is None
    assert security.verify_state("no-dot-here") is None


def test_decode_access_token_alias():
    assert security.decode_access_token is security.decode_token


def test_encrypt_secret_roundtrip():
    enc = security.encrypt_secret("tskey-auth-secret-123")
    assert enc.startswith(security._SECRET_PREFIX)
    assert "tskey-auth-secret-123" not in enc  # ciphertext, not plaintext
    assert security.decrypt_secret(enc) == "tskey-auth-secret-123"


def test_decrypt_secret_passthrough_legacy_plaintext():
    # A value without the encrypted prefix is treated as legacy plaintext.
    assert security.decrypt_secret("tskey-plain") == "tskey-plain"
    assert security.decrypt_secret(None) is None
    assert security.decrypt_secret("") == ""


def test_decrypt_secret_returns_none_on_corrupt_ciphertext():
    assert security.decrypt_secret(security._SECRET_PREFIX + "not-valid-fernet") is None
