"""COVE_AGENT_MODE flips the app into a zone agent: control-plane routes off,
agent API on."""

from fastapi.testclient import TestClient

import server.main as main
from server.config import Settings


def _agent_app(monkeypatch):
    s = Settings(agent_mode=True)
    monkeypatch.setattr(main, "get_settings", lambda: s)
    return TestClient(main.create_app())


def test_agent_mode_exposes_agent_health(monkeypatch):
    c = _agent_app(monkeypatch)
    resp = c.get("/agent/health")
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "agent"
    assert c.get("/api/health").json()["role"] == "agent"


def test_agent_mode_disables_control_plane_routes(monkeypatch):
    c = _agent_app(monkeypatch)
    # Admin/zones/enroll/SPA are not mounted in agent mode.
    assert c.get("/api/admin/zones").status_code == 404
    assert c.get("/install.sh?token=x").status_code == 404
    # No SPA catch-all either.
    assert c.get("/some/spa/path").status_code == 404
