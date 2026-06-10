"""Tests for the on-demand diagnostics endpoints (tailscale status + logs).

The DockerManager is stubbed by the ``fake_docker_manager`` fixture, so these
exercise the router wiring (ownership, routing-flag guards, available flag)
without a real daemon.
"""

from server.db import SessionLocal
from server.models import Workspace
from server.tests.helpers import (
    add_image,
    auth_header,
    create_user_via_admin,
    login,
    setup_admin,
)


def _make_ws(client, *, use_tailscale=False, use_gluetun=False, status="running"):
    """Create a workspace via the API, then force its runtime state in the DB."""
    image_id = add_image(name="Desktop", image_type="desktop")
    resp = client.post("/api/workspaces", json={"name": "diag", "image_id": image_id})
    assert resp.status_code == 201, resp.text
    ws_id = resp.json()["id"]
    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        ws.status = status
        ws.container_id = f"cove-ws-{ws_id}"
        ws.use_tailscale = use_tailscale
        ws.use_gluetun = use_gluetun
        db.commit()
    finally:
        db.close()
    return ws_id


# ── tailscale status ───────────────────────────────────────────────────────────

def test_tailscale_status_returns_output(client, fake_docker_manager):
    setup_admin(client)
    ws_id = _make_ws(client, use_tailscale=True)
    fake_docker_manager.tailscale_status.return_value = "100.64.0.1 host active"

    resp = client.get(f"/api/workspaces/{ws_id}/tailscale-status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    assert "100.64.0.1" in body["output"]
    fake_docker_manager.tailscale_status.assert_called_once_with(ws_id)


def test_tailscale_status_unavailable_when_sidecar_down(client, fake_docker_manager):
    setup_admin(client)
    ws_id = _make_ws(client, use_tailscale=True)
    fake_docker_manager.tailscale_status.return_value = None

    resp = client.get(f"/api/workspaces/{ws_id}/tailscale-status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is False
    assert body["output"] == ""


def test_tailscale_status_rejected_without_tailscale(client):
    setup_admin(client)
    ws_id = _make_ws(client, use_tailscale=False)

    resp = client.get(f"/api/workspaces/{ws_id}/tailscale-status")
    assert resp.status_code == 400, resp.text


# ── container logs ───────────────────────────────────────────────────────────--

def test_logs_desktop(client, fake_docker_manager):
    setup_admin(client)
    ws_id = _make_ws(client)
    fake_docker_manager.container_logs.return_value = "boot ok\nready"

    resp = client.get(f"/api/workspaces/{ws_id}/logs?source=desktop")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "desktop"
    assert body["available"] is True
    assert "ready" in body["output"]


def test_logs_default_source_is_desktop(client, fake_docker_manager):
    setup_admin(client)
    ws_id = _make_ws(client)
    fake_docker_manager.container_logs.return_value = "log"

    resp = client.get(f"/api/workspaces/{ws_id}/logs")
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "desktop"


def test_logs_gluetun_requires_vpn(client):
    setup_admin(client)
    ws_id = _make_ws(client, use_gluetun=False)

    resp = client.get(f"/api/workspaces/{ws_id}/logs?source=gluetun")
    assert resp.status_code == 400, resp.text


def test_logs_tailscale_requires_tailscale(client):
    setup_admin(client)
    ws_id = _make_ws(client, use_tailscale=False)

    resp = client.get(f"/api/workspaces/{ws_id}/logs?source=tailscale")
    assert resp.status_code == 400, resp.text


def test_logs_unknown_source_rejected(client):
    setup_admin(client)
    ws_id = _make_ws(client)

    resp = client.get(f"/api/workspaces/{ws_id}/logs?source=bogus")
    assert resp.status_code == 400, resp.text


def test_logs_unavailable_when_container_gone(client, fake_docker_manager):
    setup_admin(client)
    ws_id = _make_ws(client)
    fake_docker_manager.container_logs.return_value = None

    resp = client.get(f"/api/workspaces/{ws_id}/logs?source=desktop")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is False
    assert body["output"] == ""


# ── auth / ownership ───────────────────────────────────────────────────────────

def test_diagnostics_requires_auth(client):
    setup_admin(client)
    ws_id = _make_ws(client)
    # A bogus bearer token must not authorize.
    resp = client.get(
        f"/api/workspaces/{ws_id}/logs", headers={"Authorization": "Bearer nope"}
    )
    assert resp.status_code == 401, resp.text


def test_diagnostics_forbidden_for_other_user(client):
    admin_token, _ = setup_admin(client)
    ws_id = _make_ws(client)  # owned by admin
    create_user_via_admin(client, admin_token, "bob")
    bob_token = login(client, "bob", "password123").json()["access_token"]

    resp = client.get(
        f"/api/workspaces/{ws_id}/logs", headers=auth_header(bob_token)
    )
    assert resp.status_code == 403, resp.text
