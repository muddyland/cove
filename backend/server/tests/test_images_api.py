"""Tests for the images router (create/list/sync, admin gating)."""

import server.routers.images as images_router
from server.tests.helpers import auth_header, create_user_via_admin, login, setup_admin

_FAKE_SPECS = [
    {
        "name": "Webtop — Alpine XFCE",
        "docker_image": "lscr.io/linuxserver/webtop:latest",
        "image_type": "desktop",
        "internal_port": 3000,
        "url_env": None,
        "description": "desktop",
    },
    {
        "name": "Chromium",
        "docker_image": "lscr.io/linuxserver/chromium:latest",
        "image_type": "browser",
        "internal_port": 3000,
        "url_env": "CHROME_CLI",
        "description": "browser",
    },
]


def test_admin_can_create_and_list_image(client):
    token, _ = setup_admin(client)
    resp = client.post(
        "/api/images",
        json={"name": "My Desktop", "docker_image": "lscr.io/linuxserver/webtop:latest"},
        headers=auth_header(token),
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["name"] == "My Desktop"

    listed = client.get("/api/images", headers=auth_header(token))
    assert listed.status_code == 200
    names = [i["name"] for i in listed.json()]
    assert "My Desktop" in names


def test_non_admin_cannot_create_image(client):
    admin_token, _ = setup_admin(client)
    create_user_via_admin(client, admin_token, "alice")
    client.cookies.clear()
    user_token = login(client, "alice", "password123").json()["access_token"]

    resp = client.post(
        "/api/images",
        json={"name": "Sneaky", "docker_image": "lscr.io/linuxserver/webtop:latest"},
        headers=auth_header(user_token),
    )
    assert resp.status_code == 403


def test_sync_adds_rows_and_is_idempotent(client, monkeypatch):
    token, _ = setup_admin(client)

    async def _fake_fetch_catalog():
        return list(_FAKE_SPECS)

    # The router imported fetch_catalog by name, so patch it on the router module.
    monkeypatch.setattr(images_router, "fetch_catalog", _fake_fetch_catalog)

    first = client.post("/api/images/sync", headers=auth_header(token))
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["added"] == len(_FAKE_SPECS)
    assert body["total"] == len(_FAKE_SPECS)

    # Second call is idempotent: nothing new added.
    second = client.post("/api/images/sync", headers=auth_header(token))
    assert second.status_code == 200
    assert second.json()["added"] == 0
    assert second.json()["total"] == len(_FAKE_SPECS)


def test_sync_requires_admin(client, monkeypatch):
    admin_token, _ = setup_admin(client)
    create_user_via_admin(client, admin_token, "carol")
    client.cookies.clear()
    user_token = login(client, "carol", "password123").json()["access_token"]

    async def _fake_fetch_catalog():
        return list(_FAKE_SPECS)

    monkeypatch.setattr(images_router, "fetch_catalog", _fake_fetch_catalog)
    resp = client.post("/api/images/sync", headers=auth_header(user_token))
    assert resp.status_code == 403
