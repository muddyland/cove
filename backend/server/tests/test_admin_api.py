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
        "gluetun_image": "qmcgaw/gluetun:latest",
        "workspace_lan_access": False,
        "workspace_lan_subnets": "",
        "workspace_no_new_privileges": False,
        "workspace_max_runtime_hours": 24,
        "workspace_cpu_limit": 0.0,
        "workspace_memory_limit_mb": 0,
        "workspace_gpu_accel": False,
        "workspace_gpu_render_node": "/dev/dri/renderD128",
        "workspace_gpu_render_gid": 992,
        "workspace_docker": False,
        "dind_image": "docker:dind",
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
        "gluetun_image": "qmcgaw/gluetun:latest",
        "workspace_lan_access": True,
        "workspace_lan_subnets": "",
        "workspace_no_new_privileges": False,
        "workspace_max_runtime_hours": 24,
        "workspace_cpu_limit": 0.0,
        "workspace_memory_limit_mb": 0,
        "workspace_gpu_accel": False,
        "workspace_gpu_render_node": "/dev/dri/renderD128",
        "workspace_gpu_render_gid": 992,
        "workspace_docker": False,
        "dind_image": "docker:dind",
    }
    # Persisted across requests.
    got = client.get("/api/admin/settings").json()
    assert got["tailscale_image"] == "tailscale/tailscale:v1.2"
    assert got["workspace_lan_access"] is True


def test_settings_cpu_and_memory_limits(client):
    setup_admin(client)
    resp = client.put(
        "/api/admin/settings",
        json={"workspace_cpu_limit": 2.5, "workspace_memory_limit_mb": 4096},
    )
    assert resp.status_code == 200, resp.text
    got = resp.json()
    assert got["workspace_cpu_limit"] == 2.5
    assert got["workspace_memory_limit_mb"] == 4096


def test_settings_gpu_accel(client):
    setup_admin(client)
    resp = client.put(
        "/api/admin/settings",
        json={
            "workspace_gpu_accel": True,
            "workspace_gpu_render_node": "/dev/dri/renderD129",
            "workspace_gpu_render_gid": 44,
        },
    )
    assert resp.status_code == 200, resp.text
    got = resp.json()
    assert got["workspace_gpu_accel"] is True
    assert got["workspace_gpu_render_node"] == "/dev/dri/renderD129"
    assert got["workspace_gpu_render_gid"] == 44
    # Blank render node falls back to the default rather than persisting empty.
    resp = client.put("/api/admin/settings", json={"workspace_gpu_render_node": "  "})
    assert resp.json()["workspace_gpu_render_node"] == "/dev/dri/renderD128"
    # Persisted + negatives are clamped to 0 (unlimited).
    client.put("/api/admin/settings", json={"workspace_cpu_limit": -3, "workspace_memory_limit_mb": -1})
    got = client.get("/api/admin/settings").json()
    assert got["workspace_cpu_limit"] == 0.0
    assert got["workspace_memory_limit_mb"] == 0


def test_settings_docker_toggle(client):
    setup_admin(client)
    # Off by default.
    assert client.get("/api/admin/settings").json()["workspace_docker"] is False
    resp = client.put(
        "/api/admin/settings",
        json={"workspace_docker": True, "dind_image": "docker:27-dind"},
    )
    assert resp.status_code == 200, resp.text
    got = resp.json()
    assert got["workspace_docker"] is True
    assert got["dind_image"] == "docker:27-dind"
    # Blank image falls back to the default rather than persisting empty.
    resp = client.put("/api/admin/settings", json={"dind_image": "  "})
    assert resp.json()["dind_image"] == "docker:dind"
    # Persisted across requests.
    assert client.get("/api/admin/settings").json()["workspace_docker"] is True


def test_settings_lan_subnets_validates_and_normalizes(client):
    setup_admin(client)
    # Mixed valid/invalid: valid IPv4 CIDRs are normalized+deduped; bare IPs get
    # a /32; garbage and IPv6 are dropped.
    resp = client.put(
        "/api/admin/settings",
        json={"workspace_lan_subnets": "10.12.0.0/24, 192.168.1.5, nonsense, fd00::/8, 10.12.0.0/24"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["workspace_lan_subnets"] == "10.12.0.0/24, 192.168.1.5/32"
    # Persisted.
    got = client.get("/api/admin/settings").json()
    assert got["workspace_lan_subnets"] == "10.12.0.0/24, 192.168.1.5/32"


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


# ── SSO (OIDC) account password guards ────────────────────────────────────────

def _make_oidc_user(username="ssouser", is_admin=False):
    """Insert an OIDC-provisioned user directly and return its id."""
    from server.db import SessionLocal
    from server.models import User

    db = SessionLocal()
    try:
        u = User(
            username=username,
            auth_provider="oidc",
            oidc_sub=f"sub-{username}",
            is_admin=is_admin,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def test_admin_cannot_set_password_on_oidc_user(client):
    setup_admin(client)
    uid = _make_oidc_user()
    resp = client.patch(
        f"/api/admin/users/{uid}",
        json={"password": "newpassword123"},
    )
    assert resp.status_code == 400, resp.text
    assert "SSO" in resp.json()["detail"]


def test_self_service_change_password_rejects_oidc_user(client):
    from server.security import create_access_token

    setup_admin(client)
    uid = _make_oidc_user("ssoself")
    client.cookies.clear()
    tok = create_access_token(uid, False)
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": "x", "new_password": "newpassword123"},
        headers=auth_header(tok),
    )
    assert resp.status_code == 400, resp.text
    assert "SSO" in resp.json()["detail"]


def test_create_user_blocked_in_oidc_only(client, monkeypatch):
    from types import SimpleNamespace

    token, _ = setup_admin(client)
    # Flip the app into OIDC-only AFTER setup (setup itself is gated on it too).
    monkeypatch.setattr(
        "server.routers.admin.get_settings",
        lambda: SimpleNamespace(oidc_only_active=True),
    )
    resp = client.post(
        "/api/admin/users",
        json={"username": "newbie", "password": "password123", "is_admin": False},
        headers=auth_header(token),
    )
    assert resp.status_code == 400, resp.text
    assert "OIDC-only" in resp.json()["detail"]


def test_admin_cannot_delete_self(client):
    token, _ = setup_admin(client)
    me = client.get("/api/auth/me", headers=auth_header(token)).json()
    resp = client.delete(f"/api/admin/users/{me['id']}", headers=auth_header(token))
    assert resp.status_code == 400, resp.text
    assert "your own account" in resp.json()["detail"]


def test_cannot_demote_last_admin(client):
    token, _ = setup_admin(client)
    me = client.get("/api/auth/me", headers=auth_header(token)).json()
    resp = client.patch(
        f"/api/admin/users/{me['id']}",
        json={"is_admin": False},
        headers=auth_header(token),
    )
    assert resp.status_code == 400, resp.text
    assert "last admin" in resp.json()["detail"]


def test_can_demote_admin_when_another_exists(client):
    token, _ = setup_admin(client)
    create_user_via_admin(client, token, "admin2", is_admin=True)
    me = client.get("/api/auth/me", headers=auth_header(token)).json()
    resp = client.patch(
        f"/api/admin/users/{me['id']}",
        json={"is_admin": False},
        headers=auth_header(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_admin"] is False


# ── Storage ─────────────────────────────────────────────────────────────────

def test_storage_lists_local_zone_once(client, monkeypatch):
    """Zone 0 is both seeded as an enrolled Zone row (migration 0029) AND added
    explicitly by the storage endpoint. It must appear once, not twice."""
    from unittest.mock import MagicMock

    setup_admin(client)

    mgr = MagicMock()
    mgr.disk_usage.return_value = {"categories": []}
    monkeypatch.setattr(
        "server.docker_manager.get_docker_manager", lambda *a, **k: mgr
    )
    monkeypatch.setattr("server.routers.admin._host_disk_local", lambda: None)

    resp = client.get("/api/admin/storage")
    assert resp.status_code == 200, resp.text
    zones = resp.json()["zones"]
    local = [z for z in zones if z["zone_name"] == "Local"]
    assert len(local) == 1, f"'Local' listed {len(local)}x: {zones}"
    assert local[0]["zone_id"] == 0
