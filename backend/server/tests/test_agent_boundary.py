"""1.1.0 zone-agent boundary hardening: the Docker create policy as an
allow-list, policy on exec/archive/volumes/networks/rename, the split relay
(edge) cert, subpath-mode relay auth, and enrollment endpoint pinning."""

import json
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.testclient import TestClient

import server.main as main
from server.agent_update import _RECREATE_CMD
from server.config import get_settings
from server.db import SessionLocal
from server.docker_policy import (
    check_archive_policy,
    check_create_policy,
    check_exec_policy,
    check_network_connect_policy,
    check_network_create_policy,
    check_volume_create_policy,
)
from server.models import Workspace, Zone
from server.security import create_stream_token, decode_stream_token
from server.tests.helpers import add_image, enroll_zone, make_csr, setup_admin


def _root() -> str:
    s = get_settings()
    return str(s.storage_path or (s.data_dir / "workspaces"))


def _create(hostconfig=None, **top) -> bytes:
    body = {"Image": "x", **top}
    if hostconfig is not None:
        body["HostConfig"] = hostconfig
    return json.dumps(body).encode()


# ── create policy: the bypasses the audit found ────────────────────────────

@pytest.mark.parametrize("hc,needle", [
    ({"VolumesFrom": ["cove-agent-updater"]}, "VolumesFrom"),
    ({"DeviceCgroupRules": ["b *:* rwm"]}, "DeviceCgroupRules"),
    ({"DeviceRequests": [{"Count": -1}]}, "DeviceRequests"),
    ({"MaskedPaths": []}, "MaskedPaths"),
    ({"ReadonlyPaths": []}, "ReadonlyPaths"),
    ({"SecurityOpt": ["systempaths=unconfined"]}, "security option"),
    ({"SecurityOpt": ["seccomp=unconfined"]}, "security option"),
    ({"PidMode": "container:cove-agent-updater"}, "PidMode"),
    ({"IpcMode": "container:cove-agent"}, "IpcMode"),
    ({"UsernsMode": "host"}, "UsernsMode"),
    ({"NetworkMode": "container:cove-agent"}, "network_mode"),
    ({"NetworkMode": "container:cove-traefik"}, "network_mode"),
    ({"NetworkMode": "cove-agent"}, "not a Cove workspace network"),
    ({"Sysctls": {"net.ipv4.ip_forward": "1"}}, "sysctl"),
    ({"Binds": ["notcove:/x"]}, "not a Cove volume"),
    ({"Binds": ["cove-agent-data:/x"]}, "not a Cove volume"),
    ({"Mounts": [{"Type": "volume", "Source": "cove-ts-state-1", "Target": "/x",
                  "VolumeOptions": {"DriverConfig": {"Name": "local",
                                                     "Options": {"type": "none", "o": "bind", "device": "/"}}}}]},
     "driver options"),
    ({"Mounts": [{"Type": "volume", "Source": "evil", "Target": "/x"}]}, "not a Cove volume"),
    ({"Mounts": [{"Type": "npipe", "Source": "x", "Target": "/x"}]}, "mount type"),
    ({"Runtime": "nvidia"}, "Runtime"),
    ({"Privileged": True}, "privileged"),
])
def test_create_policy_denies_bypasses(hc, needle):
    reason = check_create_policy(_create(hc))
    assert reason is not None and needle.lower() in reason.lower(), reason


def test_create_policy_keys_are_case_insensitive():
    # dockerd matches JSON keys case-insensitively; so must the policy.
    body = json.dumps({"Image": "x", "hostconfig": {"privileged": True}}).encode()
    assert "privileged" in (check_create_policy(body) or "")
    body = json.dumps({"Image": "x", "HostConfig": {"PRIVILEGED": True}}).encode()
    assert "privileged" in (check_create_policy(body) or "")


def test_create_policy_rejects_duplicate_keys():
    # Last-duplicate-wins at the daemon: a benign first copy must not mask a
    # hostile second one.
    body = b'{"Image":"x","HostConfig":{"Privileged":false},"hostconfig":{"Privileged":true}}'
    assert check_create_policy(body) is not None
    body = b'{"Image":"x","HostConfig":{"Binds":["/etc:/etc"],"binds":[]}}'
    assert check_create_policy(body) is not None


def test_create_policy_allows_coves_own_shapes():
    root = _root()
    # A workspace container.
    assert check_create_policy(_create({
        "Binds": [f"{root}/alice/workspace-x:/config:rw", f"{root}/.cove-scripts/x.sh:/custom-cont-init.d/x.sh:ro"],
        "NetworkMode": "cove-ws-net-5", "CapDrop": ["ALL"],
        "CapAdd": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID", "KILL"],
        "SecurityOpt": ["no-new-privileges:true"], "ShmSize": 1073741824,
        "NanoCpus": 2000000000, "Memory": 4294967296, "PidsLimit": 4096,
        "Dns": ["1.1.1.1"],
    })) is None
    # The egress guard helper / readiness probe joining the workspace netns.
    assert check_create_policy(_create({
        "NetworkMode": "container:cove-ws-5", "CapAdd": ["NET_ADMIN"], "AutoRemove": True,
    })) is None
    # The Tailscale sidecar.
    assert check_create_policy(_create({
        "CapAdd": ["NET_ADMIN"], "Devices": [{"PathOnHost": "/dev/net/tun", "PathInContainer": "/dev/net/tun"}],
        "Binds": ["cove-ts-state-5:/var/lib/tailscale:rw"], "NetworkMode": "cove-ws-net-5",
    })) is None
    # A workspace joining its sidecar's netns.
    assert check_create_policy(_create({"NetworkMode": "container:cove-gluetun-5"})) is None
    # docker-py's default NetworkMode.
    assert check_create_policy(_create({"NetworkMode": "default"})) is None


# ── volume / network / exec / archive policies ─────────────────────────────

def test_volume_create_policy():
    assert check_volume_create_policy(json.dumps({"Name": "cove-ts-state-5"}).encode()) is None
    assert check_volume_create_policy(json.dumps({"Name": "cove-dind-state-5", "Driver": "local"}).encode()) is None
    assert check_volume_create_policy(json.dumps({"Name": "evil"}).encode())
    assert check_volume_create_policy(json.dumps({
        "Name": "cove-ts-state-5", "DriverOpts": {"type": "none", "o": "bind", "device": "/"}
    }).encode())
    assert check_volume_create_policy(json.dumps({"Name": "cove-ts-state-5", "Driver": "nfs"}).encode())


def test_network_policies():
    assert check_network_create_policy(json.dumps({"Name": "cove-ws-net-5", "Driver": "bridge"}).encode()) is None
    assert check_network_create_policy(json.dumps({"Name": "evil"}).encode())
    assert check_network_create_policy(json.dumps({"Name": "cove-ws-net-5", "Driver": "macvlan"}).encode())
    assert check_network_connect_policy(json.dumps({"Container": "cove-traefik"}).encode()) is None
    assert check_network_connect_policy(json.dumps({"Container": "cove-ws-5"}).encode()) is None
    assert check_network_connect_policy(json.dumps({"Container": "cove-agent-sockproxy"}).encode())
    assert check_network_connect_policy(json.dumps({"Container": "cove-agent"}).encode())


def test_exec_policy():
    cmd = list(_RECREATE_CMD)
    ok = json.dumps({"Cmd": ["tailscale", "ip", "-4"], "Privileged": False}).encode()
    assert check_exec_policy("cove-ts-5", ok, cmd) is None
    assert check_exec_policy("cove-ws-5", ok, cmd) is None
    assert check_exec_policy("cove-gluetun-5", ok, cmd) is None
    # Never into the agent, the proxies, or Traefik.
    for name in ("cove-agent", "cove-agent-sockproxy", "cove-traefik", "random"):
        assert check_exec_policy(name, ok, cmd)
    # Privileged exec is out even into a workspace.
    assert check_exec_policy("cove-ws-5", json.dumps({"Cmd": ["id"], "Privileged": True}).encode(), cmd)
    # The updater accepts exactly the recreate command, nothing else.
    assert check_exec_policy("cove-agent-updater", json.dumps({"Cmd": cmd}).encode(), cmd) is None
    assert check_exec_policy("cove-agent-updater", json.dumps({"Cmd": ["sh"]}).encode(), cmd)
    assert check_exec_policy("cove-agent-updater", json.dumps({"Cmd": cmd + ["--build"]}).encode(), cmd)


def test_archive_policy():
    assert check_archive_policy("cove-ws-5") is None
    for name in ("cove-agent-updater", "cove-ts-5", "cove-traefik", "cove-agent"):
        assert check_archive_policy(name)


# ── proxy: name resolution + versioned-path normalisation ──────────────────

@pytest.fixture
def upstream(monkeypatch):
    """A fake local docker-socket-proxy that records what reaches it."""
    app = FastAPI()
    hits: list[str] = []
    names = {"abc123": "/cove-agent-updater", "def456": "/cove-ws-7"}

    @app.get("/v1.41/containers/{ref}/json")
    def inspect(ref: str):
        if ref in names:
            return {"Name": names[ref]}
        if ref.startswith("cove-"):
            return {"Name": "/" + ref}
        return JSONResponse({"message": "no such container"}, status_code=404)

    @app.api_route("/v1.41/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def catch(rest: str, request: Request):
        hits.append(f"{request.method} /{rest}")
        return PlainTextResponse("OK")

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    monkeypatch.setenv("COVE_AGENT_MODE", "1")
    monkeypatch.setenv("COVE_AGENT_DOCKER_SOCKET_URL", f"http://127.0.0.1:{port}")
    get_settings.cache_clear()
    try:
        yield TestClient(main.create_app()), hits
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        get_settings.cache_clear()


def test_proxy_blocks_exec_into_updater_by_id(upstream):
    c, hits = upstream
    r = c.post("/v1.41/containers/abc123/exec", content=json.dumps({"Cmd": ["sh"]}))
    assert r.status_code == 403, r.text
    assert not any("exec" in h for h in hits)


def test_proxy_allows_recreate_exec_and_workspace_exec(upstream):
    c, hits = upstream
    r = c.post("/v1.41/containers/abc123/exec", content=json.dumps({"Cmd": list(_RECREATE_CMD)}))
    assert r.status_code == 200, r.text
    r = c.post("/v1.41/containers/def456/exec", content=json.dumps({"Cmd": ["python3", "-c", "x"]}))
    assert r.status_code == 200, r.text
    assert "POST /containers/abc123/exec" in hits and "POST /containers/def456/exec" in hits


def test_proxy_blocks_archive_rename_and_bad_volume(upstream):
    c, hits = upstream
    assert c.put("/v1.41/containers/abc123/archive?path=/", content=b"x").status_code == 403
    assert c.get("/v1.41/containers/abc123/archive?path=/etc").status_code == 403
    assert c.post("/v1.41/containers/def456/rename?name=cove-ws-1").status_code == 403
    assert c.post("/v1.41/volumes/create", content=json.dumps({"Name": "x"})).status_code == 403
    assert c.post(
        "/v1.41/networks/cove-ws-net-1/connect", content=json.dumps({"Container": "cove-agent"})
    ).status_code == 403
    assert hits == []
    # Legit shapes pass through.
    assert c.put("/v1.41/containers/def456/archive?path=/config", content=b"x").status_code == 200
    assert c.post("/v1.41/volumes/create", content=json.dumps({"Name": "cove-ts-state-1"})).status_code == 200


def test_proxy_versioned_paths_cannot_reach_unlisted_families(upstream):
    c, hits = upstream
    for path in ("/v1.41/build", "/v1.41/commit", "/v1.41/plugins", "/v1.41/swarm/init", "/v1.41/session"):
        assert c.post(path).status_code == 404, path
    assert hits == []
    assert c.get("/v1.41/_ping").status_code == 200
    assert c.get("/v1.41/system/df").status_code == 200


def test_proxy_unknown_container_is_rejected(upstream):
    c, hits = upstream
    assert c.post("/v1.41/containers/nope/exec", content=json.dumps({"Cmd": ["id"]})).status_code == 403
    assert hits == []


# ── enrollment: the endpoint is pinned to what the admin set ───────────────

def test_enroll_rejects_endpoint_override(client):
    setup_admin(client)
    zid = client.post("/api/admin/zones", json={"name": "LAN", "endpoint_host": "10.0.0.5"}).json()["id"]
    token = client.post(f"/api/admin/zones/{zid}/enroll-token").json()["token"]
    r = client.post(
        f"/api/zones/enroll?token={token}",
        json={"csr_pem": make_csr(), "endpoint_host": "evil.example", "endpoint_port": 8443},
    )
    assert r.status_code == 400, r.text
    # The token was NOT consumed by the rejected attempt.
    r = client.post(
        f"/api/zones/enroll?token={token}",
        json={"csr_pem": make_csr(), "endpoint_host": "10.0.0.5", "endpoint_port": 8443},
    )
    assert r.status_code == 200, r.text
    assert r.json()["edge_client_cn"] == f"cove-edge-{client.get(f'/api/admin/zones/{zid}').json()['public_id']}"


def test_enroll_bad_csr_does_not_consume_token(client):
    setup_admin(client)
    zid = client.post("/api/admin/zones", json={"name": "LAN", "endpoint_host": "10.0.0.5"}).json()["id"]
    token = client.post(f"/api/admin/zones/{zid}/enroll-token").json()["token"]
    r = client.post(
        f"/api/zones/enroll?token={token}",
        json={"csr_pem": "garbage", "endpoint_host": "10.0.0.5", "endpoint_port": 8443},
    )
    assert r.status_code == 400
    r = client.post(
        f"/api/zones/enroll?token={token}",
        json={"csr_pem": make_csr(), "endpoint_host": "10.0.0.5", "endpoint_port": 8443},
    )
    assert r.status_code == 200, r.text


def test_enroll_issues_edge_cert_and_rotate_renews_it(client):
    setup_admin(client)
    zid, _ = enroll_zone(client)
    db = SessionLocal()
    try:
        zone = db.get(Zone, zid)
        assert zone.edge_cert_pem and zone.edge_key_enc
        before = zone.edge_cert_pem
    finally:
        db.close()
    assert client.post(f"/api/admin/zones/{zid}/rotate-client-cert").status_code == 200
    db = SessionLocal()
    try:
        assert db.get(Zone, zid).edge_cert_pem != before
    finally:
        db.close()


def test_zone_status_not_client_settable(client):
    setup_admin(client)
    zid = client.post("/api/admin/zones", json={"name": "LAN", "endpoint_host": "10.0.0.5"}).json()["id"]
    client.patch(f"/api/admin/zones/{zid}", json={"status": "enrolled"})
    assert client.get(f"/api/admin/zones/{zid}").json()["status"] == "pending"


def test_no_plain_tcp_docker_client_by_default(client):
    from server.docker_manager import DockerManager, reset_docker_manager

    setup_admin(client)
    zid = client.post("/api/admin/zones", json={"name": "LAN", "endpoint_host": "10.0.0.5"}).json()["id"]
    reset_docker_manager(zid)
    with pytest.raises(RuntimeError, match="not enrolled for mTLS"):
        DockerManager(zone_id=zid)


# ── relay: subpath mode routes and authenticates at the agent ──────────────

def _set_running(ws_id: int):
    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        ws.status = "running"
        ws.container_id = "deadbeef"
        db.commit()
    finally:
        db.close()


def test_traefik_config_subpath_does_not_strip_and_forwards_stream_auth(client):
    setup_admin(client)
    zid, _ = enroll_zone(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post("/api/workspaces", json={"name": "ws", "image_id": image_id, "zone_id": zid}).json()
    _set_running(ws["id"])
    http = client.get("/api/internal/traefik-config").json()["http"]
    router = http["routers"][f"cove-ws-{ws['id']}"]
    # The agent's own router expects the /workspace/<id>/ prefix; stripping here
    # made every relayed request miss it and land on the agent's catch-all.
    assert not any(m.endswith("-strip") for m in router["middlewares"])
    assert f"cove-ws-{ws['id']}-strip" not in http["middlewares"]
    assert "X-Cove-Stream-Auth" in http["middlewares"]["cove-auth"]["forwardAuth"]["authResponseHeaders"]


def test_central_forward_auth_hands_remote_subpath_a_stream_token(client):
    setup_admin(client)
    zid, _ = enroll_zone(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post("/api/workspaces", json={"name": "ws", "image_id": image_id, "zone_id": zid}).json()
    local = client.post("/api/workspaces", json={"name": "local", "image_id": image_id}).json()
    _set_running(ws["id"])
    _set_running(local["id"])

    r = client.get("/api/auth/forward", headers={"X-Forwarded-Uri": f"/workspace/{ws['public_id']}/"})
    assert r.status_code == 200, r.text
    tok = r.headers.get("X-Cove-Stream-Auth")
    assert tok and decode_stream_token(tok)["ws"] == ws["public_id"]
    # A local workspace's container never receives a stream credential.
    r = client.get("/api/auth/forward", headers={"X-Forwarded-Uri": f"/workspace/{local['public_id']}/"})
    assert r.status_code == 200 and "X-Cove-Stream-Auth" not in r.headers


def test_agent_forward_auth_subpath_verifies_relayed_token(monkeypatch):
    monkeypatch.setenv("COVE_AGENT_MODE", "1")
    monkeypatch.setenv("COVE_STREAM_SIGNING_KEY", "streamkey-xyz")
    monkeypatch.delenv("COVE_WORKSPACE_DOMAIN", raising=False)
    get_settings.cache_clear()
    try:
        c = TestClient(main.create_app())
        tok = create_stream_token(1, "abc")
        hdr = {"X-Forwarded-Uri": "/workspace/abc/", "X-Cove-Stream-Auth": tok}
        assert c.get("/agent/auth/forward", headers=hdr).status_code == 200
        hdr["X-Forwarded-Uri"] = "/workspace/other/"
        assert c.get("/agent/auth/forward", headers=hdr).status_code == 401
        missing = c.get("/agent/auth/forward", headers={"X-Forwarded-Uri": "/workspace/abc/"})
        assert missing.status_code == 401
    finally:
        get_settings.cache_clear()
