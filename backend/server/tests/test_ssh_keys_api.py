"""Tests for per-user SSH key management + the per-workspace inject toggle."""

from cryptography.hazmat.primitives import serialization as s
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from sqlalchemy import select

from server import ssh_keys
from server.db import SessionLocal
from server.models import User
from server.tests.helpers import add_image, setup_admin


def _unencrypted_rsa_pem() -> str:
    r = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return r.private_bytes(
        s.Encoding.PEM, s.PrivateFormat.TraditionalOpenSSL, s.NoEncryption()
    ).decode()


def _encrypted_ed25519() -> str:
    k = ed25519.Ed25519PrivateKey.generate()
    return k.private_bytes(
        s.Encoding.PEM, s.PrivateFormat.OpenSSH, s.BestAvailableEncryption(b"secret")
    ).decode()


# ── ssh_keys module ─────────────────────────────────────────────────────────────

def test_generate_keypair_roundtrip():
    k = ssh_keys.generate_keypair("cove:bob")
    assert k.key_type == "ed25519"
    assert k.public_key.startswith("ssh-ed25519 ")
    assert k.public_key.endswith("cove:bob")
    assert "PRIVATE KEY" in k.private_key
    # Re-parsing the generated private key yields a matching public key.
    again = ssh_keys.parse_private_key(k.private_key)
    assert again.public_key.split()[1] == k.public_key.split()[1]


def test_fingerprint_and_filename():
    k = ssh_keys.generate_keypair()
    fp = ssh_keys.fingerprint(k.public_key)
    assert fp.startswith("SHA256:")
    assert ssh_keys.key_filename("ed25519") == "id_ed25519"
    assert ssh_keys.key_filename("rsa") == "id_rsa"
    assert ssh_keys.key_filename("unknown") == "id_ed25519"


def test_parse_rsa_pem():
    p = ssh_keys.parse_private_key(_unencrypted_rsa_pem())
    assert p.key_type == "rsa"
    assert p.public_key.startswith("ssh-rsa ")


def test_parse_rejects_garbage():
    try:
        ssh_keys.parse_private_key("definitely not a key")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "parse" in str(e).lower()


def test_parse_rejects_passphrase_key():
    try:
        ssh_keys.parse_private_key(_encrypted_ed25519())
        assert False, "expected ValueError"
    except ValueError as e:
        assert "passphrase" in str(e).lower()


# ── API ─────────────────────────────────────────────────────────────────────────

def test_default_ssh_is_empty(client):
    setup_admin(client)
    resp = client.get("/api/users/me/ssh")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "has_key": False,
        "public_key": None,
        "key_type": None,
        "fingerprint": None,
    }


def test_generate_returns_public_never_private(client):
    setup_admin(client)
    resp = client.post("/api/users/me/ssh/generate")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_key"] is True
    assert body["key_type"] == "ed25519"
    assert body["public_key"].startswith("ssh-ed25519 ")
    assert body["fingerprint"].startswith("SHA256:")
    # The private key must never appear anywhere in the response.
    assert "PRIVATE KEY" not in resp.text
    assert "private" not in body

    # Stored encrypted at rest.
    db = SessionLocal()
    try:
        u = db.scalar(select(User))
        assert u.ssh_private_key.startswith("enc:v1:")
    finally:
        db.close()


def test_upload_existing_key(client):
    setup_admin(client)
    resp = client.put("/api/users/me/ssh", json={"private_key": _unencrypted_rsa_pem()})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_key"] is True
    assert body["key_type"] == "rsa"
    assert body["public_key"].startswith("ssh-rsa ")
    assert "PRIVATE KEY" not in resp.text


def test_upload_garbage_rejected(client):
    setup_admin(client)
    resp = client.put("/api/users/me/ssh", json={"private_key": "nope"})
    assert resp.status_code == 400


def test_upload_passphrase_key_rejected(client):
    setup_admin(client)
    resp = client.put("/api/users/me/ssh", json={"private_key": _encrypted_ed25519()})
    assert resp.status_code == 400
    assert "passphrase" in resp.json()["detail"].lower()


def test_clear_via_put_empty(client):
    setup_admin(client)
    client.post("/api/users/me/ssh/generate")
    assert client.get("/api/users/me/ssh").json()["has_key"] is True
    resp = client.put("/api/users/me/ssh", json={"private_key": ""})
    assert resp.status_code == 200
    assert resp.json()["has_key"] is False


def test_delete_clears_key(client):
    setup_admin(client)
    client.post("/api/users/me/ssh/generate")
    resp = client.delete("/api/users/me/ssh")
    assert resp.status_code == 200
    assert resp.json()["has_key"] is False


def test_ssh_requires_auth(client):
    setup_admin(client)
    # Drop the auth cookie/header the TestClient set during setup.
    resp = client.get("/api/users/me/ssh", headers={"Authorization": "Bearer bad"})
    assert resp.status_code == 401


# ── Per-workspace inject toggle ──────────────────────────────────────────────────

def test_workspace_inject_ssh_key_defaults_true(client):
    setup_admin(client)
    image_id = add_image()
    resp = client.post("/api/workspaces", json={"name": "ws1", "image_id": image_id})
    assert resp.status_code == 201, resp.text
    assert resp.json()["inject_ssh_key"] is True


def test_workspace_inject_ssh_key_can_be_disabled(client):
    setup_admin(client)
    image_id = add_image()
    resp = client.post(
        "/api/workspaces",
        json={"name": "ws2", "image_id": image_id, "inject_ssh_key": False},
    )
    assert resp.status_code == 201, resp.text
    ws = resp.json()
    assert ws["inject_ssh_key"] is False
    # And can be toggled back on via PATCH.
    patched = client.patch(f"/api/workspaces/{ws['id']}", json={"inject_ssh_key": True})
    assert patched.status_code == 200, patched.text
    assert patched.json()["inject_ssh_key"] is True
