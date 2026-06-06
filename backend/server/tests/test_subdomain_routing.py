"""Tests for per-workspace subdomain routing (COVE_WORKSPACE_DOMAIN).

The ``get_settings`` function is ``lru_cache``d, so toggling env at runtime
requires clearing the cache. The ``subdomain_env`` fixture sets the env, clears
the cache, yields, then restores env and clears the cache again so other tests
keep the default (subpath) behavior.
"""

import os

import pytest

from server.config import get_settings
from server.db import SessionLocal
from server.models import Workspace
from server.schemas import WorkspaceOut
from server.security import create_stream_token
from server.tests.helpers import add_image, setup_admin

DOMAIN = "ws.example.com"


@pytest.fixture
def subdomain_env():
    """Enable subdomain mode for the duration of the test.

    Note: we deliberately do NOT set COVE_COOKIE_DOMAIN here — with a Domain
    attribute the TestClient (host ``testserver``) would reject the auth cookie
    and never send it back. The cookie-Domain behavior is covered separately by
    inspecting the raw Set-Cookie header rather than the cookie jar.
    """
    prev_domain = os.environ.get("COVE_WORKSPACE_DOMAIN")
    os.environ["COVE_WORKSPACE_DOMAIN"] = DOMAIN
    get_settings.cache_clear()
    try:
        yield
    finally:
        if prev_domain is None:
            os.environ.pop("COVE_WORKSPACE_DOMAIN", None)
        else:
            os.environ["COVE_WORKSPACE_DOMAIN"] = prev_domain
        get_settings.cache_clear()


def _create_workspace(client, image_id, name="ws1"):
    resp = client.post("/api/workspaces", json={"name": name, "image_id": image_id})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _mark_running(ws_id: int) -> str:
    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        ws.status = "running"
        db.commit()
        return ws.public_id
    finally:
        db.close()


# ── stream_url ────────────────────────────────────────────────────────────────

def test_stream_url_subpath_mode(client):
    """Default (no workspace_domain): subpath stream URL."""
    setup_admin(client)
    image_id = add_image()
    ws = _create_workspace(client, image_id)
    public_id = _mark_running(ws["id"])

    db = SessionLocal()
    try:
        out = WorkspaceOut.from_workspace(db.get(Workspace, ws["id"]))
    finally:
        db.close()
    assert out.stream_url == f"/workspace/{public_id}/"


def test_stream_url_subdomain_mode(client, subdomain_env):
    setup_admin(client)
    image_id = add_image()
    ws = _create_workspace(client, image_id)
    public_id = _mark_running(ws["id"])

    db = SessionLocal()
    try:
        out = WorkspaceOut.from_workspace(db.get(Workspace, ws["id"]))
    finally:
        db.close()
    assert out.stream_url == f"//{public_id}.{DOMAIN}/"


# ── ForwardAuth in subdomain mode ─────────────────────────────────────────────

def _stream_cookie_name():
    return get_settings().cookie_stream_name


def test_forward_auth_subdomain_stream_cookie_authorizes(client, subdomain_env):
    setup_admin(client)
    image_id = add_image()
    ws = _create_workspace(client, image_id)
    public_id = ws["public_id"]
    host = f"{public_id}.{DOMAIN}"
    token = create_stream_token(ws["user_id"], public_id)

    # Clear the session cookie to prove it plays NO part in stream auth.
    client.cookies.clear()
    client.cookies.set(_stream_cookie_name(), token)
    resp = client.get("/api/auth/forward", headers={"X-Forwarded-Host": host})
    assert resp.status_code == 200
    assert resp.headers.get("X-Cove-User") == "admin"


def test_forward_auth_subdomain_session_cookie_rejected(client, subdomain_env):
    """The session cookie must NOT authorize a workspace origin (isolation)."""
    setup_admin(client)  # session cookie in jar
    image_id = add_image()
    ws = _create_workspace(client, image_id)
    host = f"{ws['public_id']}.{DOMAIN}"
    resp = client.get("/api/auth/forward", headers={"X-Forwarded-Host": host})
    assert resp.status_code == 401


def test_forward_auth_subdomain_bootstrap_redirects_and_sets_cookie(client, subdomain_env):
    setup_admin(client)
    image_id = add_image()
    ws = _create_workspace(client, image_id)
    public_id = ws["public_id"]
    host = f"{public_id}.{DOMAIN}"
    token = create_stream_token(ws["user_id"], public_id)

    client.cookies.clear()
    resp = client.get(
        "/api/auth/forward",
        headers={
            "X-Forwarded-Host": host,
            "X-Forwarded-Uri": f"/?__cove_t={token}",
            "X-Forwarded-Proto": "https",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # Redirects to the clean URL with the one-time token stripped.
    assert resp.headers["location"] == f"https://{host}/"
    assert "__cove_t" not in resp.headers["location"]
    # Sets the host-only stream cookie (no Domain attribute).
    set_cookies = resp.headers.get_list("set-cookie")
    assert any(_stream_cookie_name() in c for c in set_cookies), set_cookies
    assert all("domain=" not in c.lower() for c in set_cookies), set_cookies


def test_forward_auth_subdomain_token_scoped_to_one_workspace(client, subdomain_env):
    setup_admin(client)
    image_id = add_image()
    ws_a = _create_workspace(client, image_id, name="a")
    ws_b = _create_workspace(client, image_id, name="b")
    host_b = f"{ws_b['public_id']}.{DOMAIN}"

    # A token minted for workspace A must not authorize workspace B's origin.
    token_a = create_stream_token(ws_a["user_id"], ws_a["public_id"])
    client.cookies.clear()
    client.cookies.set(_stream_cookie_name(), token_a)
    resp = client.get("/api/auth/forward", headers={"X-Forwarded-Host": host_b})
    assert resp.status_code == 401


def test_forward_auth_subdomain_unknown_host_401(client, subdomain_env):
    setup_admin(client)
    add_image()
    resp = client.get(
        "/api/auth/forward",
        headers={"X-Forwarded-Host": f"does-not-exist.{DOMAIN}"},
    )
    assert resp.status_code == 401


def test_forward_auth_subdomain_falls_back_to_subpath(client, subdomain_env):
    """Even in subdomain mode, a subpath X-Forwarded-Uri still resolves."""
    setup_admin(client)
    image_id = add_image()
    ws = _create_workspace(client, image_id)
    resp = client.get(
        "/api/auth/forward",
        headers={"X-Forwarded-Uri": f"/workspace/{ws['public_id']}/"},
    )
    assert resp.status_code == 200


# ── Cookie Domain attribute ───────────────────────────────────────────────────

# ── stream-auth endpoint ──────────────────────────────────────────────────────

def test_stream_auth_subpath_returns_plain_path(client):
    setup_admin(client)
    image_id = add_image()
    ws = _create_workspace(client, image_id)
    _mark_running(ws["id"])
    resp = client.post(f"/api/workspaces/{ws['id']}/stream-auth")
    assert resp.status_code == 200, resp.text
    assert resp.json()["url"] == f"/workspace/{ws['public_id']}/"


def test_stream_auth_subdomain_returns_token_url(client, subdomain_env):
    setup_admin(client)
    image_id = add_image()
    ws = _create_workspace(client, image_id)
    _mark_running(ws["id"])
    resp = client.post(f"/api/workspaces/{ws['id']}/stream-auth")
    assert resp.status_code == 200, resp.text
    assert resp.json()["url"].startswith(f"//{ws['public_id']}.{DOMAIN}/?__cove_t=")


def test_stream_auth_requires_running(client):
    setup_admin(client)
    image_id = add_image()
    ws = _create_workspace(client, image_id)  # status 'creating'
    resp = client.post(f"/api/workspaces/{ws['id']}/stream-auth")
    assert resp.status_code == 409


def test_stream_auth_denied_for_non_owner(client):
    admin_token, _ = setup_admin(client)
    image_id = add_image()
    ws = _create_workspace(client, image_id)
    _mark_running(ws["id"])

    from server.tests.helpers import create_user_via_admin
    create_user_via_admin(client, admin_token, "bob")
    client.cookies.clear()
    login = client.post("/api/auth/login", json={"username": "bob", "password": "password123"})
    assert login.status_code == 200

    resp = client.post(f"/api/workspaces/{ws['id']}/stream-auth")
    assert resp.status_code == 403


def test_session_cookies_are_always_host_only(client):
    """The session/refresh cookies must never carry a Domain attribute, so they
    are not sent to workspace origins. (Regression guard for the workspace
    session-cookie leak: there is deliberately no cookie-Domain setting.)"""
    resp = client.post(
        "/api/auth/setup", json={"username": "admin", "password": "password123"}
    )
    assert resp.status_code == 201, resp.text
    set_cookies = resp.headers.get_list("set-cookie")
    assert set_cookies
    assert not any("domain=" in c.lower() for c in set_cookies), set_cookies


# ── Traefik labels in both modes ──────────────────────────────────────────────

def test_traefik_labels_subdomain_mode(client, subdomain_env):
    from server.docker_manager import DockerManager
    from server.models import WorkspaceImage

    setup_admin(client)
    image_id = add_image()
    ws = _create_workspace(client, image_id)
    db = SessionLocal()
    try:
        wsrow = db.get(Workspace, ws["id"])
        img = db.get(WorkspaceImage, image_id)
        labels = DockerManager._build_traefik_labels(wsrow, img, "cove-net")
    finally:
        db.close()

    name = f"cove-ws-{ws['id']}"
    host = f"{ws['public_id']}.{DOMAIN}"
    assert labels[f"traefik.http.routers.{name}.rule"] == f"Host(`{host}`)"
    # Per-workspace headers middleware (frame-ancestors) instead of cove-headers.
    assert (
        labels[f"traefik.http.routers.{name}.middlewares"]
        == f"cove-errors@docker,cove-auth@docker,{name}-hdr"
    )
    # X-Frame-Options is stripped; CSP frame-ancestors allows the SPA to iframe it.
    assert labels[f"traefik.http.middlewares.{name}-hdr.headers.customResponseHeaders.X-Frame-Options"] == ""
    assert "frame-ancestors" in labels[f"traefik.http.middlewares.{name}-hdr.headers.contentSecurityPolicy"]
    # No stripprefix in subdomain mode.
    assert f"traefik.http.middlewares.{name}-strip.stripprefix.prefixes" not in labels
    assert labels["traefik.docker.network"] == "cove-net"


def test_traefik_labels_subpath_mode(client):
    from server.docker_manager import DockerManager
    from server.models import WorkspaceImage

    setup_admin(client)
    image_id = add_image()
    ws = _create_workspace(client, image_id)
    db = SessionLocal()
    try:
        wsrow = db.get(Workspace, ws["id"])
        img = db.get(WorkspaceImage, image_id)
        labels = DockerManager._build_traefik_labels(wsrow, img, "cove-net")
    finally:
        db.close()

    name = f"cove-ws-{ws['id']}"
    prefix = f"/workspace/{ws['public_id']}"
    assert labels[f"traefik.http.routers.{name}.rule"] == f"PathPrefix(`{prefix}/`)"
    assert labels[f"traefik.http.middlewares.{name}-strip.stripprefix.prefixes"] == prefix
