"""Tests for the usability-audit additions: pull progress, runtime extension,
audit filters/names, image details/update check, zone health, session revoke."""

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from server import docker_manager as dm_mod
from server.db import SessionLocal
from server.docker_manager import DockerManager, pull_progress, runtime_anchor
from server.models import Workspace
from server.registry import local_digest, parse_ref
from server.tests.helpers import add_image, set_workspace_status, setup_admin


def _manager():
    dm = DockerManager.__new__(DockerManager)
    dm.zone_id = 0
    dm._client = MagicMock()
    dm._pulling = set()
    dm._pulling_lock = threading.Lock()
    return dm


# ── pull progress ─────────────────────────────────────────────────────────


def test_stream_pull_records_progress_and_clears_it():
    dm = _manager()
    seen = []

    def events(*_a, **_k):
        yield {"status": "Pulling from linuxserver/chromium", "id": "latest"}
        yield {"status": "Already exists", "id": "aaa"}
        yield {"status": "Downloading", "id": "bbb", "progressDetail": {"current": 50, "total": 200}}
        seen.append(pull_progress(0, "img:latest"))
        yield {"status": "Downloading", "id": "ccc", "progressDetail": {"current": 10, "total": 100}}
        seen.append(pull_progress(0, "img:latest"))
        yield {"status": "Download complete", "id": "bbb"}
        seen.append(pull_progress(0, "img:latest"))

    dm._client.api.pull.side_effect = events
    dm._stream_pull("img:latest")
    assert seen[0] == {"current": 50, "total": 200, "layers": 2}
    assert seen[1] == {"current": 60, "total": 300, "layers": 3}
    assert seen[2]["current"] == 210
    # Finished pulls disappear from the table.
    assert pull_progress(0, "img:latest") is None
    assert dm.pull_status("img:latest") in ("present", "absent")


def test_stream_pull_raises_on_error_event_and_clears():
    import docker
    import pytest

    dm = _manager()
    dm._client.api.pull.return_value = iter([{"error": "manifest unknown"}])
    with pytest.raises(docker.errors.APIError):
        dm._stream_pull("nope:1")
    assert pull_progress(0, "nope:1") is None


def test_workspace_out_carries_pull_progress(client):
    setup_admin(client)
    image_id = add_image(name="Desk", docker_image="example/desk:latest", image_type="desktop")
    ws = client.post("/api/workspaces", json={"name": "d", "image_id": image_id}).json()
    with dm_mod._PULL_PROGRESS_LOCK:
        dm_mod._PULL_PROGRESS[(0, "example/desk:latest")] = {"current": 5, "total": 10, "layers": 1}
    try:
        body = client.get(f"/api/workspaces/{ws['id']}").json()
        assert body["pull_progress"] == {"current": 5, "total": 10, "layers": 1}
    finally:
        with dm_mod._PULL_PROGRESS_LOCK:
            dm_mod._PULL_PROGRESS.clear()
    assert client.get(f"/api/workspaces/{ws['id']}").json()["pull_progress"] is None


# ── runtime limit + extend ────────────────────────────────────────────────


def test_runtime_policy_and_extend(client):
    setup_admin(client)
    client.put("/api/admin/settings", json={"workspace_max_runtime_hours": 6})
    assert client.get("/api/workspaces/runtime-policy").json() == {"max_hours": 6}

    image_id = add_image(name="Desk", image_type="desktop")
    ws = client.post("/api/workspaces", json={"name": "d", "image_id": image_id}).json()
    # Only running workspaces can be extended.
    assert client.post(f"/api/workspaces/{ws['id']}/extend").status_code == 400
    set_workspace_status(ws["id"], "running")
    r = client.post(f"/api/workspaces/{ws['id']}/extend")
    assert r.status_code == 200, r.text
    assert r.json()["runtime_extended_at"] is not None


def test_runtime_anchor_uses_later_of_start_and_extension():
    started = datetime(2026, 1, 1, 8, 0)
    ws = Workspace(started_at=started, runtime_extended_at=None)
    assert runtime_anchor(ws) == started.replace(tzinfo=timezone.utc)
    ws.runtime_extended_at = started + timedelta(hours=3)
    assert runtime_anchor(ws) == (started + timedelta(hours=3)).replace(tzinfo=timezone.utc)
    ws.started_at = None
    ws.runtime_extended_at = None
    assert runtime_anchor(ws) is None


def test_enforce_runtime_limits_respects_extension(monkeypatch):
    dm = _manager()
    db = SessionLocal()
    try:
        from server.tests.helpers import add_image as _add

        image_id = _add(name="Desk", image_type="desktop")
        from server.models import User

        user = User(username="u1", password_hash="x", auth_provider="local", is_admin=False)
        db.add(user)
        db.commit()
        old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=30)
        a = Workspace(name="a", user_id=user.id, image_id=image_id, status="running",
                      started_at=old, container_id="c1", public_id="a" * 32)
        b = Workspace(name="b", user_id=user.id, image_id=image_id, status="running",
                      started_at=old, runtime_extended_at=datetime.now(timezone.utc).replace(tzinfo=None),
                      container_id="c2", public_id="b" * 32)
        db.add_all([a, b])
        db.commit()
        a_id, b_id = a.id, b.id
    finally:
        db.close()
    dm._get_db = SessionLocal
    stopped = []
    dm.stop_workspace = stopped.append
    monkeypatch.setattr(dm_mod, "get_workspace_max_runtime_hours", lambda _db: 24)
    dm.enforce_runtime_limits(0)
    assert stopped == [a_id]
    assert b_id not in stopped


# ── audit log ─────────────────────────────────────────────────────────────


def test_audit_resolves_workspace_names_and_filters(client):
    setup_admin(client)
    image_id = add_image(name="Desk", image_type="desktop")
    ws = client.post("/api/workspaces", json={"name": "my-desk", "image_id": image_id}).json()

    rows = client.get("/api/admin/audit").json()
    launch = next(r for r in rows if r["action"] == "workspace.launch")
    assert launch["workspace_name"] == "my-desk"
    assert launch["workspace_id"] == ws["id"]
    setup = next(r for r in rows if r["action"] == "setup")
    assert setup["workspace_name"] is None

    only_ws = client.get("/api/admin/audit", params={"action": "workspace."}).json()
    assert only_ws and all(r["action"].startswith("workspace.") for r in only_ws)
    exact = client.get("/api/admin/audit", params={"action": "setup"}).json()
    assert [r["action"] for r in exact] == ["setup"]
    assert client.get("/api/admin/audit", params={"user": "nobody"}).json() == []
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert client.get("/api/admin/audit", params={"since": future}).json() == []
    paged = client.get("/api/admin/audit", params={"limit": 1, "offset": 1}).json()
    assert len(paged) == 1 and paged[0]["id"] == rows[1]["id"]

    facets = client.get("/api/admin/audit/facets").json()
    assert "admin" in facets["users"]
    assert {"setup", "workspace.launch"} <= set(facets["actions"])


# ── sessions ──────────────────────────────────────────────────────────────


def test_revoke_sessions_signs_user_out(client):
    token, _ = setup_admin(client)
    from fastapi.testclient import TestClient

    from server.tests.helpers import create_user_via_admin, login

    uid = create_user_via_admin(client, token, "bob", "password123")["id"]
    other = TestClient(client.app)
    assert login(other, "bob", "password123").status_code == 200
    assert other.get("/api/auth/me").status_code == 200
    import time

    time.sleep(1.1)  # revocation has second granularity
    assert client.post(f"/api/admin/users/{uid}/revoke-sessions").status_code == 204
    assert other.get("/api/auth/me").status_code == 401
    assert client.post("/api/admin/users/9999/revoke-sessions").status_code == 404


# ── images ────────────────────────────────────────────────────────────────


def test_image_details_and_availability(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Desk", docker_image="example/desk:latest", image_type="desktop")
    client.post("/api/workspaces", json={"name": "d", "image_id": image_id})
    fake_docker_manager.is_pulling.return_value = False
    fake_docker_manager.image_info.return_value = {
        "size": 1234, "updated_at": "2026-10-01T00:00:00Z", "repo_digests": [],
    }
    fake_docker_manager.pull_status.return_value = "present"
    details = client.get("/api/images/details").json()
    assert details[str(image_id)] == {
        "status": "present", "size": 1234, "updated_at": "2026-10-01T00:00:00Z",
        "workspace_count": 1, "pull_progress": None,
    }
    assert client.get("/api/images/availability").json() == {str(image_id): "present"}


def test_image_check_update(client, fake_docker_manager, monkeypatch):
    setup_admin(client)
    image_id = add_image(name="Desk", docker_image="example/desk:latest", image_type="desktop")
    fake_docker_manager.image_info.return_value = {
        "size": 1, "updated_at": None, "repo_digests": ["example/desk@sha256:old"],
    }
    monkeypatch.setattr("server.registry.remote_digest", lambda ref: "sha256:new")
    r = client.post(f"/api/images/{image_id}/check-update").json()
    assert r["up_to_date"] is False and r["local_digest"] == "sha256:old"
    monkeypatch.setattr("server.registry.remote_digest", lambda ref: "sha256:old")
    assert client.post(f"/api/images/{image_id}/check-update").json()["up_to_date"] is True
    fake_docker_manager.image_info.return_value = None
    assert client.post(f"/api/images/{image_id}/check-update").json()["up_to_date"] is None


def test_registry_ref_parsing():
    assert parse_ref("traefik:v3.7") == ("registry-1.docker.io", "library/traefik", "v3.7")
    assert parse_ref("qmcgaw/gluetun") == ("registry-1.docker.io", "qmcgaw/gluetun", "latest")
    assert parse_ref("lscr.io/linuxserver/chromium:latest") == ("lscr.io", "linuxserver/chromium", "latest")
    assert parse_ref("localhost:5000/x/y:1") == ("localhost:5000", "x/y", "1")
    assert local_digest(["traefik@sha256:abc"], "traefik:v3.7") == "sha256:abc"
    assert local_digest([], "traefik:v3.7") is None


# ── zones ─────────────────────────────────────────────────────────────────


def test_zone_health_local(client, fake_docker_manager):
    setup_admin(client)
    fake_docker_manager._client.version.return_value = {"Version": "29.8.2"}
    fake_docker_manager._client.info.return_value = {"ContainersRunning": 3}
    body = client.get("/api/admin/zones/0/health").json()
    assert body["ok"] is True and body["docker_version"] == "29.8.2" and body["containers_running"] == 3
    fake_docker_manager._client.version.side_effect = RuntimeError("boom")
    body = client.get("/api/admin/zones/0/health").json()
    assert body == {"ok": False, "latency_ms": None, "docker_version": None,
                    "containers_running": None, "error": "boom", "agent_dind": None,
                    "agent_current": None}
    assert client.get("/api/admin/zones/42/health").status_code == 404


def test_admin_stop_session_stops_instead_of_deleting(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image(name="Desk", image_type="desktop")
    ws = client.post("/api/workspaces", json={"name": "d", "image_id": image_id}).json()
    set_workspace_status(ws["id"], "running")
    assert client.delete(f"/api/admin/sessions/{ws['id']}").status_code == 204
    fake_docker_manager.stop_workspace.assert_called_once_with(ws["id"])
    fake_docker_manager.remove_workspace.assert_not_called()
    body = client.get(f"/api/workspaces/{ws['id']}").json()
    assert body["status"] == "stopping"
    # Not running any more: nothing to stop.
    set_workspace_status(ws["id"], "stopped")
    assert client.delete(f"/api/admin/sessions/{ws['id']}").status_code == 400
