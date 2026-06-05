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


@pytest.fixture
def cookie_domain_env():
    """Set COVE_COOKIE_DOMAIN so Set-Cookie carries a Domain attribute."""
    prev = os.environ.get("COVE_COOKIE_DOMAIN")
    os.environ["COVE_COOKIE_DOMAIN"] = DOMAIN
    get_settings.cache_clear()
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("COVE_COOKIE_DOMAIN", None)
        else:
            os.environ["COVE_COOKIE_DOMAIN"] = prev
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

def test_forward_auth_subdomain_authorizes_owner(client, subdomain_env):
    setup_admin(client)  # owner session cookie in jar
    image_id = add_image()
    ws = _create_workspace(client, image_id)
    public_id = ws["public_id"]
    host = f"{public_id}.{DOMAIN}"

    # 200 with the owner cookie and the workspace host.
    resp = client.get("/api/auth/forward", headers={"X-Forwarded-Host": host})
    assert resp.status_code == 200
    assert resp.headers.get("X-Cove-User") == "admin"

    # 401 without the session cookie.
    client.cookies.clear()
    resp = client.get("/api/auth/forward", headers={"X-Forwarded-Host": host})
    assert resp.status_code == 401


def test_forward_auth_subdomain_uses_host_header_fallback(client, subdomain_env):
    setup_admin(client)
    image_id = add_image()
    ws = _create_workspace(client, image_id)
    host = f"{ws['public_id']}.{DOMAIN}"

    # No X-Forwarded-Host; falls back to Host header.
    resp = client.get("/api/auth/forward", headers={"Host": host})
    assert resp.status_code == 200


def test_forward_auth_subdomain_strips_port(client, subdomain_env):
    setup_admin(client)
    image_id = add_image()
    ws = _create_workspace(client, image_id)
    host = f"{ws['public_id']}.{DOMAIN}:8443"

    resp = client.get("/api/auth/forward", headers={"X-Forwarded-Host": host})
    assert resp.status_code == 200


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

def test_cookies_include_domain_when_set(client, cookie_domain_env):
    # Fresh setup issues Set-Cookie with the configured Domain.
    resp = client.post(
        "/api/auth/setup", json={"username": "admin", "password": "password123"}
    )
    assert resp.status_code == 201, resp.text
    set_cookies = resp.headers.get_list("set-cookie")
    assert set_cookies
    assert any(f"domain={DOMAIN}" in c.lower() for c in set_cookies), set_cookies


def test_cookies_host_only_by_default(client):
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
    assert labels[f"traefik.http.routers.{name}.middlewares"] == "cove-auth@docker,cove-headers@docker"
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
