"""Subpath mode serves a workspace on the app origin: Cove's credentials must
never reach the workspace container, the stream only loads inside the SPA's
frame, and an admin opening someone else's workspace gets through only after
confirming (server-side, not just a warning in the SPA)."""

from server.config import get_settings
from server.db import SessionLocal
from server.docker_manager import DockerManager
from server.models import AuditLog, Workspace, WorkspaceImage
from server.tests.helpers import add_image, auth_header, create_user_via_admin, login, setup_admin


def _running(ws_id: int, container_id: str = "c" * 64) -> None:
    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        ws.status = "running"
        ws.container_id = container_id
        db.commit()
    finally:
        db.close()


def _labels(ws_id: int, image_id: int) -> dict:
    db = SessionLocal()
    try:
        return DockerManager._build_traefik_labels(
            db.get(Workspace, ws_id), db.get(WorkspaceImage, image_id), "cove-net"
        )
    finally:
        db.close()


def _forward(client, public_id: str, **headers):
    return client.get(
        "/api/auth/forward",
        headers={"X-Forwarded-Uri": f"/workspace/{public_id}/", **headers},
    )


def _bobs_workspace(client):
    """(admin token, bob's workspace) with the admin's session in the jar."""
    admin_token, _ = setup_admin(client, password="admin-pass-123")
    image_id = add_image()
    create_user_via_admin(client, admin_token, "bob")
    client.cookies.clear()
    bob = login(client, "bob", "password123").json()["access_token"]
    ws = client.post(
        "/api/workspaces", json={"name": "bobs", "image_id": image_id}, headers=auth_header(bob)
    ).json()
    _running(ws["id"])
    client.cookies.clear()
    assert login(client, "admin", "admin-pass-123").status_code == 200
    return ws


# ── credentials never reach the container ─────────────────────────────────────


def test_subpath_labels_strip_cove_credentials_before_the_container(client):
    setup_admin(client)
    image_id = add_image()
    ws = client.post("/api/workspaces", json={"name": "w", "image_id": image_id}).json()
    labels = _labels(ws["id"], image_id)
    hdr = f"traefik.http.middlewares.cove-ws-{ws['id']}-hdr.headers.customRequestHeaders"
    assert labels[f"{hdr}.Cookie"] == ""
    assert labels[f"{hdr}.Authorization"] == ""
    assert labels[f"{hdr}.X-Cove-Stream-Auth"] == ""
    # The strip runs after ForwardAuth (which needs the cookie) in the chain.
    chain = labels[f"traefik.http.routers.cove-ws-{ws['id']}.middlewares"].split(",")
    assert chain.index("cove-auth@docker") < chain.index(f"cove-ws-{ws['id']}-hdr")


def test_subdomain_labels_keep_the_workspace_origins_own_cookies(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "workspace_domain", "ws.example.com")
    setup_admin(client)
    image_id = add_image()
    ws = client.post("/api/workspaces", json={"name": "w", "image_id": image_id}).json()
    labels = _labels(ws["id"], image_id)
    assert not any("customRequestHeaders" in k for k in labels)


def test_remote_subpath_relay_strips_cookies_but_keeps_the_stream_token(client):
    from server.tests.helpers import enroll_zone

    setup_admin(client)
    zid, _ = enroll_zone(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post("/api/workspaces", json={"name": "ws", "image_id": image_id, "zone_id": zid}).json()
    _running(ws["id"])
    http = client.get("/api/internal/traefik-config").json()["http"]
    stripped = http["middlewares"][f"cove-ws-{ws['id']}-hdr"]["headers"]["customRequestHeaders"]
    assert stripped == {"Cookie": "", "Authorization": ""}


# ── the stream only loads inside the SPA's frame ──────────────────────────────


def test_top_level_navigation_to_a_stream_is_refused(client):
    setup_admin(client)
    image_id = add_image()
    ws = client.post("/api/workspaces", json={"name": "w", "image_id": image_id}).json()
    _running(ws["id"])
    assert _forward(client, ws["public_id"], **{"Sec-Fetch-Dest": "document"}).status_code == 403
    assert _forward(client, ws["public_id"], **{"Sec-Fetch-Dest": "iframe"}).status_code == 200
    # Subresources and the WebSocket (no Sec-Fetch-Dest from older clients too).
    assert _forward(client, ws["public_id"], **{"Sec-Fetch-Dest": "empty"}).status_code == 200
    assert _forward(client, ws["public_id"]).status_code == 200


# ── an admin opening another user's workspace must confirm ────────────────────


def test_admin_needs_a_recorded_consent_for_another_users_workspace(client):
    ws = _bobs_workspace(client)

    # A link alone gets nothing.
    assert _forward(client, ws["public_id"], **{"Sec-Fetch-Dest": "iframe"}).status_code == 401

    # Asking for the URL only says consent is required; it grants nothing.
    r = client.post(f"/api/workspaces/{ws['id']}/stream-auth")
    assert r.status_code == 200
    assert r.json() == {"url": f"/workspace/{ws['public_id']}/", "consent_required": True}
    assert _forward(client, ws["public_id"]).status_code == 401

    r = client.post(f"/api/workspaces/{ws['id']}/stream-auth?accept_foreign=true")
    assert r.status_code == 200 and r.json()["consent_required"] is False
    assert _forward(client, ws["public_id"]).status_code == 200

    db = SessionLocal()
    try:
        actions = [a.action for a in db.query(AuditLog).all()]
    finally:
        db.close()
    assert "stream.foreign_open" in actions


def test_consent_ends_with_the_container_it_was_given_for(client):
    ws = _bobs_workspace(client)
    client.post(f"/api/workspaces/{ws['id']}/stream-auth?accept_foreign=true")
    assert _forward(client, ws["public_id"]).status_code == 200

    _running(ws["id"], container_id="d" * 64)  # stopped and relaunched
    assert _forward(client, ws["public_id"]).status_code == 401


def test_owner_needs_no_consent(client):
    setup_admin(client)
    image_id = add_image()
    ws = client.post("/api/workspaces", json={"name": "w", "image_id": image_id}).json()
    _running(ws["id"])
    r = client.post(f"/api/workspaces/{ws['id']}/stream-auth")
    assert r.json()["consent_required"] is False
    assert _forward(client, ws["public_id"]).status_code == 200


def test_subdomain_mode_needs_no_consent(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "workspace_domain", "ws.example.com")
    ws = _bobs_workspace(client)
    r = client.post(f"/api/workspaces/{ws['id']}/stream-auth")
    assert r.status_code == 200
    assert r.json()["consent_required"] is False
    assert "__cove_t=" in r.json()["url"]
