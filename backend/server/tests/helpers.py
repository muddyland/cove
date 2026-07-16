"""Shared helpers for the API test modules."""

from server.db import SessionLocal
from server.models import WorkspaceImage


def setup_admin(client, username="admin", password="password123"):
    """Run first-run setup and return (token, response)."""
    resp = client.post(
        "/api/auth/setup", json={"username": username, "password": password}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"], resp


def login(client, username, password):
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def create_user_via_admin(client, admin_token, username, password="password123", is_admin=False):
    resp = client.post(
        "/api/admin/users",
        json={"username": username, "password": password, "is_admin": is_admin},
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def set_workspace_status(ws_id, status):
    """Force a workspace's status directly in the DB.

    The tests' fake DockerManager is a no-op MagicMock, so it never transitions a
    workspace out of "creating"/"stopping". Tests that need a resting workspace
    (e.g. to edit or migrate it) set the precondition here."""
    from server.models import Workspace

    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        ws.status = status
        db.commit()
    finally:
        db.close()


def add_image(name="Ubuntu Desktop", docker_image="lscr.io/linuxserver/webtop:latest",
              image_type="desktop", url_env=None, internal_port=3000, logo_url=None,
              icon_png=None):
    """Insert a WorkspaceImage directly into the DB and return its id.

    Used because the create-image API path currently rejects the "browser"
    image_type, so direct insertion is the reliable way to seed a browser image.
    """
    db = SessionLocal()
    try:
        img = WorkspaceImage(
            name=name,
            docker_image=docker_image,
            image_type=image_type,
            url_env=url_env,
            internal_port=internal_port,
            logo_url=logo_url,
            icon_png=icon_png,
            enabled=True,
        )
        db.add(img)
        db.commit()
        db.refresh(img)
        return img.id
    finally:
        db.close()
