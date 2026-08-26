"""API tests for the in-app documentation reader.

Docs are scoped: end-user pages are readable by anyone signed in, operator
pages (installation/security/zones/api-reference/…) are admin-only, and any
doc nobody has classified defaults to admin (fail closed).
"""

from server.tests.helpers import (
    auth_header,
    create_user_via_admin,
    login,
    setup_admin,
)

ADMIN_SLUGS = [
    "installation", "configuration", "deployment", "administration",
    "authentication", "security", "zones", "api-reference",
]
USER_SLUGS = ["README", "user-guide", "workspaces", "networking", "troubleshooting"]


def _admin_header(client):
    """A clean bearer token for the admin, regardless of cookie state."""
    client.cookies.clear()
    return auth_header(login(client, "admin", "password123").json()["access_token"])


def _bob(client):
    """Create a non-admin user and return a bearer-auth header for them."""
    admin = _admin_header(client)
    create_user_via_admin(client, admin["Authorization"].split()[1], "bob")
    client.cookies.clear()
    token = login(client, "bob", "password123").json()["access_token"]
    client.cookies.clear()
    return auth_header(token)


# ── Listing ──────────────────────────────────────────────────────────────────

def test_list_docs(client):
    setup_admin(client)
    r = client.get("/api/docs")
    assert r.status_code == 200, r.text
    entries = r.json()
    slugs = [d["slug"] for d in entries]
    assert slugs[0] == "README"  # README first
    assert "zones" in slugs
    assert all(d["title"] for d in entries)  # every entry has a title
    assert all(d["scope"] in ("user", "admin") for d in entries)


def test_list_docs_admin_sees_everything_with_real_scopes(client):
    setup_admin(client)
    entries = client.get("/api/docs").json()
    by_slug = {d["slug"]: d for d in entries}
    for slug in ADMIN_SLUGS:
        assert by_slug[slug]["scope"] == "admin", slug
    for slug in USER_SLUGS:
        assert by_slug[slug]["scope"] == "user", slug


def test_list_docs_non_admin_only_user_scoped(client):
    setup_admin(client)
    hdr = _bob(client)
    r = client.get("/api/docs", headers=hdr)
    assert r.status_code == 200, r.text
    entries = r.json()
    slugs = [d["slug"] for d in entries]
    assert set(slugs) == set(USER_SLUGS)
    assert all(d["scope"] == "user" for d in entries)
    for slug in ADMIN_SLUGS:
        assert slug not in slugs


def test_list_docs_preserves_order_for_both_roles(client):
    setup_admin(client)
    admin_slugs = [d["slug"] for d in client.get("/api/docs").json()]
    hdr = _bob(client)
    user_slugs = [d["slug"] for d in client.get("/api/docs", headers=hdr).json()]
    # README first, and the user view is the admin view with admin docs removed.
    assert admin_slugs[0] == "README"
    assert user_slugs == [s for s in admin_slugs if s in USER_SLUGS]


# ── Fetching a single doc ────────────────────────────────────────────────────

def test_get_doc(client):
    setup_admin(client)
    r = client.get("/api/docs/zones")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "zones"
    assert body["title"]
    assert body["scope"] == "admin"
    assert "#" in body["content"]  # markdown content


def test_get_doc_admin_can_read_admin_scoped(client):
    setup_admin(client)
    for slug in ADMIN_SLUGS:
        r = client.get(f"/api/docs/{slug}")
        assert r.status_code == 200, (slug, r.text)
        assert r.json()["scope"] == "admin"


def test_get_doc_non_admin_gets_user_scoped(client):
    setup_admin(client)
    hdr = _bob(client)
    for slug in USER_SLUGS:
        r = client.get(f"/api/docs/{slug}", headers=hdr)
        assert r.status_code == 200, (slug, r.text)
        body = r.json()
        assert body["scope"] == "user"
        assert body["content"]


def test_get_doc_non_admin_forbidden_on_admin_scoped(client):
    setup_admin(client)
    hdr = _bob(client)
    for slug in ADMIN_SLUGS:
        r = client.get(f"/api/docs/{slug}", headers=hdr)
        assert r.status_code == 403, (slug, r.text)


def test_get_doc_unknown_returns_404(client):
    setup_admin(client)
    assert client.get("/api/docs/nonexistent").status_code == 404


def test_get_doc_rejects_traversal(client):
    setup_admin(client)
    # Slug with path separators / dots can't escape the docs dir.
    assert client.get("/api/docs/..%2f..%2fREADME").status_code == 404
    assert client.get("/api/docs/../secrets").status_code == 404


def test_get_doc_traversal_rejected_for_non_admin_too(client):
    setup_admin(client)
    hdr = _bob(client)
    # Malformed slugs are rejected as 404 before any scope/filesystem work.
    for bad in ("..%2f..%2fREADME", "../secrets", "..", "-hidden.md"):
        assert client.get(f"/api/docs/{bad}", headers=hdr).status_code == 404, bad


def test_docs_require_auth(client):
    assert client.get("/api/docs").status_code == 401
    assert client.get("/api/docs/README").status_code == 401


# ── Unclassified docs fail closed ────────────────────────────────────────────

def test_unclassified_doc_defaults_to_admin_scope(client, tmp_path, monkeypatch):
    (tmp_path / "user-guide.md").write_text("# User Guide\n", encoding="utf-8")
    (tmp_path / "brand-new-page.md").write_text("# Brand New Page\n", encoding="utf-8")
    monkeypatch.setenv("COVE_DOCS_DIR", str(tmp_path))

    setup_admin(client)
    admin_entries = client.get("/api/docs").json()
    by_slug = {d["slug"]: d for d in admin_entries}
    assert by_slug["brand-new-page"]["scope"] == "admin"
    assert by_slug["user-guide"]["scope"] == "user"
    # Unknown slugs still sort after the known reading order.
    assert [d["slug"] for d in admin_entries] == ["user-guide", "brand-new-page"]

    hdr = _bob(client)
    user_slugs = [d["slug"] for d in client.get("/api/docs", headers=hdr).json()]
    assert user_slugs == ["user-guide"]
    assert client.get("/api/docs/brand-new-page", headers=hdr).status_code == 403
    admin = _admin_header(client)
    r = client.get("/api/docs/brand-new-page", headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == "admin"
