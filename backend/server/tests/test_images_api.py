"""Tests for the images router (create/list/sync, admin gating)."""

import server.routers.images as images_router
from server.tests.helpers import auth_header, create_user_via_admin, login, setup_admin

# Raw LinuxServer API image shape (what fetch_linuxserver_images returns).
_FAKE_RAW = [
    {
        "name": "webtop",
        "deprecated": False,
        "description": "desktop",
        "project_logo": "https://logo.example/webtop.png",
        "tags": [{"tag": "latest", "desc": "Alpine XFCE"}],
    },
    {
        "name": "chromium",
        "deprecated": False,
        "description": "browser",
        "project_logo": "https://logo.example/chromium.png",
        "tags": [{"tag": "latest", "desc": "Chromium"}],
    },
]


def _patch_fetch(monkeypatch, raw):
    async def _fake():
        return list(raw)

    monkeypatch.setattr(images_router, "fetch_linuxserver_images", _fake)


def _stub_logo(monkeypatch, value=None):
    """Keep create_image hermetic — no real LinuxServer API call for the logo."""

    async def _fake_logo(_docker_image):
        return value

    monkeypatch.setattr(images_router, "fetch_logo", _fake_logo)


def test_admin_can_create_and_list_image(client, monkeypatch):
    _stub_logo(monkeypatch)
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
    _patch_fetch(monkeypatch, _FAKE_RAW)

    first = client.post("/api/images/sync", headers=auth_header(token))
    assert first.status_code == 200, first.text
    body = first.json()
    # webtop (1 tag) + chromium => 2 specs.
    assert body["added"] == 2
    assert body["total"] == 2

    # Second call is idempotent: nothing new added.
    second = client.post("/api/images/sync", headers=auth_header(token))
    assert second.status_code == 200
    assert second.json()["added"] == 0
    assert second.json()["total"] == 2


def test_sync_requires_admin(client, monkeypatch):
    admin_token, _ = setup_admin(client)
    create_user_via_admin(client, admin_token, "carol")
    client.cookies.clear()
    user_token = login(client, "carol", "password123").json()["access_token"]

    _patch_fetch(monkeypatch, _FAKE_RAW)
    resp = client.post("/api/images/sync", headers=auth_header(user_token))
    assert resp.status_code == 403


def test_sync_backfills_logo_on_existing_rows(client, monkeypatch):
    """Re-sync refreshes upstream metadata (logo_url) on curated rows that predate
    it, without clobbering admin edits."""
    from sqlalchemy import select as _select

    from server.db import SessionLocal
    from server.models import WorkspaceImage

    token, _ = setup_admin(client)

    # Pre-existing row with no logo and an admin-renamed display name.
    db = SessionLocal()
    try:
        db.add(
            WorkspaceImage(
                name="My Custom Name",
                docker_image="lscr.io/linuxserver/webtop:latest",
                image_type="desktop",
                internal_port=3000,
                logo_url=None,
            )
        )
        db.commit()
    finally:
        db.close()

    _patch_fetch(monkeypatch, _FAKE_RAW)
    resp = client.post("/api/images/sync", headers=auth_header(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] >= 1

    db = SessionLocal()
    try:
        row = db.scalar(
            _select(WorkspaceImage).where(
                WorkspaceImage.docker_image == "lscr.io/linuxserver/webtop:latest"
            )
        )
        assert row.logo_url == "https://logo.example/webtop.png"
        assert row.name == "My Custom Name"  # admin edit preserved
    finally:
        db.close()


def test_sync_backfills_logo_on_non_catalog_image(client, monkeypatch):
    """A manually-added lsio image outside the curated catalog (e.g. handbrake)
    still gets its project logo backfilled on sync."""
    from sqlalchemy import select as _select

    from server.db import SessionLocal
    from server.models import WorkspaceImage

    token, _ = setup_admin(client)

    db = SessionLocal()
    try:
        db.add(
            WorkspaceImage(
                name="Handbrake",
                docker_image="lscr.io/linuxserver/handbrake:latest",
                image_type="desktop",
                internal_port=3000,
                logo_url=None,
            )
        )
        db.commit()
    finally:
        db.close()

    raw = _FAKE_RAW + [
        {
            "name": "handbrake",
            "deprecated": False,
            "description": "video transcoder",
            "project_logo": "https://logo.example/handbrake.png",
            "tags": [{"tag": "latest", "desc": "Handbrake"}],
        }
    ]
    _patch_fetch(monkeypatch, raw)
    resp = client.post("/api/images/sync", headers=auth_header(token))
    assert resp.status_code == 200, resp.text

    db = SessionLocal()
    try:
        row = db.scalar(
            _select(WorkspaceImage).where(
                WorkspaceImage.docker_image == "lscr.io/linuxserver/handbrake:latest"
            )
        )
        assert row.logo_url == "https://logo.example/handbrake.png"
    finally:
        db.close()


def test_create_image_autofetches_logo(client, monkeypatch):
    """Creating an lsio image with no logo pulls the project logo automatically."""
    token, _ = setup_admin(client)
    _stub_logo(monkeypatch, "https://logo.example/handbrake.png")

    resp = client.post(
        "/api/images",
        json={"name": "Handbrake", "docker_image": "lscr.io/linuxserver/handbrake:latest"},
        headers=auth_header(token),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["logo_url"] == "https://logo.example/handbrake.png"
