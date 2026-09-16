"""A workspace's type follows its image, and desktop-only features (proot-apps,
AppImage launchers) are refused everywhere else."""

from sqlalchemy import select

import server.routers.images as images_router
from server.db import SessionLocal
from server.models import Workspace, WorkspaceImage
from server.tests.helpers import add_image, auth_header, setup_admin


def _set(model, row_id, **fields):
    db = SessionLocal()
    try:
        row = db.get(model, row_id)
        for k, v in fields.items():
            setattr(row, k, v)
        db.commit()
    finally:
        db.close()


def test_kind_prefers_the_image_type():
    ws = Workspace(workspace_type="desktop")
    assert ws.kind == "desktop"  # no image loaded: the stored copy
    ws.image = WorkspaceImage(image_type="app")
    assert ws.kind == "app"


def test_type_follows_the_image_not_the_stale_copy(client, fake_docker_manager):
    """A workspace created while its image was mistyped as a desktop reports the
    image's corrected type — and loses desktop-only features with it."""
    setup_admin(client)
    image_id = add_image(name="VSCodium", docker_image="lscr.io/linuxserver/vscodium:latest")
    body = {"name": "code", "image_id": image_id, "proot_apps": "firefox"}
    ws = client.post("/api/workspaces", json=body).json()
    assert ws["workspace_type"] == "desktop"

    _set(WorkspaceImage, image_id, image_type="app")
    _set(Workspace, ws["id"], status="running", container_id="c1")
    assert client.get(f"/api/workspaces/{ws['id']}").json()["workspace_type"] == "app"
    assert client.get(f"/api/workspaces/{ws['id']}/proot-apps").status_code == 400
    fake_docker_manager.proot_command.assert_not_called()


def test_create_refuses_launchers_for_non_desktops(client, fake_docker_manager):
    setup_admin(client)
    app_img = add_image(name="Krita", image_type="app")
    browser_img = add_image(name="Chromium", image_type="browser", url_env="CHROME_CLI")
    for img in (app_img, browser_img):
        for field, value in (("proot_apps", "firefox"), ("appimages", "https://x.io/A.AppImage")):
            resp = client.post("/api/workspaces", json={"name": f"w{img}{field}", "image_id": img, field: value})
            assert resp.status_code == 400, (img, field)
    # Packages stay allowed on an app image (usable from its terminal).
    ok = client.post("/api/workspaces", json={"name": "krita", "image_id": app_img, "install_packages": "git"})
    assert ok.status_code == 201, ok.text


def test_edit_can_clear_but_not_set_launchers_on_an_app(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Code", image_type="app")
    ws_id = client.post("/api/workspaces", json={"name": "code", "image_id": image_id}).json()["id"]
    # A row from before the rule still carrying them.
    _set(Workspace, ws_id, status="stopped", proot_apps="firefox", appimages="https://x.io/A.AppImage")

    assert client.patch(f"/api/workspaces/{ws_id}", json={"proot_apps": "gimp"}).status_code == 400
    cleared = client.patch(f"/api/workspaces/{ws_id}", json={"proot_apps": "", "appimages": "", "name": "code2"})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["proot_apps"] is None and cleared.json()["appimages"] is None


def test_clone_onto_an_app_image_drops_launchers(client, fake_docker_manager):
    setup_admin(client)
    desk = add_image(name="Ubuntu", image_type="desktop")
    app = add_image(name="VSCodium", image_type="app")
    body = {"name": "src", "image_id": desk, "proot_apps": "firefox", "install_packages": "git"}
    src = client.post("/api/workspaces", json=body).json()
    _set(Workspace, src["id"], status="stopped")
    clone = client.post(f"/api/workspaces/{src['id']}/clone", json={"name": "copy", "image_id": app}).json()
    assert clone["proot_apps"] is None
    assert clone["install_packages"] == "git"


def test_catalog_sync_corrects_an_app_seeded_as_desktop(client, monkeypatch):
    token, _ = setup_admin(client)
    add_image(name="VSCodium", docker_image="lscr.io/linuxserver/vscodium:latest", image_type="desktop")
    add_image(name="Custom", docker_image="example/custom:latest", image_type="desktop")

    async def fake_fetch():
        return [{"name": "vscodium", "deprecated": False, "description": "code", "project_logo": None, "tags": []}]

    monkeypatch.setattr(images_router, "fetch_linuxserver_images", fake_fetch)
    resp = client.post("/api/images/sync", headers=auth_header(token))
    assert resp.status_code == 200, resp.text
    db = SessionLocal()
    try:
        types = {r.name: r.image_type for r in db.scalars(select(WorkspaceImage)).all()}
    finally:
        db.close()
    assert types["VSCodium"] == "app"
    assert types["Custom"] == "desktop"  # not curated: left alone


# ── in-container permissions ───────────────────────────────────────────────────

def test_browser_workspaces_get_no_sudo_or_ssh_key(client, fake_docker_manager):
    """A browser runs one kiosk-ish program with no terminal: an injected key has
    nothing to use it, and sudo would only drop no-new-privileges."""
    setup_admin(client)
    browser = add_image(name="Chromium", image_type="browser", url_env="CHROME_CLI")
    body = {"name": "b", "image_id": browser, "allow_sudo": True, "inject_ssh_key": True}
    ws = client.post("/api/workspaces", json=body).json()
    assert ws["allow_sudo"] is False
    assert ws["inject_ssh_key"] is False

    # And they can't be switched on later either.
    _set(Workspace, ws["id"], status="stopped")
    edited = client.patch(f"/api/workspaces/{ws['id']}", json={"allow_sudo": True, "inject_ssh_key": True})
    assert edited.status_code == 200, edited.text
    assert edited.json()["allow_sudo"] is False
    assert edited.json()["inject_ssh_key"] is False


def test_desktops_and_apps_keep_them(client, fake_docker_manager):
    setup_admin(client)
    for kind in ("desktop", "app"):
        image = add_image(name=f"Img {kind}", image_type=kind)
        ws = client.post(
            "/api/workspaces",
            json={"name": f"w-{kind}", "image_id": image, "allow_sudo": True, "inject_ssh_key": True},
        ).json()
        assert ws["allow_sudo"] is True, kind
        assert ws["inject_ssh_key"] is True, kind


def test_launch_never_drops_hardening_for_a_browser(client, fake_docker_manager):
    """Older rows may still carry allow_sudo; the launch path applies the rule."""
    from server.docker_manager import DockerManager

    ws = Workspace(workspace_type="browser", allow_sudo=True, inject_ssh_key=True)
    ws.image = WorkspaceImage(image_type="browser", url_env="CHROME_CLI")
    assert ws.takes_permissions is False
    hardening = DockerManager._build_hardening(
        no_new_privileges_setting=False, allow_sudo=ws.allow_sudo and ws.takes_permissions
    )
    assert "no-new-privileges:true" in hardening["security_opt"]
