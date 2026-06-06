"""Tests for the custom workspace-stream error page (/__cove_error/{status})."""


def test_stream_error_page_renders(client):
    r = client.get("/__cove_error/502")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Workspace unavailable" in r.text
    assert "Stream 502" in r.text


def test_stream_error_page_sanitizes_status(client):
    # A non-numeric status is never reflected verbatim (guards against reflected
    # XSS via the {status} path segment) — it renders as a generic "Error".
    r = client.get("/__cove_error/notanumber")
    assert r.status_code == 200
    assert "notanumber" not in r.text
    assert "Stream Error" in r.text
