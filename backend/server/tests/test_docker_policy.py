"""The agent's Docker create-policy filter and the proxy that enforces it."""

import json
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

import server.main as main
from server.config import get_settings
from server.docker_policy import check_create_policy


def _root() -> str:
    s = get_settings()
    return str(s.storage_path or (s.data_dir / "workspaces"))


def _create(**hostconfig) -> bytes:
    return json.dumps({"Image": "x", "HostConfig": hostconfig}).encode()


# ── policy: allowed (Cove's own envelope) ──────────────────────────────────

def test_policy_allows_minimal():
    assert check_create_policy(_create()) is None


def test_policy_allows_storage_root_bind():
    assert check_create_policy(_create(Binds=[f"{_root()}/alice/workspace-x:/config:rw"])) is None


def test_policy_allows_named_volume():
    assert check_create_policy(_create(Binds=["cove-ts-state-5:/var/lib/tailscale"])) is None


def test_policy_allows_net_admin_and_tun():
    body = _create(CapAdd=["NET_ADMIN"], Devices=[{"PathOnHost": "/dev/net/tun"}])
    assert check_create_policy(body) is None


def test_policy_allows_hardening_cap_set():
    # Exactly what _build_hardening adds back after cap_drop=ALL.
    body = _create(
        CapDrop=["ALL"],
        CapAdd=["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID", "KILL"],
    )
    assert check_create_policy(body) is None


def test_policy_allows_cove_container_network():
    assert check_create_policy(_create(NetworkMode="container:cove-ts-5")) is None


# ── policy: denied (host-escape vectors) ───────────────────────────────────

@pytest.mark.parametrize("hc,needle", [
    ({"Privileged": True}, "privileged"),
    ({"NetworkMode": "host"}, "NetworkMode=host"),
    ({"PidMode": "host"}, "PidMode=host"),
    ({"NetworkMode": "container:something-else"}, "network_mode"),
    ({"CapAdd": ["SYS_ADMIN"]}, "capability"),
    ({"Devices": [{"PathOnHost": "/dev/sda"}]}, "device"),
    ({"Binds": ["/etc:/etc"]}, "outside"),
    ({"Binds": ["/var/run/docker.sock:/var/run/docker.sock"]}, "outside"),
    ({"Mounts": [{"Type": "bind", "Source": "/root", "Target": "/root"}]}, "outside"),
])
def test_policy_denies(hc, needle):
    reason = check_create_policy(_create(**hc))
    assert reason is not None and needle in reason


def test_policy_rejects_garbage():
    assert check_create_policy(b"not json") is not None


# ── proxy ──────────────────────────────────────────────────────────────────

@pytest.fixture
def agent_app(monkeypatch):
    monkeypatch.setenv("COVE_AGENT_MODE", "1")
    get_settings.cache_clear()
    yield main.create_app()
    get_settings.cache_clear()


def test_proxy_blocks_privileged_create(agent_app):
    c = TestClient(agent_app)
    r = c.post("/v1.41/containers/create", content=_create(Privileged=True))
    assert r.status_code == 403, r.text
    assert "policy" in r.json()["message"]


def test_proxy_rejects_non_docker_path(agent_app):
    c = TestClient(agent_app)
    # The catch-all only serves the Docker API surface.
    assert c.get("/v1.41/_ping" if False else "/not/docker").status_code == 404


def test_proxy_passthrough_to_local_socket(monkeypatch):
    """A non-create Docker call is forwarded to the local socket-proxy URL."""
    upstream = FastAPI()

    @upstream.get("/v1.41/_ping")
    def _ping():
        return PlainTextResponse("OK")

    config = uvicorn.Config(upstream, host="127.0.0.1", port=0, log_level="error")
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
        c = TestClient(main.create_app())
        r = c.get("/v1.41/_ping")
        assert r.status_code == 200, r.text
        assert r.text == "OK"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        get_settings.cache_clear()
