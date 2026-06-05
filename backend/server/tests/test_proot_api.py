"""Tests for the proot-apps catalog endpoint."""

import server.proot as proot_module
from server.tests.helpers import setup_admin


def test_proot_apps_lists_catalog(client, monkeypatch):
    # Seed the module cache so no network call is made.
    monkeypatch.setattr(proot_module, "_cache", ["blender", "firefox"])
    setup_admin(client)
    resp = client.get("/api/proot-apps")
    assert resp.status_code == 200
    assert resp.json() == {"apps": ["blender", "firefox"]}


def test_proot_apps_requires_auth(client):
    resp = client.get("/api/proot-apps")
    assert resp.status_code == 401
