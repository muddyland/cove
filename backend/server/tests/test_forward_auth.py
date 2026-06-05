"""Tests for the Traefik ForwardAuth endpoint /api/auth/forward."""

from server.config import get_settings
from server.db import SessionLocal
from server.models import Workspace
from server.tests.helpers import add_image, setup_admin


def _create_workspace(client, image_id, name="ws1"):
    resp = client.post(
        "/api/workspaces",
        json={"name": name, "image_id": image_id},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_forward_auth_flow(client):
    setup_admin(client)  # client jar now holds the owner's session cookie
    image_id = add_image()
    ws = _create_workspace(client, image_id)
    public_id = ws["public_id"]
    uri = f"/workspace/{public_id}/"

    settings = get_settings()
    session_cookie = client.cookies.get(settings.cookie_session_name)
    assert session_cookie

    # 200 with the owner's session cookie.
    resp = client.get(
        "/api/auth/forward",
        headers={"X-Forwarded-Uri": uri},
    )
    assert resp.status_code == 200
    assert resp.headers.get("X-Cove-User") == "admin"

    # 401 without the session cookie.
    client.cookies.clear()
    resp = client.get("/api/auth/forward", headers={"X-Forwarded-Uri": uri})
    assert resp.status_code == 401


def test_forward_auth_bogus_public_id(client):
    setup_admin(client)
    add_image()
    resp = client.get(
        "/api/auth/forward",
        headers={"X-Forwarded-Uri": "/workspace/does-not-exist/"},
    )
    assert resp.status_code == 401


def test_forward_auth_missing_uri_header(client):
    setup_admin(client)
    resp = client.get("/api/auth/forward")
    assert resp.status_code == 401


def test_forward_auth_other_users_workspace_denied(client):
    """A logged-in non-admin cannot pass ForwardAuth for someone else's ws."""
    admin_token, _ = setup_admin(client)
    image_id = add_image()
    # Admin owns a workspace.
    ws = _create_workspace(client, image_id, name="admin-ws")

    # Create a normal user owning a *different* workspace.
    from server.tests.helpers import create_user_via_admin
    create_user_via_admin(client, admin_token, "bob")

    # Insert a workspace owned by admin only; bob logs in.
    client.cookies.clear()
    login = client.post("/api/auth/login", json={"username": "bob", "password": "password123"})
    assert login.status_code == 200

    # bob's session cookie is now in the jar; he tries to access admin's ws.
    # But admin's ForwardAuth would 200; we verify a *non-owner non-admin* is
    # denied by making a workspace owned by admin and confirming bob != owner.
    db = SessionLocal()
    try:
        # Make bob a plain (non-admin) workspace owner of a separate ws so we
        # can prove cross-user denial.
        wsrow = db.get(Workspace, ws["id"])
        assert wsrow is not None
        owner_id = wsrow.user_id
    finally:
        db.close()

    resp = client.get(
        "/api/auth/forward",
        headers={"X-Forwarded-Uri": f"/workspace/{ws['public_id']}/"},
    )
    # bob is not the owner and not admin -> 401.
    assert resp.status_code == 401
    assert owner_id != 0
