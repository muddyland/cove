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
        "workspace_no_new_privileges": False,
        "workspace_max_runtime_hours": 24,
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
        "workspace_no_new_privileges": False,
        "workspace_max_runtime_hours": 24,
    }
    # Persisted across requests.
    got = client.get("/api/admin/settings").json()
    assert got["tailscale_image"] == "tailscale/tailscale:v1.2"
    assert got["workspace_lan_access"] is True


def test_settings_no_new_privileges_toggle(client):
    setup_admin(client)
    assert client.get("/api/admin/settings").json()["workspace_no_new_privileges"] is False
    client.put("/api/admin/settings", json={"workspace_no_new_privileges": True})
    assert client.get("/api/admin/settings").json()["workspace_no_new_privileges"] is True


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


# ── Env summary ──────────────────────────────────────────────────────────────

def test_env_summary_returns_entries(client):
    setup_admin(client)
    resp = client.get("/api/admin/env")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "entries" in body
    names = {e["name"] for e in body["entries"]}
    assert "COVE_WORKSPACE_DOMAIN" in names
    assert "COVE_COOKIE_SECURE" in names
    assert "OIDC enabled" in names
    assert "DB encryption" in names
    # Default (no workspace_domain) shows the subpath hint.
    domain = next(e for e in body["entries"] if e["name"] == "COVE_WORKSPACE_DOMAIN")
    assert domain["value"] == "(unset — subpath routing)"


def test_env_summary_never_leaks_secrets(client, monkeypatch):
    from server.config import get_settings

    # Force secret-bearing settings, then make sure none leak.
    s = get_settings()
    monkeypatch.setattr(s, "oidc_client_secret", "super-secret-value", raising=False)
    monkeypatch.setattr(s, "db_encryption_key", "db-secret-key", raising=False)

    setup_admin(client)
    resp = client.get("/api/admin/env")
    assert resp.status_code == 200, resp.text
    text = resp.text
    assert "super-secret-value" not in text
    assert "db-secret-key" not in text
    assert "client_secret" not in text
    # Presence still reported.
    db_enc = next(e for e in resp.json()["entries"] if e["name"] == "DB encryption")
    assert db_enc["value"] == "configured"


def test_env_summary_non_admin_forbidden(client):
    setup_admin(client)
    create_user_via_admin(client, _admin_token(client), "bob")
    client.cookies.clear()
    bob = login(client, "bob", "password123").json()["access_token"]
    client.cookies.clear()
    resp = client.get("/api/admin/env", headers=auth_header(bob))
    assert resp.status_code == 403


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


def test_settings_max_runtime_hours(client):
    setup_admin(client)
    assert client.get("/api/admin/settings").json()["workspace_max_runtime_hours"] == 24
    client.put("/api/admin/settings", json={"workspace_max_runtime_hours": 8})
    assert client.get("/api/admin/settings").json()["workspace_max_runtime_hours"] == 8
