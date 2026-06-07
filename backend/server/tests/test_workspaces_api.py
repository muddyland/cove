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


def test_browser_workspace_rejects_whitespace_in_target_url(client, fake_docker_manager):
    """A space in target_url would inject extra browser CLI flags — reject it."""
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
            "name": "inj",
            "image_id": image_id,
            "target_url": "https://x.io/ --proxy-server=evil:8080",
        },
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


def test_create_persists_lan_access_flag(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    # Default off.
    d = client.post("/api/workspaces", json={"name": "plain", "image_id": image_id}).json()
    assert d["lan_access"] is False
    # Explicitly opted in.
    e = client.post(
        "/api/workspaces",
        json={"name": "lan-ws", "image_id": image_id, "lan_access": True},
    ).json()
    assert e["lan_access"] is True
    db = SessionLocal()
    try:
        assert db.get(Workspace, e["id"]).lan_access is True
    finally:
        db.close()


def test_lan_policy_reflects_admin_settings(client, fake_docker_manager):
    admin_token, _ = setup_admin(client)
    # Default: disabled, no subnets.
    pol = client.get("/api/workspaces/lan-policy").json()
    assert pol == {"enabled": False, "subnets": []}
    # After the admin enables it and configures ranges, a normal user sees them.
    client.put(
        "/api/admin/settings",
        json={"workspace_lan_access": True, "workspace_lan_subnets": "10.12.0.0/24 192.168.0.0/16"},
    )
    create_user_via_admin(client, admin_token, "alice")
    client.cookies.clear()
    alice_token = login(client, "alice", "password123").json()["access_token"]
    pol = client.get("/api/workspaces/lan-policy", headers=auth_header(alice_token)).json()
    assert pol == {"enabled": True, "subnets": ["10.12.0.0/24", "192.168.0.0/16"]}


def test_create_kiosk_flag(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(
        name="Chromium", image_type="browser", url_env="CHROME_CLI"
    )
    # Default is off.
    d = client.post("/api/workspaces", json={"name": "plain", "image_id": image_id}).json()
    assert d["kiosk"] is False
    # Explicitly enabled, with dark mode + right-click menu.
    k = client.post(
        "/api/workspaces",
        json={
            "name": "kioskws",
            "image_id": image_id,
            "kiosk": True,
            "kiosk_dark": True,
            "kiosk_menu": True,
            "target_url": "https://x.io",
        },
    ).json()
    assert k["kiosk"] is True
    assert k["kiosk_dark"] is True
    assert k["kiosk_menu"] is True


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


# ── start / recovery from error ───────────────────────────────────────────────

def _set_status(ws_id: int, status: str):
    db = SessionLocal()
    try:
        row = db.get(Workspace, ws_id)
        row.status = status
        row.error_message = "boom" if status == "error" else None
        db.commit()
    finally:
        db.close()


def test_start_recovers_errored_workspace(client):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post("/api/workspaces", json={"name": "rec", "image_id": image_id}).json()
    _set_status(ws["id"], "error")

    resp = client.post(f"/api/workspaces/{ws['id']}/start")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "creating"
    assert body["error_message"] is None


def test_start_allowed_from_stopped(client):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post("/api/workspaces", json={"name": "st", "image_id": image_id}).json()
    _set_status(ws["id"], "stopped")
    resp = client.post(f"/api/workspaces/{ws['id']}/start")
    assert resp.status_code == 200, resp.text


def test_start_rejected_when_already_running(client):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post("/api/workspaces", json={"name": "run", "image_id": image_id}).json()
    _set_status(ws["id"], "running")
    resp = client.post(f"/api/workspaces/{ws['id']}/start")
    assert resp.status_code == 400, resp.text


def test_create_rejects_unsafe_package_tokens(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    cases = [
        ("install_packages", "vim; rm -rf /"),
        ("proot_apps", "firefox|evil"),
        ("appimages", "https://x.io/A.AppImage\n; touch pwned"),
    ]
    for field, val in cases:
        resp = client.post(
            "/api/workspaces", json={"name": "x", "image_id": image_id, field: val}
        )
        assert resp.status_code == 400, (field, resp.text)


def test_create_accepts_clean_package_tokens(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    resp = client.post(
        "/api/workspaces",
        json={
            "name": "ok",
            "image_id": image_id,
            "install_packages": "git vim htop",
            "proot_apps": "obs-studio firefox",
            "appimages": "https://x.io/A.AppImage",
        },
    )
    assert resp.status_code == 201, resp.text


def _mark_stopped(ws_id: int) -> None:
    db = SessionLocal()
    try:
        db.get(Workspace, ws_id).status = "stopped"
        db.commit()
    finally:
        db.close()


def test_clone_copies_config_and_schedules_launch(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    src = client.post(
        "/api/workspaces",
        json={"name": "orig", "image_id": image_id, "allow_sudo": True, "install_packages": "git vim"},
    ).json()
    _mark_stopped(src["id"])

    resp = client.post(f"/api/workspaces/{src['id']}/clone", json={"name": "orig copy"})
    assert resp.status_code == 201, resp.text
    clone = resp.json()
    assert clone["id"] != src["id"]
    assert clone["name"] == "orig copy"
    assert clone["install_packages"] == "git vim"
    assert clone["allow_sudo"] is True
    assert clone["status"] == "creating"
    fake_docker_manager.clone_and_launch.assert_called_once_with(src["id"], clone["id"])


def test_clone_requires_source_stopped(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    src = client.post("/api/workspaces", json={"name": "orig", "image_id": image_id}).json()
    # Fresh workspace is "creating" (not stopped) -> refuse.
    resp = client.post(f"/api/workspaces/{src['id']}/clone", json={"name": "copy"})
    assert resp.status_code == 409, resp.text


def test_clone_rejects_same_name(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    src = client.post("/api/workspaces", json={"name": "Orig", "image_id": image_id}).json()
    _mark_stopped(src["id"])
    # Sanitizes to the same storage dir -> refuse.
    resp = client.post(f"/api/workspaces/{src['id']}/clone", json={"name": "orig"})
    assert resp.status_code == 400, resp.text


def test_clone_can_switch_image(client, fake_docker_manager):
    setup_admin(client)
    src_img = add_image(name="Ubuntu", image_type="desktop")
    other_img = add_image(name="Fedora", image_type="desktop")
    src = client.post("/api/workspaces", json={"name": "orig", "image_id": src_img}).json()
    _mark_stopped(src["id"])
    resp = client.post(
        f"/api/workspaces/{src['id']}/clone", json={"name": "on fedora", "image_id": other_img}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["image_id"] == other_img


def test_create_persists_ephemeral_flag(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Chromium", image_type="browser", url_env="CHROME_CLI")
    d = client.post("/api/workspaces", json={"name": "plain", "image_id": image_id}).json()
    assert d["ephemeral"] is False
    e = client.post(
        "/api/workspaces", json={"name": "eph", "image_id": image_id, "ephemeral": True}
    ).json()
    assert e["ephemeral"] is True
    db = SessionLocal()
    try:
        assert db.get(Workspace, e["id"]).ephemeral is True
    finally:
        db.close()


def _configure_gluetun(client, vpn_type="openvpn"):
    resp = client.put(
        "/api/users/me/gluetun",
        json={"enabled": True, "vpn_type": vpn_type, "config_file": "CONFIG", "config_filename": "x.conf"},
    )
    assert resp.status_code == 200, resp.text


def test_create_with_gluetun_requires_config(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    # No gluetun config yet -> 400.
    resp = client.post(
        "/api/workspaces", json={"name": "g", "image_id": image_id, "use_gluetun": True}
    )
    assert resp.status_code == 400, resp.text


def test_create_with_gluetun_configured_accepted(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    _configure_gluetun(client)
    resp = client.post(
        "/api/workspaces", json={"name": "g", "image_id": image_id, "use_gluetun": True}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["use_gluetun"] is True


def test_create_rejects_tailscale_and_gluetun_together(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    _configure_gluetun(client)
    client.put("/api/users/me/tailscale", json={"auth_key": "tskey-x", "enabled": True})
    resp = client.post(
        "/api/workspaces",
        json={"name": "both", "image_id": image_id, "use_gluetun": True, "use_tailscale": True},
    )
    assert resp.status_code == 400, resp.text
    assert "not both" in resp.json()["detail"]


def test_gluetun_single_connection_enforced(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    _configure_gluetun(client)
    # First gluetun workspace is accepted (status 'creating' -> counts as active).
    first = client.post(
        "/api/workspaces", json={"name": "g1", "image_id": image_id, "use_gluetun": True}
    )
    assert first.status_code == 201, first.text
    # Second is refused while the first is active.
    second = client.post(
        "/api/workspaces", json={"name": "g2", "image_id": image_id, "use_gluetun": True}
    )
    assert second.status_code == 409, second.text
    assert "one connection" in second.json()["detail"]

    # Stop the first; a second may then start.
    db = SessionLocal()
    try:
        db.get(Workspace, first.json()["id"]).status = "stopped"
        db.commit()
    finally:
        db.close()
    third = client.post(
        "/api/workspaces", json={"name": "g3", "image_id": image_id, "use_gluetun": True}
    )
    assert third.status_code == 201, third.text
