"""Phase 4: Traefik dynamic config for remote zones, agent ForwardAuth, and the
dedicated stream-signing key provisioned at enrollment."""

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

import server.main as main
from server.config import get_settings
from server.db import SessionLocal
from server.models import Workspace
from server.tests.helpers import add_image, setup_admin


def _make_csr() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cove-zone")]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture
def with_settings(monkeypatch):
    """Apply env overrides and rebuild the cached Settings; restore afterwards."""

    def _apply(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, str(v))
        get_settings.cache_clear()

    yield _apply
    get_settings.cache_clear()


def _enroll_zone(client, host="10.0.0.5"):
    zid = client.post(
        "/api/admin/zones", json={"name": "LAN", "endpoint_host": host}
    ).json()["id"]
    token = client.post(f"/api/admin/zones/{zid}/enroll-token").json()["token"]
    resp = client.post(
        f"/api/zones/enroll?token={token}",
        json={"csr_pem": _make_csr(), "endpoint_host": host, "stream_port": 8443},
    )
    assert resp.status_code == 200, resp.text
    return zid, resp.json()


def _set_running(ws_id: int):
    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        ws.status = "running"
        ws.container_id = "deadbeef"
        db.commit()
    finally:
        db.close()


# ── Enrollment provisioning ────────────────────────────────────────────────

def test_enroll_provisions_stream_key(client):
    setup_admin(client)
    _, body = _enroll_zone(client)
    # The agent receives the stream-signing key (so it can ForwardAuth locally).
    assert body["stream_signing_key"]
    assert body["stream_signing_key"] == get_settings().get_stream_signing_key()


# ── Traefik dynamic config ─────────────────────────────────────────────────

def test_traefik_config_routes_remote_workspace(client):
    setup_admin(client)
    zid, _ = _enroll_zone(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post(
        "/api/workspaces", json={"name": "ws", "image_id": image_id, "zone_id": zid}
    ).json()
    _set_running(ws["id"])

    cfg = client.get("/api/internal/traefik-config").json()
    http = cfg["http"]
    name = f"cove-ws-{ws['id']}"
    assert name in http["routers"]
    # Service points at the agent's stream port over HTTPS with a per-zone mTLS transport.
    svc = http["services"][name]["loadBalancer"]
    assert svc["servers"][0]["url"] == "https://10.0.0.5:8443"
    transport = svc["serversTransport"]
    assert transport == f"cove-zone-{zid}"
    t = http["serversTransports"][transport]
    assert t["rootCAs"] == [f"/zone-certs/{zid}/ca.crt"]
    assert t["certificates"][0]["certFile"] == f"/zone-certs/{zid}/client.crt"
    # ForwardAuth (cove-auth) is kept on the central edge.
    assert "cove-auth@docker" in http["routers"][name]["middlewares"]


def test_traefik_config_excludes_local_and_stopped(client):
    setup_admin(client)
    zid, _ = _enroll_zone(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    # A remote workspace left in "creating" (not running) is not routed.
    client.post("/api/workspaces", json={"name": "pending", "image_id": image_id, "zone_id": zid})
    # A running LOCAL workspace is discovered by the Docker provider, not here.
    local = client.post("/api/workspaces", json={"name": "local", "image_id": image_id}).json()
    _set_running(local["id"])

    cfg = client.get("/api/internal/traefik-config").json()
    assert cfg["http"]["routers"] == {}


# ── Agent ForwardAuth (defense-in-depth) ───────────────────────────────────

def _agent_client(with_settings, domain="ws.example.com", key="streamkey-xyz"):
    with_settings(
        COVE_AGENT_MODE="1", COVE_WORKSPACE_DOMAIN=domain, COVE_STREAM_SIGNING_KEY=key
    )
    return TestClient(main.create_app())


def test_agent_forward_auth_accepts_valid_stream_cookie(with_settings):
    c = _agent_client(with_settings)
    from server.security import create_stream_token

    token = create_stream_token(5, "pub123")
    c.cookies.set("cove_stream", token)
    r = c.get("/agent/auth/forward", headers={"X-Forwarded-Host": "pub123.ws.example.com"})
    assert r.status_code == 200, r.text


def test_agent_forward_auth_rejects_missing_cookie(with_settings):
    c = _agent_client(with_settings)
    r = c.get("/agent/auth/forward", headers={"X-Forwarded-Host": "pub123.ws.example.com"})
    assert r.status_code == 401


def test_agent_forward_auth_rejects_wrong_workspace(with_settings):
    c = _agent_client(with_settings)
    from server.security import create_stream_token

    token = create_stream_token(5, "pub123")
    c.cookies.set("cove_stream", token)
    # Token is scoped to pub123 but the host resolves to a different workspace.
    r = c.get("/agent/auth/forward", headers={"X-Forwarded-Host": "other.ws.example.com"})
    assert r.status_code == 401
