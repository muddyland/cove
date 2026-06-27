"""A launch failure (e.g. an unreachable zone agent) must mark the workspace
'error', not leave it hanging in 'creating'."""

from unittest.mock import MagicMock

import pytest

from server.db import SessionLocal
from server.docker_manager import DockerManager
from server.models import User, Workspace, WorkspaceImage


@pytest.fixture(autouse=True)
def _stub_docker_from_env(monkeypatch):
    """DockerManager(0).__init__ calls docker.from_env(), which connects to the
    daemon for version negotiation. Stub it so these tests run with no Docker
    socket (CI); each test then overrides ._client with its own mock anyway."""
    monkeypatch.setattr("docker.from_env", lambda *a, **k: MagicMock())


def _seed_ws(zone_id: int = 0) -> int:
    db = SessionLocal()
    try:
        user = User(username="alice", password_hash="x")
        db.add(user)
        db.commit()
        db.refresh(user)
        img = WorkspaceImage(
            name="Img", docker_image="lscr.io/x:latest", image_type="desktop",
            enabled=True, internal_port=3000,
        )
        db.add(img)
        db.commit()
        db.refresh(img)
        ws = Workspace(
            user_id=user.id, name="ws", image_id=img.id, zone_id=zone_id,
            status="creating", ephemeral=True,
        )
        db.add(ws)
        db.commit()
        return ws.id
    finally:
        db.close()


def _status(ws_id: int):
    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        return ws.status, ws.error_message
    finally:
        db.close()


def test_connection_error_marks_workspace_error():
    """A non-APIError (connection/timeout) reaching the daemon is caught and
    surfaced, instead of dying silently and leaving the workspace 'creating'."""
    ws_id = _seed_ws(zone_id=0)
    dm = DockerManager(0)
    fake = MagicMock()
    fake.networks.get.side_effect = ConnectionError("agent unreachable")
    fake.networks.create.side_effect = ConnectionError("agent unreachable")
    dm._client = fake

    dm.launch_workspace(ws_id)  # must not raise

    status, msg = _status(ws_id)
    assert status == "error"
    assert "unreachable" in (msg or "")


def test_remote_zone_unreachable_fails_fast():
    """For a remote zone, the pre-flight ping turns an unreachable agent into a
    clear error rather than a partial launch."""
    ws_id = _seed_ws(zone_id=0)  # row stays zone 0; we force the manager remote
    dm = DockerManager(0)
    dm.zone_id = 5  # pretend this manager is for a remote zone
    fake = MagicMock()
    fake.ping.side_effect = ConnectionError("no route to host")
    dm._client = fake

    dm.launch_workspace(ws_id)

    status, msg = _status(ws_id)
    assert status == "error"
    assert "zone agent unreachable" in (msg or "")


def test_remove_deletes_record_when_zone_unreachable():
    """Purging a workspace whose zone agent is unreachable must still delete the
    DB record (otherwise it reappears on the next poll)."""
    ws_id = _seed_ws(zone_id=0)
    dm = DockerManager(0)
    fake = MagicMock()
    # The network cleanup talks to the daemon; simulate the agent being down.
    fake.networks.get.side_effect = ConnectionError("agent unreachable")
    dm._client = fake

    dm.remove_workspace(ws_id)

    db = SessionLocal()
    try:
        assert db.get(Workspace, ws_id) is None
    finally:
        db.close()
