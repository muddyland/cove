"""Saved apps come back on their own when a desktop comes up.

A migration doesn't copy app trees, and a fresh home has none. The first-boot
install used to be an init script bind-mounted from ``<storage>/.cove-scripts``,
which on a zone is the zone host's copy, staged by its agent from the agent's
own image. An agent older than the script left no file there (Docker turns a
missing bind source into an empty directory), so after a migration nothing
installed. Now the control plane queues the install over exec once the desktop
is up, and it shows in the tasks menu as a boot task.
"""

import itertools
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import server.docker_manager as dm
import server.routers.proot as proot_router
from server.db import SessionLocal
from server.docker_manager import DockerManager
from server.models import Workspace
from server.proot import parse_tasks
from server.tests.helpers import add_image, setup_admin

URL = "https://apps.example.com/Foo-1.2_x86_64.AppImage"
_seq = itertools.count()


def _ws_obj(**kw):
    base = dict(id=7, kind="desktop", container_id="cove-ws-7", proot_apps=None, appimages=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _mgr():
    mgr = DockerManager.__new__(DockerManager)
    mgr.zone_id = 2
    mgr.apps_command = MagicMock(return_value=(0, b""))
    return mgr


# ── queue_boot_apps ─────────────────────────────────────────────────────────

def test_queues_both_lists_detached():
    mgr = _mgr()
    mgr.queue_boot_apps(_ws_obj(proot_apps="firefox, gimp", appimages=URL))
    calls = [c.args[1] for c in mgr.apps_command.call_args_list]
    assert calls == [["boot-appimage", URL], ["boot-proot", "firefox", "gimp"]]
    assert all(c.kwargs == {"detach": True} for c in mgr.apps_command.call_args_list)


@pytest.mark.parametrize(
    "ws",
    [
        _ws_obj(proot_apps=None, appimages=None),
        _ws_obj(kind="browser", proot_apps="firefox"),
        _ws_obj(container_id=None, proot_apps="firefox"),
    ],
)
def test_nothing_to_queue(ws):
    mgr = _mgr()
    mgr.queue_boot_apps(ws)
    mgr.apps_command.assert_not_called()


def test_a_failure_never_raises():
    mgr = _mgr()
    mgr.apps_command.side_effect = [RuntimeError("zone down"), (0, b"")]
    mgr.queue_boot_apps(_ws_obj(proot_apps="firefox", appimages=URL))
    # The AppImage call failed; the proot one is still tried.
    assert mgr.apps_command.call_count == 2


# ── launch: no init mount, install queued once running ──────────────────────

def _seed(zone_id=0, **fields) -> int:
    from server.models import User, WorkspaceImage

    db = SessionLocal()
    try:
        n = next(_seq)
        user = User(username=f"u{n}", password_hash="x")
        db.add(user)
        db.commit()
        img = WorkspaceImage(
            name=f"Desk {n}", docker_image="lscr.io/x:latest", image_type="desktop",
            enabled=True, internal_port=3000,
        )
        db.add(img)
        db.commit()
        ws = Workspace(
            user_id=user.id, name="ws", image_id=img.id, zone_id=zone_id,
            status="creating", ephemeral=True, **fields,
        )
        db.add(ws)
        db.commit()
        return ws.id
    finally:
        db.close()


def test_launch_mounts_no_app_init_script_and_queues_after_running(monkeypatch):
    monkeypatch.setattr("docker.from_env", lambda *a, **k: MagicMock())
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    monkeypatch.setattr(dm, "capture_preview_frame", lambda *a, **k: b"\xff\xd8frame")
    ws_id = _seed(proot_apps="firefox", appimages=URL)

    mgr = DockerManager(0)
    fake = MagicMock()
    fake.containers.run.return_value.id = "deadbeefcafe"
    fake.containers.run.return_value.attrs = {"State": {"Status": "running"}}
    mgr._client = fake
    mgr._wait_for_http_ready = lambda *a, **k: True
    queued = []
    monkeypatch.setattr(mgr, "queue_boot_apps", lambda ws: queued.append((ws.id, ws.status)))

    mgr.launch_workspace(ws_id)

    run = next(c for c in fake.containers.run.call_args_list if c.kwargs.get("name") == f"cove-ws-{ws_id}")
    binds = [v["bind"] for v in (run.kwargs.get("volumes") or {}).values()]
    assert not any("install-proot-apps" in b or "install-appimages" in b for b in binds)
    assert queued == [(ws_id, "running")]


def test_reconciler_promotion_queues_boot_apps(client, monkeypatch):
    setup_admin(client)
    image_id = add_image(name="Slow")
    ws_id = client.post("/api/workspaces", json={"name": "slow", "image_id": image_id}).json()["id"]
    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        ws.status = "creating"
        ws.container_id = f"cove-ws-{ws_id}"
        ws.proot_apps = "firefox"
        ws.status_changed_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()
    finally:
        db.close()

    mgr = DockerManager.__new__(DockerManager)
    mgr.zone_id = 0
    mgr._client = MagicMock()
    mgr._client.containers.get.return_value = MagicMock(status="running")
    mgr._sidecar_failure = lambda ws: None
    mgr._wait_for_http_ready = lambda *a, **k: True
    monkeypatch.setattr(dm, "capture_preview_frame", lambda *a, **k: b"\xff\xd8frame")
    queued = []
    mgr.queue_boot_apps = lambda ws: queued.append(ws.id)

    mgr.sync_workspace_statuses()
    assert queued == [ws_id]


# ── tasks: boot tasks are marked ────────────────────────────────────────────

def _task(task_id, kind="proot", apps="firefox gimp"):
    return (
        f"TASK\t{task_id}\tinstall\trunning\t\t1789500000\t1789500001\t\t1\tgimp\t{apps}\t\t{kind}\n"
    ).encode()


def test_boot_tasks_are_flagged():
    tasks = parse_tasks(
        _task("001789500000-boot-proot")
        + _task("001789500000-boot-appimage", kind="appimage", apps="Foo")
        + _task("001789500001-ab12cd34")
    )
    assert [t["boot"] for t in tasks] == [True, True, False]
    assert tasks[0]["current_app"] == "gimp" and tasks[0]["done_count"] == 1


# ── AppImages saved but missing are listed ──────────────────────────────────

@pytest.fixture
def _fresh(monkeypatch):
    monkeypatch.setattr(proot_router, "_tasks_cache", {})
    monkeypatch.setattr(proot_router, "_calls", {})


def test_saved_but_missing_appimages_are_listed(client, fake_docker_manager, _fresh):
    setup_admin(client)
    image_id = add_image(name="Desk", image_type="desktop")
    ws_id = client.post("/api/workspaces", json={"name": "w", "image_id": image_id}).json()["id"]
    other = "https://x.io/Bar.AppImage"
    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        ws.status = "running"
        ws.container_id = f"cove-ws-{ws_id}"
        ws.appimages = f"{URL} {other}"
        db.commit()
    finally:
        db.close()
    # Only Foo is on disk.
    fake_docker_manager.apps_command.return_value = (
        0, f"APPIMAGE\tFoo-1.2_x86_64\tFoo\t{URL}\t120\t1789500000\n".encode(),
    )
    apps = client.get(f"/api/workspaces/{ws_id}/appimages").json()["apps"]
    assert [(a["slug"], a["installed"]) for a in apps] == [("Foo-1.2_x86_64", True), ("Bar", False)]
    assert apps[1]["url"] == other
