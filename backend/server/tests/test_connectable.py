"""Clients are held back from a workspace's stream until it has produced its
first frame (or the fallback window for uncapturable images has passed)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import server.docker_manager as dm
from server.db import SessionLocal
from server.models import Workspace
from server.preview import CONNECT_FALLBACK_SECONDS, is_connectable
from server.tests.helpers import add_image, setup_admin

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
FRAME = b"\xff\xd8\xffframe"


def _ws(**kw):
    base = {"status": "running", "preview_at": None, "started_at": NOW}
    return SimpleNamespace(**{**base, **kw})


def test_only_running_workspaces_are_connectable():
    for status in ("creating", "stopped", "stopping", "error", "migrating"):
        assert not is_connectable(_ws(status=status, preview_at=NOW), NOW)


def test_first_frame_opens_the_stream():
    assert not is_connectable(_ws(), NOW)
    assert is_connectable(_ws(preview_at=NOW), NOW)


def test_fallback_opens_uncapturable_streams():
    just_before = NOW + timedelta(seconds=CONNECT_FALLBACK_SECONDS - 1)
    after = NOW + timedelta(seconds=CONNECT_FALLBACK_SECONDS)
    assert not is_connectable(_ws(), just_before)
    assert is_connectable(_ws(), after)


def test_naive_timestamps_and_rows_without_started_at():
    naive = NOW.replace(tzinfo=None)
    assert not is_connectable(_ws(started_at=naive), NOW)
    assert is_connectable(_ws(started_at=None), NOW)


# ── API ────────────────────────────────────────────────────────────────────────

def _running_ws(client, *, preview=False, started_ago=0):
    image_id = add_image(name=f"Desktop {started_ago}-{preview}")
    body = {"name": f"n{started_ago}{preview}", "image_id": image_id}
    ws_id = client.post("/api/workspaces", json=body).json()["id"]
    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        ws.status = "running"
        ws.container_id = f"cove-ws-{ws_id}"
        ws.started_at = datetime.now(timezone.utc) - timedelta(seconds=started_ago)
        if preview:
            ws.preview_jpg = FRAME
            ws.preview_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
    return ws_id


def test_workspace_out_reports_connectable(client):
    setup_admin(client)
    waiting = _running_ws(client)
    ready = _running_ws(client, preview=True)
    fallback = _running_ws(client, started_ago=CONNECT_FALLBACK_SECONDS + 5)
    by_id = {w["id"]: w for w in client.get("/api/workspaces").json()}
    assert by_id[waiting]["connectable"] is False
    assert by_id[ready]["connectable"] is True
    assert by_id[fallback]["connectable"] is True


def test_stream_ready_waits_for_the_first_frame(client, monkeypatch):
    import httpx

    setup_admin(client)
    ws_id = _running_ws(client)

    class NoTraefik:
        def __init__(self, *a, **kw):
            raise AssertionError("route probe must not run before the first frame")

    monkeypatch.setattr(httpx, "AsyncClient", NoTraefik)
    assert client.get(f"/api/workspaces/{ws_id}/stream-ready").json() == {"ready": False}


# ── status monitor ─────────────────────────────────────────────────────────────

def _manager(monkeypatch, frame):
    mgr = dm.DockerManager.__new__(dm.DockerManager)
    mgr.zone_id = 0
    mgr._client = MagicMock()
    mgr._client.containers.get.return_value = MagicMock(status="running")
    mgr._sidecar_failure = lambda ws: None
    capture = MagicMock(return_value=frame)
    monkeypatch.setattr(dm, "capture_preview_frame", capture)
    return mgr, capture


def _row(ws_id):
    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        return ws.status, ws.preview_jpg, ws.preview_at
    finally:
        db.close()


def test_monitor_keeps_trying_for_the_first_frame(client, monkeypatch):
    setup_admin(client)
    ws_id = _running_ws(client)
    mgr, capture = _manager(monkeypatch, None)
    mgr.sync_workspace_statuses()
    assert capture.call_count == 1
    assert _row(ws_id)[2] is None

    capture.return_value = FRAME
    mgr.sync_workspace_statuses()
    status, jpg, at = _row(ws_id)
    assert (status, jpg) == ("running", FRAME) and at is not None


def test_monitor_leaves_ready_workspaces_alone(client, monkeypatch):
    setup_admin(client)
    _running_ws(client, preview=True)
    _running_ws(client, started_ago=CONNECT_FALLBACK_SECONDS + 5)
    mgr, capture = _manager(monkeypatch, FRAME)
    mgr.sync_workspace_statuses()
    capture.assert_not_called()


def test_promotion_without_a_frame_does_not_fake_a_preview(client, monkeypatch):
    setup_admin(client)
    image_id = add_image(name="Slow")
    ws_id = client.post("/api/workspaces", json={"name": "slow", "image_id": image_id}).json()["id"]
    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        ws.status = "creating"
        ws.container_id = f"cove-ws-{ws_id}"
        past_window = dm.DockerManager._FIRST_FRAME_PROMOTE_SECONDS + 5
        ws.status_changed_at = datetime.now(timezone.utc) - timedelta(seconds=past_window)
        db.commit()
    finally:
        db.close()
    mgr, _ = _manager(monkeypatch, None)
    mgr._wait_for_http_ready = lambda *a, **kw: True
    mgr.sync_workspace_statuses()
    status, jpg, at = _row(ws_id)
    assert status == "running" and jpg is None and at is None
