"""Which image the control plane ships to zone agents (installs and Updates).

The default used to be a fixed ``cove:local``. A control plane deployed from a
registry image kept shipping whatever stale ``cove:local`` its host still had,
so "Update agent" never delivered new agent code (e.g. the render-node policy:
GPU launches stayed refused with "device /dev/dri/renderD129 is not allowed").
Now it ships the image the control plane container itself runs.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import docker
import pytest
from fastapi.testclient import TestClient

import server.main as main
from server import agent_update
from server.config import get_settings
from server.tests.helpers import enroll_zone, setup_admin

CP_ID = "a" * 64


class FakeImage:
    def __init__(self, id, created="2026-10-24T04:30:00Z"):
        self.id = id
        self.attrs = {"Created": created}
        self.tags_added = []

    def tag(self, repo, tag):
        self.tags_added.append(f"{repo}:{tag}")
        return True


def _client(images, containers):
    def get_image(ref):
        if ref not in images:
            raise docker.errors.ImageNotFound(ref)
        return images[ref]

    def get_container(ref):
        if ref not in containers:
            raise docker.errors.NotFound(ref)
        return containers[ref]

    c = MagicMock()
    c.images.get.side_effect = get_image
    c.containers.get.side_effect = get_container
    return c


def _container(image_id, ref):
    return SimpleNamespace(attrs={"Image": image_id, "Config": {"Image": ref}})


@pytest.fixture
def cp(monkeypatch):
    """A control plane running in container CP_ID on the registry image, while
    its host also still has an old cove:local."""
    running = FakeImage("sha256:new")
    stale = FakeImage("sha256:old", created="2026-09-01T00:00:00Z")
    client = _client(
        {"sha256:new": running, "registry.example/cove:main": running, "cove:local": stale},
        {CP_ID: _container("sha256:new", "registry.example/cove:main")},
    )
    src = SimpleNamespace(_client=client, save_image_stream=MagicMock(return_value=[b"tar"]))
    mountinfo = f"1 2 0:1 /var/lib/docker/containers/{CP_ID}/hostname /etc/hostname rw\n"
    monkeypatch.setattr(agent_update, "_mountinfo", lambda: mountinfo)
    monkeypatch.setattr(agent_update.socket, "gethostname", lambda: "not-a-container")
    monkeypatch.delenv("COVE_ZONE_AGENT_IMAGE", raising=False)
    get_settings.cache_clear()
    yield src
    get_settings.cache_clear()


def test_agent_image_is_the_control_planes_own(cp):
    image, ref = agent_update.agent_image(cp)
    assert image.id == "sha256:new"
    assert ref == "registry.example/cove:main"


def test_agent_image_configured_wins(cp, monkeypatch):
    monkeypatch.setenv("COVE_ZONE_AGENT_IMAGE", "cove:local")
    get_settings.cache_clear()
    image, ref = agent_update.agent_image(cp)
    assert (image.id, ref) == ("sha256:old", "cove:local")


def test_agent_image_falls_back_outside_a_container(cp, monkeypatch):
    monkeypatch.setattr(agent_update, "_mountinfo", lambda: "")
    image, ref = agent_update.agent_image(cp)
    assert (image.id, ref) == ("sha256:old", "cove:local")


def test_agent_image_unavailable_is_explicit(monkeypatch):
    monkeypatch.setattr(agent_update, "_mountinfo", lambda: "")
    monkeypatch.setattr(agent_update.socket, "gethostname", lambda: "nope")
    src = SimpleNamespace(_client=_client({}, {}))
    with pytest.raises(agent_update.AgentImageUnavailable):
        agent_update.agent_image(src)


def _zone(agent_ref="cove:local", agent_image_id="sha256:old-on-zone", created="2026-09-01T00:00:00Z"):
    loaded = FakeImage("sha256:loaded-on-zone")
    old = FakeImage(agent_image_id, created=created)
    client = _client({agent_image_id: old}, {"cove-agent": _container(agent_image_id, agent_ref)})
    dst = SimpleNamespace(
        _client=client,
        load_image=MagicMock(return_value=[loaded]),
        exec_detached=MagicMock(),
    )
    return dst, loaded


def test_update_ships_running_image_and_retags_the_zones_ref(cp, monkeypatch):
    """Regression: the update saved the configured "cove:local" (stale on a
    registry-deployed control plane) instead of the running image."""
    dst, loaded = _zone()
    monkeypatch.setattr(agent_update, "get_docker_manager", lambda z: cp if z == 0 else dst)

    agent_update.run_agent_update(3)

    cp.save_image_stream.assert_called_once_with("sha256:new")
    dst.load_image.assert_called_once()
    # Tagged as what the zone's compose runs (so the recreate picks it up) and
    # as the control plane's own ref.
    assert loaded.tags_added == ["cove:local", "registry.example/cove:main"]
    dst.exec_detached.assert_called_once_with(agent_update.UPDATER_CONTAINER, agent_update._RECREATE_CMD)


def test_update_refuses_when_nothing_loaded(cp, monkeypatch):
    dst, _ = _zone()
    dst.load_image.return_value = []
    monkeypatch.setattr(agent_update, "get_docker_manager", lambda z: cp if z == 0 else dst)
    with pytest.raises(RuntimeError):
        agent_update.run_agent_update(3)
    dst.exec_detached.assert_not_called()


def test_agent_current_compares_build_time(cp, monkeypatch):
    stale, _ = _zone()
    fresh, _ = _zone(created="2026-10-24T04:30:00Z")
    zones = {0: cp, 3: stale, 4: fresh}
    monkeypatch.setattr(agent_update, "get_docker_manager", lambda z: zones[z])
    assert agent_update.agent_current(3) is False
    assert agent_update.agent_current(4) is True
    # Unknown when the agent can't be inspected.
    gone = SimpleNamespace(_client=_client({}, {}))
    zones[5] = gone
    assert agent_update.agent_current(5) is None


def test_update_agent_route_reports_missing_image(client, monkeypatch):
    setup_admin(client)
    zid, _ = enroll_zone(client)
    monkeypatch.setattr(agent_update, "updater_present", lambda zone_id: True)

    def boom(src=None):
        raise agent_update.AgentImageUnavailable("no image")

    monkeypatch.setattr(agent_update, "agent_image", boom)
    r = client.post(f"/api/admin/zones/{zid}/update-agent")
    assert r.status_code == 409, r.text
    assert "no image" in r.text


def test_zone_health_reports_agent_current(client, monkeypatch, fake_docker_manager):
    setup_admin(client)
    fake_docker_manager._client.version.return_value = {"Version": "29.8.2"}
    fake_docker_manager._client.info.return_value = {"ContainersRunning": 1}
    zid, _ = enroll_zone(client)
    from server.routers import zones

    monkeypatch.setattr(zones, "refresh_agent_dind", lambda db, zone: None)
    monkeypatch.setattr(agent_update, "agent_current", lambda zone_id: False)
    body = client.get(f"/api/admin/zones/{zid}/health").json()
    assert body["agent_current"] is False
    assert client.get("/api/admin/zones/0/health").json()["agent_current"] is None


def test_installer_runs_the_control_planes_image(client, monkeypatch):
    setup_admin(client)
    monkeypatch.setattr(agent_update, "agent_image", lambda src=None: (object(), "registry.example/cove:main"))
    zid = client.post(
        "/api/admin/zones", json={"name": "LAN", "endpoint_host": "10.0.0.5"}
    ).json()["id"]
    token = client.post(f"/api/admin/zones/{zid}/enroll-token").json()["token"]
    script = client.get(f"/install.sh?token={token}").text
    assert 'AGENT_IMAGE="registry.example/cove:main"' in script


def test_launch_policy_refusal_on_zone_hints_at_update(client, monkeypatch):
    """A zone refusing what the control plane sends usually means an old agent."""
    from server.db import SessionLocal
    from server.docker_manager import DockerManager
    from server.models import Workspace
    from server.tests.helpers import add_image

    setup_admin(client)
    zid, _ = enroll_zone(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post(
        "/api/workspaces", json={"name": "g", "image_id": image_id, "zone_id": zid, "ephemeral": True}
    ).json()
    db = SessionLocal()
    try:
        db.get(Workspace, ws["id"]).status = "creating"
        db.commit()
    finally:
        db.close()

    resp = MagicMock(status_code=403)
    err = docker.errors.APIError(
        "403 Client Error", response=resp,
        explanation="blocked by Cove agent policy: device /dev/dri/renderD129 is not allowed",
    )
    fake = MagicMock()
    fake.containers.get.side_effect = docker.errors.NotFound("x")
    fake.containers.run.side_effect = err
    fake.containers.create.side_effect = err
    dm = DockerManager.__new__(DockerManager)
    dm.zone_id = zid
    dm._client = fake
    dm._render_gid_cache = {}
    dm._pull_image = lambda *a, **k: None
    monkeypatch.setattr(dm, "_teardown_after_failed_launch", lambda ws_id: None)
    dm.launch_workspace(ws["id"])

    db = SessionLocal()
    try:
        row = db.get(Workspace, ws["id"])
        assert row.status == "error"
        assert "renderD129 is not allowed" in row.error_message
        assert "Zones → Update" in row.error_message
    finally:
        db.close()


def test_agent_proxy_passes_image_tag(monkeypatch):
    """Retagging the loaded image goes through the agent's policy proxy."""
    monkeypatch.setenv("COVE_AGENT_MODE", "1")
    monkeypatch.setenv("COVE_AGENT_DOCKER_SOCKET_URL", "http://127.0.0.1:9")
    get_settings.cache_clear()
    try:
        c = TestClient(main.create_app())
        r = c.post("/v1.41/images/sha256:abc/tag?repo=cove&tag=local", content=json.dumps({}))
        assert r.status_code == 502, r.text  # not refused; upstream absent
    finally:
        get_settings.cache_clear()
