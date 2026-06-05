"""Tests for the users router (per-user Tailscale config)."""

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
