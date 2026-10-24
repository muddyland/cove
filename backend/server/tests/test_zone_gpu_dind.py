"""Per-zone GPU and Docker-in-Docker settings.

GPU and DinD both depend on the host a workspace runs on. The local zone uses
the global settings; a remote zone can override GPU (its own render node and
group) and must opt in to DinD twice: the admin on the control plane
(``Zone.docker_enabled``) and the zone host itself (COVE_AGENT_ALLOW_DIND), since
the DinD sidecar is privileged and the agent refuses privileged creates
otherwise.
"""

import json
from unittest.mock import MagicMock

import pytest
from docker.models.containers import _create_container_args
from fastapi.testclient import TestClient

import server.main as main
from server import settings_store
from server.agent_update import _RECREATE_CMD
from server.config import get_settings
from server.db import SessionLocal
from server.docker_manager import DockerManager
from server.docker_policy import check_create_policy, check_exec_policy
from server.models import Workspace, Zone
from server.tests.helpers import add_image, enroll_zone, setup_admin


def _set_zone(zid: int, **fields) -> None:
    db = SessionLocal()
    try:
        zone = db.get(Zone, zid)
        for k, v in fields.items():
            setattr(zone, k, v)
        db.commit()
    finally:
        db.close()


def _ws_row(ws_id: int) -> Workspace:
    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        db.expunge(ws)
        return ws
    finally:
        db.close()


def _set_ws(ws_id: int, **fields) -> None:
    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        for k, v in fields.items():
            setattr(ws, k, v)
        db.commit()
    finally:
        db.close()


@pytest.fixture
def no_agent(monkeypatch):
    """The agent health probe never reaches a real zone in tests; by default it
    reports nothing new (keeps the stored value)."""
    from server.routers import zones

    calls = []

    def fake(db, zone):
        calls.append(zone.id)
        return zone.agent_allows_docker

    monkeypatch.setattr(zones, "refresh_agent_dind", fake)
    return calls


def _dind_admin(client):
    setup_admin(client)
    client.put("/api/admin/settings", json={"workspace_docker": True})
    zid, _ = enroll_zone(client, name="Z", host="10.0.0.9")
    return zid


# ── resolution ─────────────────────────────────────────────────────────────

def test_zone_gpu_inherits_then_overrides(client):
    setup_admin(client)
    zid, _ = enroll_zone(client)
    client.put(
        "/api/admin/settings",
        json={"workspace_gpu_accel": True, "workspace_gpu_render_gid": 992},
    )
    db = SessionLocal()
    try:
        assert settings_store.get_zone_gpu_accel(db, zid) is True
        assert settings_store.get_zone_gpu_render_node(db, zid) == "/dev/dri/renderD128"
        assert settings_store.get_zone_gpu_render_gid(db, zid) == 992
    finally:
        db.close()

    _set_zone(zid, gpu_accel=False, gpu_render_node="/dev/dri/renderD129", gpu_render_gid=44)
    db = SessionLocal()
    try:
        assert settings_store.get_zone_gpu_accel(db, zid) is False
        assert settings_store.get_zone_gpu_render_node(db, zid) == "/dev/dri/renderD129"
        assert settings_store.get_zone_gpu_render_gid(db, zid) == 44
        # The local zone keeps the global values.
        assert settings_store.get_zone_gpu_accel(db, 0) is True
        assert settings_store.get_zone_gpu_render_node(db, 0) == "/dev/dri/renderD128"
    finally:
        db.close()


def test_zone_docker_needs_master_toggle_and_zone_opt_in(client):
    setup_admin(client)
    zid, _ = enroll_zone(client)
    db = SessionLocal()
    try:
        assert settings_store.get_zone_docker(db, zid) is False
    finally:
        db.close()
    _set_zone(zid, docker_enabled=True)
    db = SessionLocal()
    try:
        assert settings_store.get_zone_docker(db, zid) is False  # master toggle still off
    finally:
        db.close()
    client.put("/api/admin/settings", json={"workspace_docker": True})
    db = SessionLocal()
    try:
        assert settings_store.get_zone_docker(db, zid) is True
        assert settings_store.get_zone_docker(db, 0) is True
    finally:
        db.close()


# ── zone settings API ──────────────────────────────────────────────────────

def test_patch_zone_gpu_settings_and_clear(client, no_agent):
    setup_admin(client)
    zid, _ = enroll_zone(client)
    r = client.patch(
        f"/api/admin/zones/{zid}",
        json={"gpu_accel": True, "gpu_render_node": "/dev/dri/renderD129", "gpu_render_gid": 44},
    )
    assert r.status_code == 200, r.text
    z = r.json()
    assert (z["gpu_accel"], z["gpu_render_node"], z["gpu_render_gid"]) == (True, "/dev/dri/renderD129", 44)

    # null clears the overrides (inherit again); name is untouched.
    r = client.patch(
        f"/api/admin/zones/{zid}",
        json={"gpu_accel": None, "gpu_render_node": "", "gpu_render_gid": None},
    )
    assert r.status_code == 200, r.text
    z = r.json()
    assert (z["gpu_accel"], z["gpu_render_node"], z["gpu_render_gid"]) == (None, None, None)
    assert z["name"] == "LAN"


def test_patch_zone_rejects_bad_render_node(client):
    setup_admin(client)
    zid, _ = enroll_zone(client)
    r = client.patch(f"/api/admin/zones/{zid}", json={"gpu_render_node": "/dev/sda; reboot"})
    assert r.status_code == 400, r.text


def test_patch_zone_docker_enabled_asks_agent(client, no_agent):
    setup_admin(client)
    zid, _ = enroll_zone(client)
    r = client.patch(f"/api/admin/zones/{zid}", json={"docker_enabled": True})
    assert r.status_code == 200, r.text
    assert r.json()["docker_enabled"] is True
    assert no_agent == [zid]


def test_local_zone_settings_point_to_global(client):
    setup_admin(client)
    r = client.patch("/api/admin/zones/0", json={"docker_enabled": True})
    assert r.status_code == 400
    assert "Administration → Settings" in r.text


# ── policies for the workspace modals ──────────────────────────────────────

def test_gpu_and_docker_policy_per_zone(client):
    zid = _dind_admin(client)
    client.put("/api/admin/settings", json={"workspace_gpu_accel": True})
    _set_zone(zid, gpu_accel=False)
    assert client.get("/api/workspaces/gpu-policy").json() == {"enabled": True}
    assert client.get(f"/api/workspaces/gpu-policy?zone_id={zid}").json() == {"enabled": False}
    assert client.get("/api/workspaces/docker-policy").json() == {"enabled": True}
    assert client.get(f"/api/workspaces/docker-policy?zone_id={zid}").json() == {"enabled": False}
    _set_zone(zid, docker_enabled=True)
    assert client.get(f"/api/workspaces/docker-policy?zone_id={zid}").json() == {"enabled": True}


# ── DinD validation on zones ───────────────────────────────────────────────

def test_create_docker_on_zone_without_opt_in_rejected(client, no_agent):
    zid = _dind_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    r = client.post(
        "/api/workspaces",
        json={"name": "dev", "image_id": image_id, "use_docker": True, "zone_id": zid},
    )
    assert r.status_code == 400, r.text
    assert "not enabled on zone 'Z'" in r.text


def test_create_docker_on_zone_whose_agent_refuses_rejected(client, no_agent):
    zid = _dind_admin(client)
    _set_zone(zid, docker_enabled=True, agent_allows_docker=False)
    image_id = add_image(name="Desktop", image_type="desktop")
    r = client.post(
        "/api/workspaces",
        json={"name": "dev", "image_id": image_id, "use_docker": True, "zone_id": zid},
    )
    assert r.status_code == 400, r.text
    assert "agent does not allow it" in r.text


def test_create_docker_on_opted_in_zone_accepted(client, no_agent, fake_docker_manager):
    zid = _dind_admin(client)
    _set_zone(zid, docker_enabled=True, agent_allows_docker=True)
    image_id = add_image(name="Desktop", image_type="desktop")
    r = client.post(
        "/api/workspaces",
        json={"name": "dev", "image_id": image_id, "use_docker": True, "zone_id": zid},
    )
    assert r.status_code == 201, r.text
    assert r.json()["use_docker"] is True


def _stopped_dind_ws_on_zone(client, zid) -> int:
    """A DinD workspace that ended up on a zone without DinD, the way a
    migration did it before migrate checked."""
    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post(
        "/api/workspaces", json={"name": "dev", "image_id": image_id, "use_docker": True}
    ).json()
    _set_ws(ws["id"], zone_id=zid, status="stopped")
    return ws["id"]


def test_migrated_dind_workspace_stays_editable(client, no_agent):
    """Regression: every edit of a DinD workspace on a zone without DinD failed
    with "Docker-in-Docker is only available on the local zone", and the edit
    form hides the Docker toggle there, so it could not even be turned off."""
    zid = _dind_admin(client)
    ws_id = _stopped_dind_ws_on_zone(client, zid)

    # The edit form always sends use_docker back as it found it.
    r = client.patch(f"/api/workspaces/{ws_id}", json={"name": "renamed", "use_docker": True})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "renamed"

    r = client.patch(f"/api/workspaces/{ws_id}", json={"use_docker": False})
    assert r.status_code == 200, r.text
    assert r.json()["use_docker"] is False

    # Turning it back on is gated again.
    r = client.patch(f"/api/workspaces/{ws_id}", json={"use_docker": True})
    assert r.status_code == 400, r.text
    assert "not enabled on zone" in r.text


def test_migrate_dind_workspace_to_zone_without_dind_rejected(client, no_agent):
    zid = _dind_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post(
        "/api/workspaces", json={"name": "dev", "image_id": image_id, "use_docker": True}
    ).json()
    _set_ws(ws["id"], status="stopped")
    r = client.post(f"/api/workspaces/{ws['id']}/migrate", json={"target_zone_id": zid})
    assert r.status_code == 400, r.text
    assert "turn off Docker-in-Docker" in r.text
    assert _ws_row(ws["id"]).zone_id == 0

    _set_zone(zid, docker_enabled=True, agent_allows_docker=True)
    r = client.post(f"/api/workspaces/{ws['id']}/migrate", json={"target_zone_id": zid})
    assert r.status_code == 200, r.text


# ── launch uses the zone's values ──────────────────────────────────────────

@pytest.fixture
def ready_fake(monkeypatch):
    import server.docker_manager as dm

    monkeypatch.setattr("docker.from_env", lambda *a, **k: MagicMock())
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    monkeypatch.setattr(dm, "capture_preview_frame", lambda *a, **k: b"\xff\xd8frame")
    fake = MagicMock()
    fake.containers.run.return_value.id = "deadbeefcafe"
    fake.containers.run.return_value.attrs = {"State": {"Status": "running"}}
    fake.containers.get.return_value.exec_run.return_value = (0, b"")
    return fake


def _run_named(fake, name):
    for call in fake.containers.run.call_args_list:
        if call.kwargs.get("name") == name:
            return call
    return None


def test_launch_on_zone_uses_zone_render_node_and_gid(client, ready_fake):
    setup_admin(client)
    zid, _ = enroll_zone(client)
    # Global GPU is off; the zone turns it on with its own node and fallback GID.
    _set_zone(zid, gpu_accel=True, gpu_render_node="/dev/dri/renderD129", gpu_render_gid=44)
    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post(
        "/api/workspaces",
        json={"name": "g", "image_id": image_id, "zone_id": zid, "gpu_accel": True, "ephemeral": True},
    ).json()
    _set_ws(ws["id"], status="creating")

    dm = DockerManager.__new__(DockerManager)
    dm.zone_id = zid
    dm._client = ready_fake
    dm._render_gid_cache = {}
    dm._pull_image = lambda *a, **k: None
    probed = []
    dm._detect_render_gid = lambda node: probed.append(node)  # probe can't run -> None
    dm.launch_workspace(ws["id"])

    call = _run_named(ready_fake, f"cove-ws-{ws['id']}")
    assert call is not None, _ws_row(ws["id"]).error_message
    assert probed == ["/dev/dri/renderD129"]
    assert call.kwargs["devices"] == ["/dev/dri/renderD129:/dev/dri/renderD129"]
    assert call.kwargs["group_add"] == ["44"]
    assert call.kwargs["environment"]["DRINODE"] == "/dev/dri/renderD129"


# ── agent policy: the DinD sidecar ─────────────────────────────────────────

def _sidecar_body(ws_id=7, netns_owner="cove-ws-7"):
    """The exact create body docker-py builds for _launch_dind_sidecar."""
    dm = DockerManager.__new__(DockerManager)
    dm.zone_id = 1
    dm._client = MagicMock()
    dm._client.containers.get.side_effect = __import__("docker").errors.NotFound("x")
    dm._pull_image = lambda *a, **k: None
    dm._ensure_named_volume = lambda *a, **k: None
    ws = MagicMock(id=ws_id)
    root = str(get_settings().storage_path or (get_settings().data_dir / "workspaces"))
    dm._launch_dind_sidecar(ws, netns_owner, "docker:dind", config_source=f"{root}/alice/workspace-dev")
    kwargs = dict(dm._client.containers.run.call_args.kwargs)
    image = dm._client.containers.run.call_args.args[0]
    name = kwargs.pop("name")
    kwargs.pop("detach", None)
    args = _create_container_args({**kwargs, "image": image, "version": "1.41"})
    hc = args.pop("host_config")
    body = {k: v for k, v in args.items() if v is not None}
    body["HostConfig"] = dict(hc)
    return name, json.dumps(body).encode()


def test_sidecar_refused_without_host_opt_in():
    name, body = _sidecar_body()
    assert "privileged" in (check_create_policy(body, name=name) or "")


def test_sidecar_allowed_with_host_opt_in():
    name, body = _sidecar_body()
    assert name == "cove-dind-7"
    assert check_create_policy(body, name=name, allow_dind=True) is None


def test_privileged_outside_sidecar_shape_still_refused():
    name, body = _sidecar_body()
    # Wrong / missing name.
    assert check_create_policy(body, name="evil", allow_dind=True)
    assert check_create_policy(body, name=None, allow_dind=True)
    # Not in a workspace's netns.
    _, own_net = _sidecar_body(netns_owner="cove-agent")
    assert check_create_policy(own_net, name=name, allow_dind=True)
    hc_body = json.loads(body)
    for extra in (
        {"NetworkMode": "host"},
        {"CapAdd": ["SYS_ADMIN"]},
        {"Devices": [{"PathOnHost": "/dev/net/tun", "PathInContainer": "/dev/net/tun", "CgroupPermissions": "rwm"}]},
        {"Binds": ["/:/host"]},
        {"PidMode": "host"},
    ):
        b = json.loads(body)
        b["HostConfig"] = {**hc_body["HostConfig"], **extra}
        assert check_create_policy(json.dumps(b).encode(), name=name, allow_dind=True), extra


def test_exec_into_sidecar_only_with_host_opt_in():
    cmd = list(_RECREATE_CMD)
    ok = json.dumps({"Cmd": ["/bin/sh", "-c", "true"]}).encode()
    assert check_exec_policy("cove-dind-7", ok, cmd)
    assert check_exec_policy("cove-dind-7", ok, cmd, allow_dind=True) is None
    assert check_exec_policy("cove-dind-x", ok, cmd, allow_dind=True)
    priv = json.dumps({"Cmd": ["id"], "Privileged": True}).encode()
    assert check_exec_policy("cove-dind-7", priv, cmd, allow_dind=True)


@pytest.fixture
def agent_env(monkeypatch):
    def make(allow: bool):
        monkeypatch.setenv("COVE_AGENT_MODE", "1")
        # Nothing listens here: an allowed request fails upstream (502), a
        # refused one never leaves the agent (403).
        monkeypatch.setenv("COVE_AGENT_DOCKER_SOCKET_URL", "http://127.0.0.1:9")
        monkeypatch.setenv("COVE_AGENT_ALLOW_DIND", "1" if allow else "0")
        get_settings.cache_clear()
        return TestClient(main.create_app())

    yield make
    get_settings.cache_clear()


def test_proxy_reads_sidecar_name_from_query(agent_env):
    name, body = _sidecar_body()
    c = agent_env(True)
    r = c.post(f"/v1.41/containers/create?name={name}", content=body)
    assert r.status_code == 502, r.text  # passed the policy, upstream absent
    r = c.post("/v1.41/containers/create?name=other", content=body)
    assert r.status_code == 403, r.text
    r = c.post(f"/v1.41/containers/create?name={name}&name=other", content=body)
    assert r.status_code == 403, r.text
    assert c.get("/agent/health").json()["dind"] is True


def test_proxy_refuses_sidecar_without_host_opt_in(agent_env):
    name, body = _sidecar_body()
    c = agent_env(False)
    r = c.post(f"/v1.41/containers/create?name={name}", content=body)
    assert r.status_code == 403, r.text
    assert c.get("/agent/health").json()["dind"] is False


# ── installer ──────────────────────────────────────────────────────────────

def test_installer_carries_zone_dind_opt_in(client):
    setup_admin(client)
    zid, _ = enroll_zone(client)
    token = client.post(f"/api/admin/zones/{zid}/enroll-token").json()["token"]
    script = client.get(f"/install.sh?token={token}").text
    assert 'ALLOW_DIND="0"' in script
    assert "COVE_AGENT_ALLOW_DIND=${ALLOW_DIND:-0}" in script

    _set_zone(zid, docker_enabled=True)
    token = client.post(f"/api/admin/zones/{zid}/enroll-token").json()["token"]
    script = client.get(f"/install.sh?token={token}").text
    assert 'ALLOW_DIND="1"' in script
    assert "ALLOW_DIND=${ALLOW_DIND}" in script  # written to the agent's .env
