"""Tests for the workspaces router (create validation + ownership).

Docker is fully stubbed by the ``client`` fixture (``get_docker_manager`` is
monkeypatched to a MagicMock), so creating a workspace schedules a harmless
background task that never touches a real daemon.
"""

from server.db import SessionLocal
from server.models import Workspace
from server.tests.helpers import (
    add_image,
    auth_header,
    create_user_via_admin,
    login,
    setup_admin,
)


def test_desktop_workspace_launches(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")

    resp = client.post("/api/workspaces", json={"name": "my-desk", "image_id": image_id})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "creating"
    assert body["workspace_type"] == "desktop"
    assert body["target_url"] is None

    # The launch was scheduled on the (mocked) docker manager as a background task.
    fake_docker_manager.launch_workspace.assert_called_once_with(body["id"])


def test_browser_workspace_accepts_and_stores_target_url(client):
    setup_admin(client)
    image_id = add_image(
        name="Chromium",
        docker_image="lscr.io/linuxserver/chromium:latest",
        image_type="browser",
        url_env="CHROME_CLI",
    )

    resp = client.post(
        "/api/workspaces",
        json={
            "name": "browse",
            "image_id": image_id,
            "target_url": "https://example.com/",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["target_url"] == "https://example.com/"
    assert body["workspace_type"] == "browser"

    # Persisted to the DB too.
    db = SessionLocal()
    try:
        ws = db.get(Workspace, body["id"])
        assert ws is not None
        assert ws.target_url == "https://example.com/"
    finally:
        db.close()


def test_browser_workspace_rejects_invalid_target_url(client):
    setup_admin(client)
    image_id = add_image(
        name="Chromium",
        docker_image="lscr.io/linuxserver/chromium:latest",
        image_type="browser",
        url_env="CHROME_CLI",
    )

    resp = client.post(
        "/api/workspaces",
        json={"name": "bad", "image_id": image_id, "target_url": "ftp://x"},
    )
    assert resp.status_code == 400, resp.text


def test_desktop_workspace_ignores_target_url(client):
    """A non-url-capable image must not store a startup URL even if supplied."""
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")

    resp = client.post(
        "/api/workspaces",
        json={"name": "d", "image_id": image_id, "target_url": "https://example.com/"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["target_url"] is None


def test_create_workspace_unknown_image_404(client):
    setup_admin(client)
    resp = client.post("/api/workspaces", json={"name": "x", "image_id": 9999})
    assert resp.status_code == 404


def test_create_workspace_disabled_image_404(client):
    setup_admin(client)
    image_id = add_image(name="Disabled", image_type="desktop")
    # Disable it directly.
    db = SessionLocal()
    try:
        from server.models import WorkspaceImage

        img = db.get(WorkspaceImage, image_id)
        img.enabled = False
        db.commit()
    finally:
        db.close()

    resp = client.post("/api/workspaces", json={"name": "x", "image_id": image_id})
    assert resp.status_code == 404


def test_ownership_enforced_other_user_cannot_get(client):
    """User A cannot GET user B's workspace."""
    admin_token, _ = setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")

    # Admin (user A) creates a workspace (session cookie is in the jar).
    created = client.post("/api/workspaces", json={"name": "a-ws", "image_id": image_id})
    assert created.status_code == 201, created.text
    ws_id = created.json()["id"]

    # Create a normal user B and log in as them.
    create_user_via_admin(client, admin_token, "bob")
    client.cookies.clear()
    bob_token = login(client, "bob", "password123").json()["access_token"]

    # Bob tries to read admin's workspace -> 403 (forbidden, exists but not owner).
    resp = client.get(f"/api/workspaces/{ws_id}", headers=auth_header(bob_token))
    assert resp.status_code == 403, resp.text


def test_create_with_tailscale_without_config_400(client):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    resp = client.post(
        "/api/workspaces",
        json={"name": "ts-ws", "image_id": image_id, "use_tailscale": True},
    )
    assert resp.status_code == 400, resp.text
    assert "Tailscale not configured" in resp.text


def test_create_with_tailscale_configured_accepted(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    # Configure Tailscale with a non-empty auth_key.
    cfg = client.put("/api/users/me/tailscale", json={"auth_key": "tskey-abc"})
    assert cfg.status_code == 200, cfg.text

    resp = client.post(
        "/api/workspaces",
        json={"name": "ts-ws", "image_id": image_id, "use_tailscale": True},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["use_tailscale"] is True
    fake_docker_manager.launch_workspace.assert_called_once_with(body["id"])


def test_create_with_tailscale_persists_routing_options(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    cfg = client.put("/api/users/me/tailscale", json={"auth_key": "tskey-abc"})
    assert cfg.status_code == 200, cfg.text

    resp = client.post(
        "/api/workspaces",
        json={
            "name": "ts-ws",
            "image_id": image_id,
            "use_tailscale": True,
            "ts_exit_node": "100.64.0.1",
            "ts_accept_routes": False,
            "ts_accept_dns": False,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ts_exit_node"] == "100.64.0.1"
    assert body["ts_accept_routes"] is False
    assert body["ts_accept_dns"] is False

    # Persisted to the DB.
    db = SessionLocal()
    try:
        ws = db.get(Workspace, body["id"])
        assert ws.ts_exit_node == "100.64.0.1"
        assert ws.ts_accept_routes is False
        assert ws.ts_accept_dns is False
    finally:
        db.close()


def test_create_workspace_ts_routing_defaults(client, fake_docker_manager):
    """Without explicit routing options the defaults are accept_routes/accept_dns on."""
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")

    resp = client.post("/api/workspaces", json={"name": "d", "image_id": image_id})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ts_exit_node"] is None
    assert body["ts_accept_routes"] is True
    assert body["ts_accept_dns"] is True


def test_create_persists_packages_and_sudo(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")

    resp = client.post(
        "/api/workspaces",
        json={
            "name": "pkg-ws",
            "image_id": image_id,
            "install_packages": "htop vim",
            "proot_apps": "firefox, libreoffice",
            "allow_sudo": False,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["install_packages"] == "htop vim"
    assert body["proot_apps"] == "firefox, libreoffice"
    assert body["allow_sudo"] is False

    db = SessionLocal()
    try:
        ws = db.get(Workspace, body["id"])
        assert ws.install_packages == "htop vim"
        assert ws.proot_apps == "firefox, libreoffice"
        assert ws.allow_sudo is False
    finally:
        db.close()


def test_create_packages_defaults(client, fake_docker_manager):
    """Defaults: no packages/apps, sudo disabled (no-new-privileges on)."""
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")

    resp = client.post("/api/workspaces", json={"name": "d", "image_id": image_id})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["install_packages"] is None
    assert body["proot_apps"] is None
    assert body["allow_sudo"] is False


def test_list_workspaces_scoped_to_owner(client):
    admin_token, _ = setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    client.post("/api/workspaces", json={"name": "a-ws", "image_id": image_id})

    create_user_via_admin(client, admin_token, "carol")
    client.cookies.clear()
    carol_token = login(client, "carol", "password123").json()["access_token"]

    # Carol sees none of admin's workspaces.
    resp = client.get("/api/workspaces", headers=auth_header(carol_token))
    assert resp.status_code == 200
    assert resp.json() == []


def test_update_workspace(client):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post("/api/workspaces", json={"name": "orig", "image_id": image_id}).json()
    resp = client.patch(
        f"/api/workspaces/{ws['id']}",
        json={"name": "renamed", "install_packages": "git vim", "allow_sudo": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "renamed"
    assert body["install_packages"] == "git vim"
    assert body["allow_sudo"] is False


def test_update_workspace_ownership(client):
    admin_token, _ = setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post("/api/workspaces", json={"name": "a", "image_id": image_id}).json()
    create_user_via_admin(client, admin_token, "dave")
    client.cookies.clear()
    dave = login(client, "dave", "password123").json()["access_token"]
    resp = client.patch(
        f"/api/workspaces/{ws['id']}", json={"name": "hax"}, headers=auth_header(dave)
    )
    assert resp.status_code in (403, 404)


# ── purge storage ─────────────────────────────────────────────────────────────

def _stopped_ws_with_storage(client, name="purgeme"):
    """Create a workspace, mark it stopped, and give it a populated storage dir."""
    from server.config import get_settings

    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post("/api/workspaces", json={"name": name, "image_id": image_id}).json()
    base = get_settings().data_dir / "workspaces"
    storage = base / "admin" / f"workspace-{name}"
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "marker.txt").write_text("keep me?")
    db = SessionLocal()
    try:
        row = db.get(Workspace, ws["id"])
        row.status = "stopped"
        row.volume_name = str(storage)
        db.commit()
    finally:
        db.close()
    return ws, storage


def test_delete_with_purge_storage_removes_dir(client):
    setup_admin(client)
    ws, storage = _stopped_ws_with_storage(client)
    assert storage.exists()

    resp = client.delete(f"/api/workspaces/{ws['id']}?purge_storage=true")
    assert resp.status_code == 204
    assert not storage.exists()


def test_delete_without_purge_keeps_storage(client):
    setup_admin(client)
    ws, storage = _stopped_ws_with_storage(client)

    resp = client.delete(f"/api/workspaces/{ws['id']}")
    assert resp.status_code == 204
    assert storage.exists()  # persistent home preserved by default


def test_delete_workspace_storage_refuses_outside_base(tmp_path):
    """The purge helper must never delete a path outside the storage base."""
    from types import SimpleNamespace

    from server.docker_manager import delete_workspace_storage

    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "important").write_text("do not delete")
    ws = SimpleNamespace(volume_name=str(victim), user=None, name="x")

    delete_workspace_storage(ws)
    assert victim.exists()  # outside base -> refused
