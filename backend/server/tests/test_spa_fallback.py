"""Tests for the SPA catch-all route (/{full_path:path}).

The catch-all exists to make client-side deep links work, but it must not
answer an unmatched API request with the SPA shell — see test_api_404 below.
Skipped entirely when the frontend hasn't been built, since the route is only
registered if server/static exists.
"""

import pytest

from server.main import STATIC_DIR

pytestmark = pytest.mark.skipif(
    not STATIC_DIR.exists(), reason="frontend not built; SPA catch-all is not registered"
)


def test_unmatched_api_path_404s_as_json(client):
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert "application/json" in r.headers["content-type"]
    assert r.json()["detail"] == "not found"


def test_unmatched_agent_path_404s_as_json(client):
    r = client.get("/agent/nope")
    assert r.status_code == 404
    assert "application/json" in r.headers["content-type"]


def test_encoded_slashes_in_api_path_404(client):
    """ASGI decodes %2f into real separators, so this never matches the
    /api/docs/{slug} route and lands here — it must 404, not return the shell."""
    r = client.get("/api/docs/..%2f..%2fREADME")
    assert r.status_code == 404
    assert "application/json" in r.headers["content-type"]


def test_api_prefix_is_matched_by_segment(client):
    """Only the 'api'/'agent' segments are special — a route like /apidocs or
    /agents is an ordinary client-side path and still gets the SPA."""
    r = client.get("/apidocs")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_client_side_deep_link_still_serves_shell(client):
    r = client.get("/workspaces/42")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_real_api_route_unaffected(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
