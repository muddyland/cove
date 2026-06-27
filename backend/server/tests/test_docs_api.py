"""API tests for the in-app documentation reader."""

from server.tests.helpers import setup_admin


def test_list_docs(client):
    setup_admin(client)
    r = client.get("/api/docs")
    assert r.status_code == 200, r.text
    entries = r.json()
    slugs = [d["slug"] for d in entries]
    assert slugs[0] == "README"  # README first
    assert "zones" in slugs
    assert all(d["title"] for d in entries)  # every entry has a title


def test_get_doc(client):
    setup_admin(client)
    r = client.get("/api/docs/zones")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "zones"
    assert body["title"]
    assert "#" in body["content"]  # markdown content


def test_get_doc_unknown_returns_404(client):
    setup_admin(client)
    assert client.get("/api/docs/nonexistent").status_code == 404


def test_get_doc_rejects_traversal(client):
    setup_admin(client)
    # Slug with path separators / dots can't escape the docs dir.
    assert client.get("/api/docs/..%2f..%2fREADME").status_code == 404


def test_docs_require_auth(client):
    assert client.get("/api/docs").status_code == 401
