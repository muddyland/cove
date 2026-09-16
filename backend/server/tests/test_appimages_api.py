"""Managing AppImages inside a running workspace: list, install, update
(the given URL replaces the app), remove.

Cove never fetches these URLs itself — the workspace downloads them, where the
egress guard applies — so what is tested here is validation, the driver argv,
and that the saved list stays in step with what's installed.
"""

import itertools

import pytest

import server.proot as proot_module
import server.routers.proot as proot_router
from server.db import SessionLocal
from server.models import Workspace
from server.tests.helpers import add_image, auth_header, create_user_via_admin, login, setup_admin

URL = "https://apps.example.com/Foo-1.2_x86_64.AppImage"
NEWER = "https://apps.example.com/Foo-1.3_x86_64.AppImage"
_seq = itertools.count()


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(proot_router, "_tasks_cache", {})
    monkeypatch.setattr(proot_router, "_calls", {})


def _ws(client, *, appimages=None):
    n = next(_seq)
    image_id = add_image(name=f"Desk {n}", image_type="desktop")
    ws_id = client.post("/api/workspaces", json={"name": f"w{n}", "image_id": image_id}).json()["id"]
    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        ws.status = "running"
        ws.container_id = f"cove-ws-{ws_id}"
        ws.appimages = appimages
        db.commit()
    finally:
        db.close()
    return ws_id


def _saved(ws_id):
    db = SessionLocal()
    try:
        return db.get(Workspace, ws_id).appimages
    finally:
        db.close()


def _row(slug="Foo-1.2_x86_64", name="Foo", url=URL, size="120", installed="1789500000"):
    return f"APPIMAGE\t{slug}\t{name}\t{url}\t{size}\t{installed}\n".encode()


# ── slug derivation (must match scripts/cove-apps.sh) ─────────────────────────

@pytest.mark.parametrize(
    "url,slug",
    [
        (URL, "Foo-1.2_x86_64"),
        ("https://x.io/App.AppImage?token=abc", "App"),
        ("https://x.io/a/b", "b"),
        ("https://x.io/", "x"),  # basename ignores the trailing slash
        ("https://x.io/.hidden", "appimage"),
        ("https://x.io/UPPER.tar.gz", "UPPER.tar"),
        ("https://x.io/we!rd%20name.AppImage", "we_rd_20name"),
    ],
)
def test_appimage_slug_matches_the_driver(url, slug):
    assert proot_module.appimage_slug(url) == slug


# ── listing ───────────────────────────────────────────────────────────────────

def test_lists_installed_appimages(client, fake_docker_manager):
    setup_admin(client)
    ws_id = _ws(client)
    fake_docker_manager.apps_command.return_value = (
        0, _row() + b"APPIMAGE\tOld\tOld App\t\t40\t1789000000\n",
    )
    body = client.get(f"/api/workspaces/{ws_id}/appimages").json()
    assert body["apps"][0] == {
        "slug": "Foo-1.2_x86_64", "name": "Foo", "url": URL, "size_kb": 120, "installed_at": 1789500000,
    }
    # Installed before Cove recorded provenance: no URL, so updating needs one.
    assert body["apps"][1]["url"] is None
    assert fake_docker_manager.apps_command.call_args.args[1] == ["appimages"]


def test_listing_requires_a_running_desktop(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Browser", image_type="browser", url_env="CHROME_CLI")
    ws_id = client.post("/api/workspaces", json={"name": "b", "image_id": image_id}).json()["id"]
    assert client.get(f"/api/workspaces/{ws_id}/appimages").status_code == 400
    fake_docker_manager.apps_command.assert_not_called()


# ── install / update / remove ─────────────────────────────────────────────────

def test_install_queues_the_urls_and_saves_them(client, fake_docker_manager):
    setup_admin(client)
    ws_id = _ws(client)
    fake_docker_manager.apps_command.return_value = (0, b"")
    resp = client.post(f"/api/workspaces/{ws_id}/appimages/tasks", json={"op": "install", "urls": [URL, URL]})
    assert resp.status_code == 202, resp.text
    task = resp.json()
    assert task["kind"] == "appimage" and task["apps"] == ["Foo-1.2_x86_64"]
    start, run = fake_docker_manager.apps_command.call_args_list
    assert start.args[1] == ["start", task["id"], "appimage", "install", URL]
    assert run.args[1] == ["run", task["id"]] and run.kwargs == {"detach": True}
    assert _saved(ws_id) == URL


def test_update_replaces_the_saved_url_for_that_app(client, fake_docker_manager):
    setup_admin(client)
    ws_id = _ws(client, appimages=f"{URL} https://x.io/Other.AppImage")
    fake_docker_manager.apps_command.return_value = (0, b"")
    resp = client.post(
        f"/api/workspaces/{ws_id}/appimages/tasks",
        json={"op": "update", "slug": "Foo-1.2_x86_64", "url": NEWER},
    )
    assert resp.status_code == 202, resp.text
    start = fake_docker_manager.apps_command.call_args_list[0]
    assert start.args[1][2:] == ["appimage", "update", "Foo-1.2_x86_64", NEWER]
    # The old URL for that app is gone; the unrelated one stays.
    assert _saved(ws_id) == f"https://x.io/Other.AppImage {NEWER}"


def test_remove_drops_the_saved_url(client, fake_docker_manager):
    setup_admin(client)
    ws_id = _ws(client, appimages=f"{URL} https://x.io/Other.AppImage")
    fake_docker_manager.apps_command.return_value = (0, b"")
    resp = client.post(
        f"/api/workspaces/{ws_id}/appimages/tasks", json={"op": "remove", "slugs": ["Foo-1.2_x86_64"]}
    )
    assert resp.status_code == 202, resp.text
    assert fake_docker_manager.apps_command.call_args_list[0].args[1][2:] == [
        "appimage", "remove", "Foo-1.2_x86_64",
    ]
    assert _saved(ws_id) == "https://x.io/Other.AppImage"


def test_busy_workspace_is_429_and_leaves_the_saved_list(client, fake_docker_manager):
    setup_admin(client)
    ws_id = _ws(client, appimages=URL)
    fake_docker_manager.apps_command.return_value = (3, b"")
    resp = client.post(f"/api/workspaces/{ws_id}/appimages/tasks", json={"op": "remove", "slugs": ["Foo-1.2_x86_64"]})
    assert resp.status_code == 429
    assert _saved(ws_id) == URL


# ── validation ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "body",
    [
        {"op": "install", "urls": ["ftp://x.io/a.AppImage"]},
        {"op": "install", "urls": ["javascript:alert(1)"]},
        {"op": "install", "urls": ["https://x.io/a b.AppImage"]},
        {"op": "install", "urls": ["http://x.io/$(id).AppImage"]},
        {"op": "install", "urls": ["https://x.io/a`id`.AppImage"]},
        {"op": "install", "urls": ["https://x.io/a;id.AppImage"]},
        {"op": "install", "urls": ["https://x.io/" + "a" * 4000]},
        {"op": "install", "urls": ["https:///nohost.AppImage"]},
        {"op": "install", "urls": []},
        {"op": "install", "urls": [URL] * 11},
        {"op": "update", "slug": "../../etc", "url": URL},
        {"op": "update", "slug": "Foo", "url": "not-a-url"},
        {"op": "update", "slug": "Foo"},
        {"op": "remove", "slugs": ["../etc"]},
        {"op": "remove", "slugs": []},
        {"op": "run", "urls": [URL]},
    ],
)
def test_rejects_bad_input(client, fake_docker_manager, body):
    setup_admin(client)
    ws_id = _ws(client)
    resp = client.post(f"/api/workspaces/{ws_id}/appimages/tasks", json=body)
    assert resp.status_code in (400, 422), resp.text
    fake_docker_manager.apps_command.assert_not_called()


def test_other_users_cannot_manage_appimages(client, fake_docker_manager):
    admin_token, _ = setup_admin(client)
    ws_id = _ws(client)
    create_user_via_admin(client, admin_token, "bob")
    bob = login(client, "bob", "password123").json()["access_token"]
    client.cookies.clear()
    headers = auth_header(bob)
    assert client.get(f"/api/workspaces/{ws_id}/appimages", headers=headers).status_code == 403
    resp = client.post(
        f"/api/workspaces/{ws_id}/appimages/tasks", json={"op": "install", "urls": [URL]}, headers=headers
    )
    assert resp.status_code == 403
    fake_docker_manager.apps_command.assert_not_called()
