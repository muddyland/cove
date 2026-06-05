"""API tests for the admin router: app settings and username validation."""

from server.tests.helpers import (
    auth_header,
    create_user_via_admin,
    login,
    setup_admin,
)

# ── App settings ────────────────────────────────────────────────────────────

def test_settings_defaults(client):
    setup_admin(client)
    resp = client.get("/api/admin/settings")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "tailscale_image": "tailscale/tailscale:latest",
        "workspace_lan_access": False,
    }


def test_settings_put_updates_both(client):
    setup_admin(client)
    resp = client.put(
        "/api/admin/settings",
        json={"tailscale_image": "tailscale/tailscale:v1.2", "workspace_lan_access": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "tailscale_image": "tailscale/tailscale:v1.2",
        "workspace_lan_access": True,
    }
    # Persisted across requests.
    got = client.get("/api/admin/settings").json()
    assert got["tailscale_image"] == "tailscale/tailscale:v1.2"
    assert got["workspace_lan_access"] is True


def test_settings_put_partial(client):
    setup_admin(client)
    client.put("/api/admin/settings", json={"workspace_lan_access": True})
    got = client.get("/api/admin/settings").json()
    # tailscale_image keeps its default; lan toggled.
    assert got["tailscale_image"] == "tailscale/tailscale:latest"
    assert got["workspace_lan_access"] is True


def test_settings_non_admin_forbidden(client):
    setup_admin(client)
    create_user_via_admin(client, _admin_token(client), "bob")
    client.cookies.clear()
    bob = login(client, "bob", "password123").json()["access_token"]
    client.cookies.clear()
    resp = client.get("/api/admin/settings", headers=auth_header(bob))
    assert resp.status_code == 403
    resp2 = client.put(
        "/api/admin/settings",
        json={"workspace_lan_access": True},
        headers=auth_header(bob),
    )
    assert resp2.status_code == 403


def _admin_token(client):
    # Re-login as admin to get a clean bearer token regardless of cookie state.
    client.cookies.clear()
    tok = login(client, "admin", "password123").json()["access_token"]
    return tok


# ── Username validation ──────────────────────────────────────────────────────

def test_setup_rejects_bad_username(client):
    for bad in ("bad/name", "..", "a/b", "."):
        resp = client.post(
            "/api/auth/setup", json={"username": bad, "password": "password123"}
        )
        assert resp.status_code == 400, (bad, resp.text)


def test_admin_create_rejects_bad_username(client):
    setup_admin(client)
    for bad in ("bad/name", "..", "a/b"):
        resp = client.post(
            "/api/admin/users",
            json={"username": bad, "password": "password123"},
        )
        assert resp.status_code == 400, (bad, resp.text)


def test_admin_create_accepts_valid_username(client):
    setup_admin(client)
    resp = client.post(
        "/api/admin/users",
        json={"username": "valid.user_name-1", "password": "password123"},
    )
    assert resp.status_code == 201, resp.text


def test_admin_update_rejects_duplicate_username(client):
    setup_admin(client)
    alice = create_user_via_admin(client, _admin_token(client), "alice")
    create_user_via_admin(client, _admin_token(client), "bob")
    # Try to rename alice -> bob (already taken).
    resp = client.patch(
        f"/api/admin/users/{alice['id']}",
        json={"username": "bob"},
    )
    assert resp.status_code == 409, resp.text


def test_admin_update_rejects_invalid_username(client):
    setup_admin(client)
    alice = create_user_via_admin(client, _admin_token(client), "alice")
    resp = client.patch(
        f"/api/admin/users/{alice['id']}",
        json={"username": "a/b"},
    )
    assert resp.status_code == 400, resp.text
