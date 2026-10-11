"""Tests for the users router: saved Tailscale / VPN profiles."""

from sqlalchemy import select

from server.db import SessionLocal
from server.models import TailscaleProfile, VpnProfile, Workspace
from server.tests.helpers import add_image, create_user_via_admin, login, setup_admin

TS = "/api/users/me/tailscale-profiles"
VPN = "/api/users/me/vpn-profiles"


# ── Tailscale profiles ─────────────────────────────────────────────────────────

def test_no_tailscale_profiles_by_default(client):
    setup_admin(client)
    resp = client.get(TS)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_create_tailscale_profile_never_leaks_key(client):
    setup_admin(client)
    resp = client.post(
        TS, json={"name": "Home", "auth_key": "tskey-secret-123", "login_server": "https://hs.example.com"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Home"
    assert body["has_auth_key"] is True
    assert body["login_server"] == "https://hs.example.com"
    assert body["problem"] is None
    assert body["used_by"] == []
    assert "auth_key" not in body
    assert "tskey-secret-123" not in resp.text
    assert "tskey-secret-123" not in client.get(TS).text


def test_several_tailscale_profiles_and_unique_names(client):
    setup_admin(client)
    assert client.post(TS, json={"name": "Home", "auth_key": "k1"}).status_code == 201
    assert client.post(TS, json={"name": "Work", "auth_key": "k2"}).status_code == 201
    dup = client.post(TS, json={"name": "home", "auth_key": "k3"})
    assert dup.status_code == 409, dup.text
    assert [p["name"] for p in client.get(TS).json()] == ["Home", "Work"]
    assert client.post(TS, json={"name": "  ", "auth_key": "k"}).status_code == 400


def test_tailscale_key_sentinel_and_clear(client):
    setup_admin(client)
    pid = client.post(TS, json={"name": "A", "auth_key": "tskey-keep-me"}).json()["id"]
    # PUT without auth_key -> key unchanged.
    resp = client.put(f"{TS}/{pid}", json={"name": "A2"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_auth_key"] is True and resp.json()["name"] == "A2"
    # Explicit empty string clears it, and the profile says why it can't be used.
    resp = client.put(f"{TS}/{pid}", json={"auth_key": ""})
    assert resp.json()["has_auth_key"] is False
    assert resp.json()["problem"] == "no auth key"


def test_tailscale_auth_key_stored_encrypted_at_rest(client):
    from server import security

    setup_admin(client)
    client.post(TS, json={"name": "A", "auth_key": "tskey-auth-PLAINTEXT-xyz"})
    db = SessionLocal()
    try:
        stored = db.scalar(select(TailscaleProfile)).auth_key
    finally:
        db.close()
    assert "tskey-auth-PLAINTEXT-xyz" not in stored
    assert stored.startswith(security._SECRET_PREFIX)
    assert security.decrypt_secret(stored) == "tskey-auth-PLAINTEXT-xyz"


def test_login_server_must_be_https(client):
    setup_admin(client)
    for bad in ("http://evil.example.com", "evil.example.com", "https://a.io --ssh"):
        resp = client.post(TS, json={"name": "X", "auth_key": "k", "login_server": bad})
        assert resp.status_code == 400, (bad, resp.text)
    assert client.get(TS).json() == []
    pid = client.post(
        TS, json={"name": "X", "auth_key": "k", "login_server": "https://hs.example.com"}
    ).json()["id"]
    resp = client.put(f"{TS}/{pid}", json={"login_server": ""})
    assert resp.json()["login_server"] is None


def test_other_users_profiles_are_invisible(client):
    token, _ = setup_admin(client)
    pid = client.post(TS, json={"name": "Mine", "auth_key": "k"}).json()["id"]
    vid = client.post(VPN, json={"name": "Mine", "config_file": "C"}).json()["id"]
    create_user_via_admin(client, token, "bob")
    client.cookies.clear()
    assert login(client, "bob", "password123").status_code == 200
    assert client.get(TS).json() == []
    assert client.get(VPN).json() == []
    assert client.put(f"{TS}/{pid}", json={"name": "x"}).status_code == 404
    assert client.delete(f"{VPN}/{vid}").status_code == 404


# ── VPN (Gluetun) profiles ─────────────────────────────────────────────────────

def test_provider_catalogue(client):
    setup_admin(client)
    resp = client.get("/api/users/vpn-providers")
    assert resp.status_code == 200, resp.text
    by = {p["value"]: p for p in resp.json()}
    assert by["mullvad"]["types"] == ["wireguard"] and by["mullvad"]["wg_addresses"] is True
    assert by["protonvpn"]["wg_addresses"] is False
    assert by["airvpn"]["wg_preshared_key"] is True
    assert by["private internet access"]["types"] == ["openvpn"]


def test_custom_vpn_profile_stores_encrypted_and_never_leaks(client):
    setup_admin(client)
    resp = client.post(
        VPN,
        json={
            "name": "Office",
            "vpn_type": "wireguard",
            "config_file": "[Interface]\nPrivateKey=SECRETKEY\n",
            "config_filename": "wg0.conf",
            "wireguard_private_key": "OVERRIDEKEY",
            "openvpn_password": "p@ss",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["mode"] == "custom"
    assert body["has_config"] is True and body["config_filename"] == "wg0.conf"
    assert body["has_wireguard_private_key"] is True
    assert body["has_openvpn_password"] is True and body["has_openvpn_user"] is False
    assert body["problem"] is None
    assert "SECRETKEY" not in resp.text and "OVERRIDEKEY" not in resp.text

    from server.security import decrypt_secret
    db = SessionLocal()
    try:
        g = db.scalar(select(VpnProfile))
        assert g.config_file.startswith("enc:v1:")
        assert decrypt_secret(g.config_file) == "[Interface]\nPrivateKey=SECRETKEY\n"
        assert decrypt_secret(g.wireguard_private_key) == "OVERRIDEKEY"
    finally:
        db.close()


def test_two_keys_for_the_same_provider(client):
    setup_admin(client)
    for name, key, addr in (
        ("Mullvad SE", "KEY-A", "10.64.0.2/32"),
        ("Mullvad CH", "KEY-B", "10.64.0.3/32"),
    ):
        resp = client.post(
            VPN,
            json={
                "name": name, "mode": "provider", "provider": "mullvad", "vpn_type": "wireguard",
                "wireguard_private_key": key, "wireguard_addresses": addr,
                "server_countries": " Sweden ,, ", "server_cities": "",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["provider_label"] == "Mullvad"
        assert body["server_countries"] == "Sweden"
        assert body["server_cities"] is None
        assert body["problem"] is None
        assert key not in resp.text
    assert [p["name"] for p in client.get(VPN).json()] == ["Mullvad SE", "Mullvad CH"]


def test_provider_profile_validation(client):
    setup_admin(client)
    # Unknown provider, missing provider, unsupported type.
    assert client.post(VPN, json={"name": "a", "mode": "provider", "provider": "nope"}).status_code == 400
    assert client.post(VPN, json={"name": "a", "mode": "provider"}).status_code == 400
    resp = client.post(
        VPN, json={"name": "a", "mode": "provider", "provider": "mullvad", "vpn_type": "openvpn"}
    )
    assert resp.status_code == 400 and "wireguard" in resp.json()["detail"]
    # Filters can't carry control characters / newlines into the environment.
    resp = client.post(
        VPN,
        json={"name": "a", "mode": "provider", "provider": "nordvpn", "vpn_type": "wireguard",
              "server_countries": "Netherlands\nFOO=bar"},
    )
    assert resp.status_code == 400, resp.text
    # Missing pieces are reported on the profile, not rejected (add them later).
    resp = client.post(
        VPN,
        json={"name": "a", "mode": "provider", "provider": "mullvad", "vpn_type": "wireguard",
              "wireguard_private_key": "K"},
    )
    assert resp.status_code == 201 and resp.json()["problem"] == "no WireGuard address"
    resp = client.post(
        VPN, json={"name": "b", "mode": "provider", "provider": "nordvpn", "vpn_type": "openvpn"}
    )
    assert resp.json()["problem"] == "no OpenVPN username"
    assert client.post(VPN, json={"name": "c", "vpn_type": "ipsec"}).status_code == 400


def test_vpn_sentinel_leaves_config_unchanged(client):
    setup_admin(client)
    pid = client.post(
        VPN, json={"name": "a", "config_file": "abc", "config_filename": "a.ovpn"}
    ).json()["id"]
    client.put(f"{VPN}/{pid}", json={"name": "renamed"})
    assert client.get(VPN).json()[0]["has_config"] is True
    resp = client.put(f"{VPN}/{pid}", json={"config_file": ""})
    assert resp.json()["has_config"] is False
    assert resp.json()["problem"] == "no config file uploaded"


def test_profile_in_use_cannot_be_deleted(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    keep = client.post(VPN, json={"name": "keep", "config_file": "C"}).json()["id"]
    used = client.post(VPN, json={"name": "used", "config_file": "C"}).json()["id"]
    ws = client.post(
        "/api/workspaces",
        json={"name": "vpn-ws", "image_id": image_id, "use_gluetun": True, "gluetun_profile_id": used},
    )
    assert ws.status_code == 201, ws.text
    assert ws.json()["gluetun_profile_id"] == used
    assert ws.json()["routing_profile_name"] == "used"
    listed = {p["name"]: p for p in client.get(VPN).json()}
    assert listed["used"]["used_by"] == ["vpn-ws"]
    assert listed["used"]["active_in"] == "vpn-ws"
    assert listed["keep"]["used_by"] == []

    resp = client.delete(f"{VPN}/{used}")
    assert resp.status_code == 409 and "vpn-ws" in resp.json()["detail"]
    assert client.delete(f"{VPN}/{keep}").status_code == 204

    # Once the workspace stops routing through it, it can go; the stale id is cleared.
    db = SessionLocal()
    try:
        w = db.get(Workspace, ws.json()["id"])
        w.status = "stopped"
        w.use_gluetun = False
        db.commit()
    finally:
        db.close()
    assert client.delete(f"{VPN}/{used}").status_code == 204
    db = SessionLocal()
    try:
        assert db.get(Workspace, ws.json()["id"]).gluetun_profile_id is None
    finally:
        db.close()
