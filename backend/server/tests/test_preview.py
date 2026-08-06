"""Tests for workspace screen previews (server.preview + the preview endpoints).

The capture itself talks to a real Selkies stream over ``docker exec``, so what
is exercised here is everything around it: stripe assembly, the refusal to serve
a partial frame, the passive-only guarantee, and the API's ownership/lifecycle
behaviour.
"""

import io

import pytest
from PIL import Image

from server.db import SessionLocal
from server.models import Workspace
from server.preview import _MARKER, _decode_payload, assemble, capture
from server.tests.helpers import (
    add_image,
    auth_header,
    create_user_via_admin,
    login,
    set_workspace_status,
    setup_admin,
)


def _jpeg(width: int, height: int, colour=(30, 90, 140)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, "JPEG")
    return buf.getvalue()


def _stripes(width=320, height=180, rows=4):
    """A complete, gap-free set of stripes covering height."""
    step = height // rows
    return {y: _jpeg(width, step) for y in range(0, height, step)}


# --- assembly -------------------------------------------------------------


def test_assemble_composites_full_frame():
    out = assemble(_stripes())
    assert out
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG"
    # Downscaled to the thumbnail bound, aspect preserved.
    assert img.width <= 480 and img.height <= 480
    assert abs(img.width / img.height - 320 / 180) < 0.05


def test_assemble_rejects_a_frame_with_a_gap():
    """A missing stripe would render as a black band through the screenshot,
    which reads as a broken workspace rather than a missing preview."""
    stripes = _stripes(rows=4)
    del stripes[sorted(stripes)[1]]
    assert assemble(stripes) is None


def test_assemble_rejects_overlapping_stripes():
    stripes = {0: _jpeg(320, 45), 20: _jpeg(320, 45)}  # 20 != 0 + 45
    assert assemble(stripes) is None


def test_assemble_handles_no_stripes_and_junk():
    assert assemble({}) is None
    assert assemble({0: b"not a jpeg"}) is None


# --- exec payload decoding ------------------------------------------------


def test_decode_payload_ignores_surrounding_chatter():
    import base64 as b64
    import json

    payload = b64.b64encode(json.dumps({"0": b64.b64encode(b"xy").decode()}).encode()).decode()
    stdout = f"some native library chatter\n{_MARKER}{payload}\ntrailing noise\n".encode()
    assert _decode_payload(stdout) == {0: b"xy"}


def test_decode_payload_returns_none_without_marker():
    assert _decode_payload(b"just noise\n") is None
    assert _decode_payload(b"") is None


# --- capture orchestration ------------------------------------------------


class _FakeContainer:
    """Records exec invocations and replays canned results."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def exec_run(self, cmd, **kwargs):
        self.calls.append(cmd)
        return self._results.pop(0)


def test_capture_falls_through_interpreters_until_one_works():
    payload = _MARKER + __import__("base64").b64encode(
        __import__("json").dumps({}).encode()
    ).decode()
    container = _FakeContainer([(127, b"not found"), (0, payload.encode())])
    # Empty stripe set => no frame, but the second interpreter was reached.
    assert capture(container, 3000) is None
    assert len(container.calls) == 2
    assert container.calls[0][0] != container.calls[1][0]


def test_capture_passes_passive_only_flag_to_the_client():
    container = _FakeContainer([(0, b"")])
    capture(container, 3000, passive_only=True)
    # argv: [python, -c, src, url, passive_only, passive_wait, active_wait]
    assert container.calls[0][3] == "ws://localhost:3000/websockets"
    assert container.calls[0][4] == "1"

    container = _FakeContainer([(0, b"")])
    capture(container, 3000, passive_only=False)
    assert container.calls[0][4] == "0"


def test_capture_survives_exec_errors():
    class Boom:
        def exec_run(self, cmd, **kwargs):
            raise RuntimeError("daemon gone")

    assert capture(Boom(), 3000) is None


def test_capture_client_never_sends_settings_when_passive_only():
    """The passive-only contract is what keeps a refresh from evicting a live
    viewer, so assert the client source actually gates the SETTINGS send."""
    from server.preview import _CAPTURE_SRC

    assert "PASSIVE_ONLY" in _CAPTURE_SRC
    assert "if not stripes and not PASSIVE_ONLY:" in _CAPTURE_SRC
    settings_at = _CAPTURE_SRC.index("SETTINGS,")
    guard_at = _CAPTURE_SRC.index("if not stripes and not PASSIVE_ONLY:")
    assert guard_at < settings_at, "SETTINGS must be sent only inside the guard"


def test_capture_client_emits_the_marker_the_parser_expects():
    """The client and the parser must agree on the marker; they used to hardcode
    it separately, so a rename could break capture with tests still green."""
    from server.preview import _CAPTURE_SRC

    assert "__COVE_MARKER__" not in _CAPTURE_SRC, "placeholder was not substituted"
    assert _MARKER in _CAPTURE_SRC


# --- API ------------------------------------------------------------------


def _running_workspace_with_preview(client, frame=b"\xff\xd8\xffdata"):
    image_id = add_image(name="Desktop")
    resp = client.post("/api/workspaces", json={"name": "node", "image_id": image_id})
    ws_id = resp.json()["id"]
    set_workspace_status(ws_id, "running")
    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        ws.preview_jpg = frame
        from datetime import datetime, timezone

        ws.preview_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
    return ws_id


def test_preview_served_with_etag_and_private_caching(client):
    token, _ = setup_admin(client)
    ws_id = _running_workspace_with_preview(client)

    resp = client.get(f"/api/workspaces/{ws_id}/preview.jpg", headers=auth_header(token))
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content == b"\xff\xd8\xffdata"
    # A screenshot of someone's desktop must never sit in a shared cache.
    assert "private" in resp.headers["cache-control"]
    etag = resp.headers["etag"]

    again = client.get(
        f"/api/workspaces/{ws_id}/preview.jpg",
        headers={**auth_header(token), "If-None-Match": etag},
    )
    assert again.status_code == 304


def test_preview_404_when_absent(client):
    token, _ = setup_admin(client)
    image_id = add_image(name="Desktop")
    ws_id = client.post(
        "/api/workspaces", json={"name": "node", "image_id": image_id}
    ).json()["id"]

    resp = client.get(f"/api/workspaces/{ws_id}/preview.jpg", headers=auth_header(token))
    assert resp.status_code == 404


def test_preview_is_not_readable_by_another_user(client):
    admin_token, _ = setup_admin(client)
    ws_id = _running_workspace_with_preview(client)
    create_user_via_admin(client, admin_token, "someone")
    other = login(client, "someone", "password123").json()["access_token"]

    # Matches the router's convention for a workspace you don't own (403, not 404).
    resp = client.get(f"/api/workspaces/{ws_id}/preview.jpg", headers=auth_header(other))
    assert resp.status_code == 403


def test_preview_requires_auth(client):
    setup_admin(client)
    ws_id = _running_workspace_with_preview(client)
    # Setup leaves a session cookie on the test client, and the endpoint accepts
    # cookie auth — drop it so this actually exercises the unauthenticated path.
    client.cookies.clear()
    assert client.get(f"/api/workspaces/{ws_id}/preview.jpg").status_code == 401


def test_refresh_is_passive_only(client, fake_docker_manager):
    """A refresh must never be allowed to start a stream: doing so makes Cove the
    primary client and evicts whoever is watching."""
    token, _ = setup_admin(client)
    ws_id = _running_workspace_with_preview(client)
    fake_docker_manager.capture_preview.return_value = b"\xff\xd8\xffnew"

    resp = client.post(
        f"/api/workspaces/{ws_id}/preview/refresh", headers=auth_header(token)
    )
    assert resp.status_code == 200
    assert resp.json()["captured"] is True
    fake_docker_manager.capture_preview.assert_called_once_with(ws_id, passive_only=True)


def test_refresh_rejects_a_stopped_workspace(client):
    token, _ = setup_admin(client)
    ws_id = _running_workspace_with_preview(client)
    set_workspace_status(ws_id, "stopped")

    resp = client.post(
        f"/api/workspaces/{ws_id}/preview/refresh", headers=auth_header(token)
    )
    assert resp.status_code == 409


def test_halt_clears_the_stored_frame(client):
    """A stopped workspace must not keep showing what was last on its screen."""
    from unittest.mock import MagicMock

    from server.docker_manager import DockerManager

    setup_admin(client)
    ws_id = _running_workspace_with_preview(client, frame=b"\xff\xd8\xffsecret")

    mgr = DockerManager.__new__(DockerManager)
    mgr._client = MagicMock()
    mgr._get_db = SessionLocal
    mgr.zone_id = 0
    mgr._cleanup_docker_sidecar = lambda *a, **k: None
    mgr._cleanup_tailscale_sidecar = lambda *a, **k: None
    mgr._cleanup_gluetun_sidecar = lambda *a, **k: None
    mgr._cleanup_ws_network = lambda *a, **k: None
    mgr._ws_network_name = lambda *a, **k: "net"

    mgr.stop_workspace(ws_id)

    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        assert ws.status == "stopped"
        assert ws.preview_jpg is None
        assert ws.preview_at is None
    finally:
        db.close()


def test_workspace_out_exposes_preview_at_but_not_the_frame(client):
    """The grid payload must stay small and must not leak frames to list calls."""
    token, _ = setup_admin(client)
    ws_id = _running_workspace_with_preview(client)

    body = client.get("/api/workspaces", headers=auth_header(token)).json()
    row = next(w for w in body if w["id"] == ws_id)
    assert row["preview_at"] is not None
    assert "preview_jpg" not in row
