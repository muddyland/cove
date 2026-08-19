"""`--rm` for workspaces: auto_remove deletes the record once it stops.

Covers the API gate (auto_remove is only allowed on an ephemeral workspace) and
the teardown behaviour in DockerManager.stop_workspace, which is where the row
actually goes away.
"""

from unittest.mock import MagicMock

import pytest

from server.db import SessionLocal
from server.models import Workspace
from server.tests.helpers import add_image, set_workspace_status, setup_admin


def _launch(client, **overrides):
    image_id = add_image(name="Chromium", image_type="browser", url_env="CHROME_CLI")
    body = {"name": "linky", "image_id": image_id, "target_url": "https://example.com"}
    body.update(overrides)
    return client.post("/api/workspaces", json=body)


def test_auto_remove_requires_ephemeral(client):
    setup_admin(client)
    resp = _launch(client, auto_remove=True, ephemeral=False)
    assert resp.status_code == 400, resp.text
    # A persistent home would be destroyed on an ordinary Halt — that is Purge's
    # job, and Purge asks first.
    assert "ephemeral" in resp.json()["detail"].lower()


def test_auto_remove_with_ephemeral_is_accepted_and_stored(client):
    setup_admin(client)
    resp = _launch(client, auto_remove=True, ephemeral=True)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["auto_remove"] is True
    assert body["ephemeral"] is True


def test_auto_remove_defaults_off(client):
    setup_admin(client)
    resp = _launch(client)
    assert resp.status_code == 201, resp.text
    # The default must stay off: turning it on for every workspace would delete
    # records nobody asked to lose.
    assert resp.json()["auto_remove"] is False


def test_edit_cannot_strand_auto_remove_on_a_persistent_workspace(client):
    """Clearing ephemeral while auto_remove is set must be refused.

    Validating only the incoming field would let a two-step edit produce exactly
    the combination create rejects.
    """
    setup_admin(client)
    ws_id = _launch(client, auto_remove=True, ephemeral=True).json()["id"]
    set_workspace_status(ws_id, "stopped")

    resp = client.patch(f"/api/workspaces/{ws_id}", json={"ephemeral": False})
    assert resp.status_code == 400, resp.text

    # Dropping both together is fine.
    resp = client.patch(
        f"/api/workspaces/{ws_id}", json={"ephemeral": False, "auto_remove": False}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["auto_remove"] is False


# ── Teardown behaviour ────────────────────────────────────────────────────


def _stop(ws_id):
    """Run the real stop_workspace against a stubbed Docker client."""
    from server.docker_manager import DockerManager

    mgr = DockerManager.__new__(DockerManager)
    mgr.zone_id = 0
    mgr._client = MagicMock()
    mgr._get_db = SessionLocal
    for name in (
        "_cleanup_docker_sidecar",
        "_cleanup_tailscale_sidecar",
        "_cleanup_gluetun_sidecar",
        "_cleanup_ws_network",
    ):
        setattr(mgr, name, MagicMock())
    mgr.stop_workspace(ws_id)


def test_stopping_an_auto_remove_workspace_deletes_the_record(client):
    setup_admin(client)
    ws_id = _launch(client, auto_remove=True, ephemeral=True).json()["id"]
    set_workspace_status(ws_id, "running")

    _stop(ws_id)

    with SessionLocal() as db:
        assert db.get(Workspace, ws_id) is None, "the row should be gone, not merely stopped"
    assert client.get(f"/api/workspaces/{ws_id}").status_code == 404


def test_stopping_an_ordinary_workspace_still_leaves_it_stopped(client):
    setup_admin(client)
    ws_id = _launch(client, ephemeral=True).json()["id"]
    set_workspace_status(ws_id, "running")

    _stop(ws_id)

    with SessionLocal() as db:
        ws = db.get(Workspace, ws_id)
        assert ws is not None
        assert ws.status == "stopped"
