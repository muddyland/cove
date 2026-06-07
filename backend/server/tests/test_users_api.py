"""Tests for the users router (per-user Tailscale config)."""

from sqlalchemy import select

from server.db import SessionLocal
from server.models import UserTailscale
from server.tests.helpers import setup_admin


def test_default_tailscale_config(client):
    setup_admin(client)
    resp = client.get("/api/users/me/tailscale")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "enabled": False,
        "has_auth_key": False,
        "login_server": None,
    }


def test_put_sets_fields_and_auth_key_never_leaks(client):
    setup_admin(client)
    resp = client.put(
        "/api/users/me/tailscale",
        json={
            "auth_key": "tskey-secret-123",
            "login_server": "https://hs.example.com",
            "enabled": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_auth_key"] is True
    assert body["enabled"] is True
    assert body["login_server"] == "https://hs.example.com"
    # Routing options no longer live at the user level.
    assert "exit_node" not in body
    assert "accept_routes" not in body
    assert "accept_dns" not in body
    # The raw key must never appear in any response field.
    assert "auth_key" not in body
    assert "tskey-secret-123" not in resp.text

    # GET shows has_auth_key true and still never leaks.
    got = client.get("/api/users/me/tailscale")
    assert got.json()["has_auth_key"] is True
    assert "tskey-secret-123" not in got.text


def test_omitted_auth_key_left_unchanged(client):
    setup_admin(client)
    client.put("/api/users/me/tailscale", json={"auth_key": "tskey-keep-me"})
    # PUT without auth_key field -> key unchanged.
    resp = client.put("/api/users/me/tailscale", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["has_auth_key"] is True
    assert resp.json()["enabled"] is True


def test_clearing_auth_key(client):
    setup_admin(client)
    client.put("/api/users/me/tailscale", json={"auth_key": "tskey-clear-me"})
    assert client.get("/api/users/me/tailscale").json()["has_auth_key"] is True
    # Explicit empty string clears the key.
    resp = client.put("/api/users/me/tailscale", json={"auth_key": ""})
    assert resp.status_code == 200
    assert resp.json()["has_auth_key"] is False


def test_auth_key_stored_encrypted_at_rest(client):
    from server import security

    setup_admin(client)
    client.put("/api/users/me/tailscale", json={"auth_key": "tskey-auth-PLAINTEXT-xyz"})

    db = SessionLocal()
    try:
        ts = db.scalar(select(UserTailscale))
        stored = ts.auth_key
    finally:
        db.close()

    # The raw key must not be on disk; it is a recognizable encrypted token that
    # decrypts back to the original.
    assert stored is not None
    assert "tskey-auth-PLAINTEXT-xyz" not in stored
    assert stored.startswith(security._SECRET_PREFIX)
    assert security.decrypt_secret(stored) == "tskey-auth-PLAINTEXT-xyz"


def test_login_server_must_be_https(client):
    setup_admin(client)
    # Plain http:// is rejected (downgrades the control channel / can MITM).
    resp = client.put("/api/users/me/tailscale", json={"login_server": "http://evil.example.com"})
    assert resp.status_code == 400, resp.text
    # A bare value with no scheme/host is rejected.
    resp = client.put("/api/users/me/tailscale", json={"login_server": "evil.example.com"})
    assert resp.status_code == 400, resp.text
    # The rejected values must not have been persisted.
    assert client.get("/api/users/me/tailscale").json()["login_server"] is None


def test_login_server_https_accepted_and_clearable(client):
    setup_admin(client)
    resp = client.put("/api/users/me/tailscale", json={"login_server": "https://hs.example.com"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["login_server"] == "https://hs.example.com"
    # Empty string clears it back to None.
    resp = client.put("/api/users/me/tailscale", json={"login_server": ""})
    assert resp.status_code == 200
    assert resp.json()["login_server"] is None


# ── Gluetun (per-user VPN) ──────────────────────────────────────────────────────

def test_default_gluetun_config(client):
    setup_admin(client)
    resp = client.get("/api/users/me/gluetun")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "enabled": False,
        "vpn_type": "openvpn",
        "has_config": False,
        "config_filename": None,
        "has_wireguard_private_key": False,
        "has_openvpn_user": False,
        "has_openvpn_password": False,
    }


def test_put_gluetun_stores_encrypted_and_never_leaks(client):
    setup_admin(client)
    resp = client.put(
        "/api/users/me/gluetun",
        json={
            "enabled": True,
            "vpn_type": "wireguard",
            "config_file": "[Interface]\nPrivateKey=SECRETKEY\n",
            "config_filename": "wg0.conf",
            "wireguard_private_key": "OVERRIDEKEY",
            "openvpn_password": "p@ss",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Presence flags only — never the secrets/config themselves.
    assert body == {
        "enabled": True,
        "vpn_type": "wireguard",
        "has_config": True,
        "config_filename": "wg0.conf",
        "has_wireguard_private_key": True,
        "has_openvpn_user": False,
        "has_openvpn_password": True,
    }
    assert "SECRETKEY" not in resp.text and "OVERRIDEKEY" not in resp.text

    # Stored encrypted at rest (not plaintext) and round-trips on decrypt.
    from server.models import UserGluetun
    from server.security import decrypt_secret
    db = SessionLocal()
    try:
        g = db.scalar(select(UserGluetun))
        assert g.config_file.startswith("enc:v1:")
        assert "SECRETKEY" not in g.config_file
        assert decrypt_secret(g.config_file) == "[Interface]\nPrivateKey=SECRETKEY\n"
        assert decrypt_secret(g.wireguard_private_key) == "OVERRIDEKEY"
    finally:
        db.close()


def test_put_gluetun_rejects_bad_vpn_type(client):
    setup_admin(client)
    resp = client.put("/api/users/me/gluetun", json={"vpn_type": "ipsec"})
    assert resp.status_code == 400, resp.text


def test_put_gluetun_sentinel_leaves_config_unchanged(client):
    setup_admin(client)
    client.put("/api/users/me/gluetun", json={"config_file": "abc", "config_filename": "a.ovpn"})
    # A later update that omits config_file must not wipe it.
    client.put("/api/users/me/gluetun", json={"enabled": True})
    assert client.get("/api/users/me/gluetun").json()["has_config"] is True
    # Explicit empty string clears it.
    client.put("/api/users/me/gluetun", json={"config_file": ""})
    assert client.get("/api/users/me/gluetun").json()["has_config"] is False
