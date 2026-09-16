"""Tests for proot-apps: the catalog, installed apps, update checks, and tasks."""

import itertools
import struct
import time
from unittest.mock import MagicMock

import httpx
import pytest

import server.proot as proot_module
import server.routers.proot as proot_router
from server.db import SessionLocal
from server.models import Workspace
from server.tests.helpers import add_image, auth_header, create_user_via_admin, login, setup_admin

OLD = "sha256:" + "a" * 64
NEW = "sha256:" + "b" * 64
PREFIX = "ghcr.io_linuxserver_proot-apps_"


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    monkeypatch.setattr(proot_module, "_digest_cache", {})
    monkeypatch.setattr(proot_module, "_token_cache", None)
    monkeypatch.setattr(proot_module, "_inflight", {})
    monkeypatch.setattr(proot_module, "_meta_cache", {})
    monkeypatch.setattr(proot_router, "_tasks_cache", {})
    monkeypatch.setattr(proot_router, "_calls", {})


_seq = itertools.count()


def _make_ws(client, *, status="running", workspace_type="desktop", proot_apps=None):
    n = next(_seq)
    # The type lives on the image: a workspace's own copy is ignored (see Workspace.kind).
    image_id = add_image(name=f"Image {n}", image_type=workspace_type)
    resp = client.post("/api/workspaces", json={"name": f"apps-{n}", "image_id": image_id})
    assert resp.status_code == 201, resp.text
    ws_id = resp.json()["id"]
    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        ws.status = status
        ws.container_id = f"cove-ws-{ws_id}"
        ws.proot_apps = proot_apps
        db.commit()
    finally:
        db.close()
    return ws_id


def _saved_apps(ws_id):
    db = SessionLocal()
    try:
        return db.get(Workspace, ws_id).proot_apps
    finally:
        db.close()


# ── catalog ────────────────────────────────────────────────────────────────────

def test_proot_apps_lists_catalog(client, monkeypatch):
    # Seed the module cache so no network call is made.
    monkeypatch.setattr(proot_module, "_cache", ["blender", "firefox"])
    setup_admin(client)
    meta = {"firefox": {"icon_url": "https://x/f.svg", "full_name": "Firefox"}}
    monkeypatch.setattr(proot_module, "_meta_cache", meta)
    resp = client.get("/api/proot-apps")
    assert resp.status_code == 200
    assert resp.json() == {
        "apps": ["blender", "firefox"],
        "meta": {"firefox": {"icon_url": "https://x/f.svg", "full_name": "Firefox"}},
    }


def test_parse_metadata_builds_icon_urls_and_rejects_bad_values():
    text = """include:
  - name: firefox
    full_name: Firefox
    arch: linux/amd64,linux/arm64
    icon: firefox.svg
    description: "Browser"
  - name: anki
    full_name: "Anki"
    icon: anydesk.svg
  - name: evil
    full_name: Evil
    icon: ../../../../evil.svg
  - name: evil2
    icon: javascript:alert(1).svg
  - name: evil3
    icon: https://attacker.example/x.png
  - name: Bad Name
    icon: bad.svg
"""
    meta = proot_module.parse_metadata(text)
    base = "https://raw.githubusercontent.com/linuxserver/proot-apps/master/metadata/img/"
    assert meta["firefox"] == {"icon_url": base + "firefox.svg", "full_name": "Firefox"}
    assert meta["anki"] == {"icon_url": base + "anydesk.svg", "full_name": "Anki"}
    assert meta["evil"]["icon_url"] is None
    assert meta["evil2"]["icon_url"] is None
    assert meta["evil3"]["icon_url"] is None
    assert "Bad Name" not in meta


def test_proot_apps_requires_auth(client):
    resp = client.get("/api/proot-apps")
    assert resp.status_code == 401


# ── parsers (input comes from a user-controlled container) ─────────────────────

def test_parse_listing_validates_every_field():
    raw = (
        "ARCH\tx86_64\nPROOT\t1\n"
        f"APP\t{PREFIX}firefox\t{OLD}\t0\n"
        f"APP\t{PREFIX}gimp\tnot-a-digest\t1\n"
        "APP\tdocker.io_someone_thing_latest\t\t0\n"
        "APP\t../../etc\t\t0\n"
        f"APP\t{PREFIX}Bad;Name\t{OLD}\t0\n"
        "APP\ttoo\tfew\n"
        "garbage line\n"
    ).encode()
    listing = proot_module.parse_listing(raw)
    assert listing["arch"] == "amd64"
    assert listing["available"] is True
    assert listing["apps"] == [
        {"folder": f"{PREFIX}firefox", "name": "firefox", "digest": OLD, "downloading": False},
        {"folder": f"{PREFIX}gimp", "name": "gimp", "digest": None, "downloading": True},
        {"folder": "docker.io_someone_thing_latest", "name": None, "digest": None, "downloading": False},
    ]


def test_parse_listing_unknown_arch_is_none():
    assert proot_module.parse_listing(b"ARCH\triscv64\n")["arch"] is None


def test_parse_tasks_drops_malformed_rows():
    good = "TASK\t000000000001-ab\tupdate\trunning\t\t100\t101\t\t1\tgimp\tfirefox gimp\t\tproot\n"
    bad = [
        "TASK\t../x\tupdate\trunning\t\t100\t101\t\t1\t\tfirefox\t\tproot\n",  # id
        "TASK\t000000000002-ab\trm -rf\trunning\t\t100\t101\t\t1\t\tfirefox\t\tproot\n",  # op
        "TASK\t000000000003-ab\tupdate\tweird\t\t100\t101\t\t1\t\tfirefox\t\tproot\n",  # state
        "TASK\t000000000004-ab\tupdate\tdone\t0\t100\t101\t102\t1\t\t\t\tproot\n",  # no apps
        "TASK\t000000000005-ab\tupdate\tdone\t0\t100\t101\t102\t1\t\tfire$fox\t\tproot\n",  # app name
        "TASK\t000000000006-ab\tupdate\tdone\t0\t100\t101\tproot\n",  # field count
    ]
    tasks = proot_module.parse_tasks((good + "".join(bad)).encode())
    assert tasks == [
        {
            "id": "000000000001-ab",
            "kind": "proot",
            "op": "update",
            "state": "running",
            "exit_code": None,
            "apps": ["firefox", "gimp"],
            "failed_apps": [],
            "current_app": "gimp",
            "done_count": 1,
            "created_at": 100,
            "started_at": 101,
            "finished_at": None,
        }
    ]


def test_parse_tasks_clamps_done_count():
    raw = b"TASK\t000000000001-ab\tinstall\tdone\t0\t1\t2\t3\t99\t\tfirefox\t\tproot\n"
    assert proot_module.parse_tasks(raw)[0]["done_count"] == 1


def test_clean_log_collapses_progress_and_strips_control_chars():
    raw = b"start\n 10%\r 50%\r100%\n\x1b[31mred\x1b[0m\x07\ndone\r\n"
    assert proot_module.clean_log(raw) == "start\n100%\nred\ndone\n"


# ── registry lookups ───────────────────────────────────────────────────────────

def _registry(handler_log, *, index=True, token_status=200):
    """A fake ghcr.io serving firefox (multi-arch index) and gimp (single)."""

    def handler(request: httpx.Request) -> httpx.Response:
        handler_log.append(request.url.path)
        assert request.url.host == "ghcr.io"
        if request.url.path == "/token":
            return httpx.Response(token_status, json={"token": "t", "expires_in": 300})
        assert request.headers["Authorization"] == "Bearer t"
        path = request.url.path
        if path.endswith("/manifests/firefox"):
            if not index:
                return httpx.Response(200, json={"layers": [{"digest": NEW}]})
            return httpx.Response(200, json={"manifests": [
                {"digest": "sha256:" + "1" * 64, "platform": {"architecture": "amd64"}},
                {"digest": "sha256:" + "2" * 64, "platform": {"architecture": "arm64"}},
                {"digest": "sha256:" + "3" * 64, "annotations": {"vnd.docker.reference.type": "attestation"}},
            ]})
        if path.endswith("/manifests/sha256:" + "1" * 64):
            return httpx.Response(200, json={"layers": [{"digest": NEW}]})
        if path.endswith("/manifests/sha256:" + "2" * 64):
            return httpx.Response(200, json={"layers": [{"digest": OLD}]})
        return httpx.Response(404, json={})

    return handler


@pytest.fixture
def fake_registry(monkeypatch):
    calls: list[str] = []
    state = {"handler": _registry(calls)}
    real = httpx.AsyncClient

    def factory(**kwargs):
        return real(transport=httpx.MockTransport(lambda r: state["handler"](r)), **kwargs)

    monkeypatch.setattr(proot_module.httpx, "AsyncClient", factory)
    return calls, state


async def test_latest_digests_resolves_per_arch_and_caches(fake_registry):
    calls, _ = fake_registry
    assert await proot_module.latest_digests(["firefox", "nope"], "amd64") == {"firefox": NEW, "nope": None}
    assert await proot_module.latest_digests(["firefox"], "arm64") == {"firefox": OLD}
    count = len(calls)
    # Both results (including the miss) are cached.
    assert await proot_module.latest_digests(["firefox", "nope"], "amd64") == {"firefox": NEW, "nope": None}
    assert len(calls) == count


async def test_latest_digests_never_builds_urls_from_bad_names(fake_registry):
    calls, _ = fake_registry
    result = await proot_module.latest_digests(["../../evil", "a b", "unknown-arch"], "riscv")
    assert result == {"../../evil": None, "a b": None, "unknown-arch": None}
    assert calls == []
    result = await proot_module.latest_digests(["../../evil"], "amd64")
    assert result == {"../../evil": None}
    assert calls == []


async def test_latest_digests_token_failure_is_unknown_and_uncached(fake_registry):
    calls, state = fake_registry
    state["handler"] = _registry(calls, token_status=503)
    assert await proot_module.latest_digests(["firefox"], "amd64") == {"firefox": None}
    state["handler"] = _registry(calls)
    assert await proot_module.latest_digests(["firefox"], "amd64") == {"firefox": NEW}


# ── installed apps endpoint ────────────────────────────────────────────────────

def _listing(*apps):
    lines = ["ARCH\tx86_64", "PROOT\t1"] + [f"APP\t{PREFIX}{n}\t{d}\t{dl}" for n, d, dl in apps]
    return ("\n".join(lines) + "\n").encode()


@pytest.fixture
def catalog(monkeypatch):
    monkeypatch.setattr(proot_module, "_cache", ["blender", "firefox", "gimp", "krita"])


def test_installed_apps_report_updates(client, fake_docker_manager, monkeypatch, catalog):
    setup_admin(client)
    ws_id = _make_ws(client, proot_apps="firefox gimp blender")
    fake_docker_manager.apps_command.return_value = (
        0, _listing(("firefox", OLD, 0), ("gimp", NEW, 0), ("krita", "", 1)),
    )

    async def fake_latest(apps, arch):
        assert arch == "amd64"
        assert sorted(apps) == ["firefox", "gimp"]  # downloading apps aren't checked
        return {"firefox": NEW, "gimp": NEW}

    monkeypatch.setattr(proot_router, "latest_digests", fake_latest)
    monkeypatch.setattr(proot_module, "_meta_cache", {
        "firefox": {"icon_url": "https://x/firefox.svg", "full_name": "Firefox"},
        "blender": {"icon_url": "https://x/blender.svg", "full_name": "Blender"},
    })
    resp = client.get(f"/api/workspaces/{ws_id}/proot-apps")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True and body["checked"] is True and body["check_failed"] is False
    apps = {a["name"]: a for a in body["apps"]}
    assert apps["firefox"]["update_available"] is True
    assert apps["firefox"]["icon_url"] == "https://x/firefox.svg"
    assert apps["blender"]["full_name"] == "Blender"  # saved-but-missing rows too
    assert apps["gimp"]["icon_url"] is None
    assert apps["gimp"]["update_available"] is False
    assert apps["krita"]["downloading"] is True and apps["krita"]["update_available"] is None
    assert apps["krita"]["in_config"] is False
    # Saved but missing from disk.
    assert apps["blender"]["installed"] is False and apps["blender"]["in_config"] is True
    fake_docker_manager.apps_command.assert_called_once()
    assert fake_docker_manager.apps_command.call_args.args[1] == ["list"]


def test_installed_apps_without_check_skip_registry(client, fake_docker_manager, monkeypatch, catalog):
    setup_admin(client)
    ws_id = _make_ws(client)
    fake_docker_manager.apps_command.return_value = (0, _listing(("firefox", OLD, 0)))

    async def boom(apps, arch):
        raise AssertionError("registry should not be queried")

    monkeypatch.setattr(proot_router, "latest_digests", boom)
    resp = client.get(f"/api/workspaces/{ws_id}/proot-apps?check=false")
    assert resp.status_code == 200
    assert resp.json()["checked"] is False
    assert resp.json()["apps"][0]["update_available"] is None


def test_update_checks_only_query_catalog_apps(client, fake_docker_manager, monkeypatch, catalog):
    setup_admin(client)
    ws_id = _make_ws(client)
    fake_docker_manager.apps_command.return_value = (
        0, _listing(("firefox", OLD, 0), ("made-up-1", OLD, 0), ("made-up-2", OLD, 0)),
    )
    seen = []

    async def fake_latest(apps, arch):
        seen.extend(apps)
        return {a: NEW for a in apps}

    monkeypatch.setattr(proot_router, "latest_digests", fake_latest)
    body = client.get(f"/api/workspaces/{ws_id}/proot-apps").json()
    assert seen == ["firefox"]
    # Unchecked non-catalog apps aren't a failed check.
    assert body["check_failed"] is False
    apps = {a["name"]: a for a in body["apps"]}
    assert apps["made-up-1"]["update_available"] is None


def test_unresolved_catalog_app_reports_check_failed(client, fake_docker_manager, monkeypatch, catalog):
    setup_admin(client)
    ws_id = _make_ws(client)
    fake_docker_manager.apps_command.return_value = (0, _listing(("firefox", OLD, 0), ("gimp", OLD, 0)))

    async def fake_latest(apps, arch):
        return {"firefox": NEW, "gimp": None}

    monkeypatch.setattr(proot_router, "latest_digests", fake_latest)
    body = client.get(f"/api/workspaces/{ws_id}/proot-apps").json()
    assert body["check_failed"] is True


def test_update_checks_skipped_without_catalog(client, fake_docker_manager, monkeypatch):
    setup_admin(client)
    ws_id = _make_ws(client)
    fake_docker_manager.apps_command.return_value = (0, _listing(("firefox", OLD, 0)))

    async def no_catalog():
        raise httpx.ConnectError("github down")

    async def boom(apps, arch):
        raise AssertionError("registry should not be queried")

    monkeypatch.setattr(proot_router, "list_proot_apps", no_catalog)
    monkeypatch.setattr(proot_router, "latest_digests", boom)
    body = client.get(f"/api/workspaces/{ws_id}/proot-apps").json()
    assert body["checked"] is False


def test_installed_apps_require_running_desktop(client, fake_docker_manager):
    setup_admin(client)
    stopped = _make_ws(client, status="stopped")
    assert client.get(f"/api/workspaces/{stopped}/proot-apps").status_code == 409
    browser = _make_ws(client, workspace_type="browser")
    assert client.get(f"/api/workspaces/{browser}/proot-apps").status_code == 400
    fake_docker_manager.apps_command.assert_not_called()


def test_installed_apps_oversized_output_is_502(client, fake_docker_manager):
    setup_admin(client)
    ws_id = _make_ws(client)
    fake_docker_manager.apps_command.return_value = (None, None)
    assert client.get(f"/api/workspaces/{ws_id}/proot-apps").status_code == 502


def test_other_users_cannot_touch_apps(client, fake_docker_manager):
    admin_token, _ = setup_admin(client)
    ws_id = _make_ws(client)
    create_user_via_admin(client, admin_token, "bob")
    bob = login(client, "bob", "password123").json()["access_token"]
    client.cookies.clear()
    headers = auth_header(bob)
    assert client.get(f"/api/workspaces/{ws_id}/proot-apps", headers=headers).status_code == 403
    resp = client.post(
        f"/api/workspaces/{ws_id}/proot-apps/tasks", json={"op": "remove", "apps": ["firefox"]}, headers=headers
    )
    assert resp.status_code == 403
    log_url = f"/api/workspaces/{ws_id}/proot-apps/tasks/000000000001-ab/log"
    assert client.get(log_url, headers=headers).status_code == 403
    assert client.get("/api/proot-tasks", headers=headers).json() == []
    fake_docker_manager.apps_command.assert_not_called()


# ── tasks ──────────────────────────────────────────────────────────────────────

def test_start_task_queues_runs_and_syncs_saved_list(client, fake_docker_manager):
    setup_admin(client)
    ws_id = _make_ws(client, proot_apps="firefox")
    fake_docker_manager.apps_command.return_value = (0, b"")

    resp = client.post(f"/api/workspaces/{ws_id}/proot-apps/tasks", json={"op": "install", "apps": ["gimp", "gimp"]})
    assert resp.status_code == 202, resp.text
    task = resp.json()
    assert task["state"] == "queued" and task["apps"] == ["gimp"]

    start, run = fake_docker_manager.apps_command.call_args_list
    assert start.args[1] == ["start", task["id"], "proot", "install", "gimp"]
    assert start.kwargs == {"detach": False}
    assert run.args[1] == ["run", task["id"]] and run.kwargs == {"detach": True}
    assert _saved_apps(ws_id) == "firefox gimp"

    client.post(f"/api/workspaces/{ws_id}/proot-apps/tasks", json={"op": "update", "apps": ["firefox"]})
    assert _saved_apps(ws_id) == "firefox gimp"
    client.post(f"/api/workspaces/{ws_id}/proot-apps/tasks", json={"op": "remove", "apps": ["firefox", "gimp"]})
    assert _saved_apps(ws_id) is None


@pytest.mark.parametrize(
    "body",
    [
        {"op": "install", "apps": ["fire fox"]},
        {"op": "install", "apps": ["$(reboot)"]},
        {"op": "install", "apps": ["-rf"]},
        {"op": "install", "apps": []},
        {"op": "run", "apps": ["firefox"]},
        {"op": "install", "apps": ["a"] * 51},
    ],
)
def test_start_task_rejects_bad_input(client, fake_docker_manager, body):
    setup_admin(client)
    ws_id = _make_ws(client)
    resp = client.post(f"/api/workspaces/{ws_id}/proot-apps/tasks", json=body)
    assert resp.status_code in (400, 422)
    fake_docker_manager.apps_command.assert_not_called()


def test_start_task_busy_is_429_and_leaves_config(client, fake_docker_manager):
    setup_admin(client)
    ws_id = _make_ws(client, proot_apps="firefox")
    fake_docker_manager.apps_command.return_value = (3, b"")
    resp = client.post(f"/api/workspaces/{ws_id}/proot-apps/tasks", json={"op": "remove", "apps": ["firefox"]})
    assert resp.status_code == 429
    assert fake_docker_manager.apps_command.call_count == 1  # never ran
    assert _saved_apps(ws_id) == "firefox"


def test_task_log_validates_id_and_cleans_output(client, fake_docker_manager):
    setup_admin(client)
    ws_id = _make_ws(client)
    assert client.get(f"/api/workspaces/{ws_id}/proot-apps/tasks/..%2Fetc/log").status_code in (400, 404)
    assert client.get(f"/api/workspaces/{ws_id}/proot-apps/tasks/bad_id/log").status_code == 400
    fake_docker_manager.apps_command.assert_not_called()

    fake_docker_manager.apps_command.return_value = (0, b"a\r b\n")
    resp = client.get(f"/api/workspaces/{ws_id}/proot-apps/tasks/000000000001-ab/log")
    assert resp.json() == {"output": " b\n"}
    fake_docker_manager.apps_command.return_value = (4, b"")
    assert client.get(f"/api/workspaces/{ws_id}/proot-apps/tasks/000000000001-ab/log").status_code == 404


def test_overview_lists_only_own_live_desktops(client, fake_docker_manager):
    setup_admin(client)
    running = _make_ws(client)
    _make_ws(client, status="stopped")
    _make_ws(client, workspace_type="browser")
    row = "TASK\t000000000001-ab\tinstall\trunning\t\t1\t2\t\t0\tfirefox\tfirefox\t\tproot\n"
    fake_docker_manager.apps_command.return_value = (0, row.encode())

    resp = client.get("/api/proot-tasks")
    assert resp.status_code == 200
    body = resp.json()
    assert [w["workspace_id"] for w in body] == [running]
    assert body[0]["tasks"][0]["current_app"] == "firefox"
    assert fake_docker_manager.apps_command.call_count == 1

    # Cached within the window: no second exec.
    client.get("/api/proot-tasks")
    assert fake_docker_manager.apps_command.call_count == 1


def test_overview_survives_unreachable_workspace(client, fake_docker_manager):
    import docker.errors

    setup_admin(client)
    _make_ws(client)
    fake_docker_manager.apps_command.side_effect = docker.errors.APIError("zone down")
    resp = client.get("/api/proot-tasks")
    assert resp.status_code == 200 and resp.json() == []


def test_overview_does_not_wait_for_a_hung_workspace(client, fake_docker_manager, monkeypatch):
    setup_admin(client)
    _make_ws(client)
    monkeypatch.setattr(proot_router, "_OVERVIEW_WAIT", 0.2)

    def slow(ws, args, detach=False):
        time.sleep(1.5)
        return 0, b""

    fake_docker_manager.apps_command.side_effect = slow
    started = time.monotonic()
    assert client.get("/api/proot-tasks").json() == []
    assert time.monotonic() - started < 1.2


# ── DockerManager.apps_command ────────────────────────────────────────────────

def _frame(data: bytes, stream_id: int = 1) -> bytes:
    return struct.pack(">BxxxL", stream_id, len(data)) + data


class _FakeSocket:
    """An exec's raw socket: serves ``chunks`` then EOF. A callable chunk runs
    instead (to simulate a stall)."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.timeout = None
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def recv(self, n):
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        return chunk(self) if callable(chunk) else chunk

    def close(self):
        self.closed = True


def _manager_with(chunks, exit_code=0):
    from server.docker_manager import DockerManager

    mgr = DockerManager.__new__(DockerManager)
    api = MagicMock()
    api.exec_create.return_value = {"Id": "e1"}
    sock = _FakeSocket(chunks)
    api.exec_start.side_effect = lambda exec_id, detach=False, socket=False: sock if socket else None
    api.exec_inspect.return_value = {"ExitCode": exit_code}
    container = MagicMock(id="c1")
    container.client.api = api
    mgr._client = MagicMock()
    mgr._client.containers.get.return_value = container
    return mgr, api, sock


def _ws(container_id="cove-ws-1"):
    return Workspace(id=1, container_id=container_id, zone_id=0)


def test_apps_command_runs_as_desktop_user_with_args_as_argv():
    mgr, api, sock = _manager_with([_frame(b"ARCH\tx86"), _frame(b"_64\n"), _frame(b"noise", 2)], exit_code=0)
    code, out = mgr.apps_command(_ws(), ["start", "000000000001-ab", "install", "firefox"])
    assert (code, out) == (0, b"ARCH\tx86_64\n")  # stderr frames dropped
    assert sock.closed
    _, cmd = api.exec_create.call_args.args
    assert api.exec_create.call_args.kwargs["user"] == "abc"
    assert "privileged" not in api.exec_create.call_args.kwargs
    assert cmd[:2] == ["sh", "-c"]
    assert cmd[3].startswith("#!/bin/bash")  # the script itself
    assert cmd[4:] == ["cove-apps", "start", "000000000001-ab", "install", "firefox"]


def test_apps_command_reassembles_frames_split_across_reads():
    data = _frame(b"hello ") + _frame(b"world")
    mgr, _, _ = _manager_with([data[:3], data[3:11], data[11:]])
    assert mgr.apps_command(_ws(), ["list"]) == (0, b"hello world")


def test_apps_command_caps_output(monkeypatch):
    import server.docker_manager as dm

    monkeypatch.setattr(dm, "_PROOT_EXEC_MAX_BYTES", 10)
    mgr, api, sock = _manager_with([_frame(b"x" * 6), _frame(b"x" * 6)])
    assert mgr.apps_command(_ws(), ["list"]) == (None, None)
    assert sock.closed
    api.exec_inspect.assert_not_called()


def test_apps_command_silent_exec_cannot_hang(monkeypatch):
    """A process that never writes (e.g. SIGSTOPped by the user) must still
    release the thread: every read is bounded by the deadline."""
    import socket as pysocket

    import server.docker_manager as dm

    monkeypatch.setattr(dm, "_PROOT_EXEC_DEADLINE", 0.05)

    def stall(sock):
        assert sock.timeout is not None and 0 < sock.timeout <= 0.05
        raise pysocket.timeout()

    mgr, _, sock = _manager_with([stall])
    assert mgr.apps_command(_ws(), ["tasks"]) == (None, None)
    assert sock.closed


def test_apps_command_trickle_hits_deadline(monkeypatch):
    import server.docker_manager as dm

    monkeypatch.setattr(dm, "_PROOT_EXEC_DEADLINE", 0.05)

    def slow(sock):
        time.sleep(0.02)
        return b"."

    mgr, _, sock = _manager_with([slow] * 100)
    assert mgr.apps_command(_ws(), ["tasks"]) == (None, None)
    assert sock.closed


def test_apps_command_detached_run():
    mgr, api, _ = _manager_with([])
    assert mgr.apps_command(_ws(), ["run", "000000000001-ab"], detach=True) == (0, b"")
    _, cmd = api.exec_create.call_args.args
    assert cmd[:2] == ["bash", "-c"] and cmd[3:] == ["cove-apps", "run", "000000000001-ab"]
    assert api.exec_create.call_args.kwargs["user"] == "abc"
    api.exec_start.assert_called_once_with("e1", detach=True)


def test_apps_command_missing_container():
    import docker.errors

    from server.docker_manager import ProotUnavailable

    mgr, _, _ = _manager_with([])
    with pytest.raises(ProotUnavailable):
        mgr.apps_command(_ws(container_id=None), ["list"])
    mgr._client.containers.get.side_effect = docker.errors.NotFound("gone")
    with pytest.raises(ProotUnavailable):
        mgr.apps_command(_ws(), ["list"])


# ── abuse limits ───────────────────────────────────────────────────────────────

def test_driver_calls_per_workspace_are_limited(client, fake_docker_manager, monkeypatch):
    setup_admin(client)
    ws_id = _make_ws(client)
    monkeypatch.setattr(proot_router, "_calls", {f"ws:{ws_id}": proot_router._MAX_CALLS_PER_WORKSPACE})
    assert client.get(f"/api/workspaces/{ws_id}/proot-apps/tasks").status_code == 429
    fake_docker_manager.apps_command.assert_not_called()


def test_driver_slots_are_released(client, fake_docker_manager, monkeypatch):
    import docker.errors

    setup_admin(client)
    ws_id = _make_ws(client)
    calls: dict = {}
    monkeypatch.setattr(proot_router, "_calls", calls)
    fake_docker_manager.apps_command.side_effect = docker.errors.APIError("boom")
    for _ in range(5):
        assert client.get(f"/api/workspaces/{ws_id}/proot-apps/tasks").status_code == 502
    fake_docker_manager.apps_command.side_effect = None
    fake_docker_manager.apps_command.return_value = (0, b"")
    assert client.get(f"/api/workspaces/{ws_id}/proot-apps/tasks").status_code == 200
    assert calls == {}


async def test_concurrent_update_checks_share_lookups(fake_registry):
    import asyncio

    calls, _ = fake_registry
    results = await asyncio.gather(*(proot_module.latest_digests(["firefox"], "amd64") for _ in range(10)))
    assert all(r == {"firefox": NEW} for r in results)
    # One token + index + per-arch manifest, not ten of each.
    assert len([c for c in calls if c.endswith("/manifests/firefox")]) == 1
    assert proot_module._inflight == {}


def test_saved_list_rejects_non_catalog_style_names(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Desk X", image_type="desktop")
    for bad in ("Firefox", "ghcr.io/evil/app:tag", "a+b"):
        resp = client.post("/api/workspaces", json={"name": "x", "image_id": image_id, "proot_apps": bad})
        assert resp.status_code == 400, bad
