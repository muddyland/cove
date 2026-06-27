"""Phase 5: file browser proxies to a remote zone's agent file API.

The agent API is exercised in-process via an ASGI transport (no real mTLS), so
these tests verify the full proxy round-trip: control plane endpoint -> agent
endpoint -> shared local storage.
"""

import threading
import time

import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI

from server.routers import agent as agent_router
from server.tests.helpers import setup_admin


def _make_csr() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cove-zone")]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode()


def _enroll_zone(client, host="10.0.0.5"):
    zid = client.post(
        "/api/admin/zones", json={"name": "LAN", "endpoint_host": host}
    ).json()["id"]
    token = client.post(f"/api/admin/zones/{zid}/enroll-token").json()["token"]
    client.post(
        f"/api/zones/enroll?token={token}",
        json={"csr_pem": _make_csr(), "endpoint_host": host},
    )
    return zid


@pytest.fixture
def agent_transport(monkeypatch):
    """Run the agent app on a real localhost port and point the control plane's
    (otherwise mTLS) agent client at it. A real server is needed because files.py
    uses a synchronous httpx client, which can't drive an in-process ASGI app."""
    app = FastAPI()
    app.include_router(agent_router.router)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]

    def _fake_client(zone, **kwargs):
        return httpx.Client(base_url=f"http://127.0.0.1:{port}")

    monkeypatch.setattr("server.docker_manager.zone_agent_client", _fake_client)
    yield
    server.should_exit = True
    thread.join(timeout=5)


def test_list_proxies_to_agent(client, agent_transport):
    setup_admin(client)
    zid = _enroll_zone(client)
    # Write a file via the local (zone 0) path; the agent shares the same storage
    # root in-process, so it lists the same user dir.
    client.post("/api/files/upload", files={"file": ("hello.txt", b"hi there")}, data={"path": ""})

    resp = client.get(f"/api/files?zone_id={zid}")
    assert resp.status_code == 200, resp.text
    names = [e["name"] for e in resp.json()["entries"]]
    assert "hello.txt" in names


def test_upload_proxies_to_agent(client, agent_transport):
    setup_admin(client)
    zid = _enroll_zone(client)
    resp = client.post(
        f"/api/files/upload?zone_id={zid}",
        files={"file": ("remote.txt", b"payload")},
        data={"path": ""},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "remote.txt"
    # Readable back through the proxy.
    listing = client.get(f"/api/files?zone_id={zid}").json()
    assert "remote.txt" in [e["name"] for e in listing["entries"]]


def test_download_proxies_to_agent(client, agent_transport):
    setup_admin(client)
    zid = _enroll_zone(client)
    client.post(
        f"/api/files/upload?zone_id={zid}",
        files={"file": ("d.txt", b"download-me")},
        data={"path": ""},
    )
    resp = client.get(f"/api/files/download?zone_id={zid}&path=d.txt")
    assert resp.status_code == 200, resp.text
    assert resp.content == b"download-me"


def test_delete_proxies_to_agent(client, agent_transport):
    setup_admin(client)
    zid = _enroll_zone(client)
    client.post(
        f"/api/files/upload?zone_id={zid}",
        files={"file": ("gone.txt", b"x")},
        data={"path": ""},
    )
    resp = client.request("DELETE", f"/api/files?zone_id={zid}&path=gone.txt")
    assert resp.status_code == 204, resp.text
    listing = client.get(f"/api/files?zone_id={zid}").json()
    assert "gone.txt" not in [e["name"] for e in listing["entries"]]


def test_agent_file_api_rejects_bad_username():
    """The agent validates the control-plane-supplied username can't escape root."""
    app = FastAPI()
    app.include_router(agent_router.router)
    from fastapi.testclient import TestClient

    c = TestClient(app)
    resp = c.get("/agent/files", params={"username": "../etc", "path": ""})
    assert resp.status_code == 400


def test_remote_file_op_requires_enrolled_zone(client):
    """A zone without mTLS material can't be browsed remotely."""
    setup_admin(client)
    zid = client.post(
        "/api/admin/zones", json={"name": "plain", "endpoint_host": "1.2.3.4"}
    ).json()["id"]
    # Manually-registered (plain TCP) zone has no mTLS certs yet.
    resp = client.get(f"/api/files?zone_id={zid}")
    assert resp.status_code == 409, resp.text
