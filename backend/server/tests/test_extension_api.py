"""The browser extension Cove hands out (Preferences → Browser extension).

The archive is built from files vendored into the image, so what matters here is
that it's offered only to signed-in users, that the zip is well-formed and
reproducible, and that a build without the extension says so instead of failing.
"""

import io
import zipfile

import server.extension as ext
from server.tests.helpers import auth_header, setup_admin


def test_requires_auth(client):
    assert client.get("/api/extension").status_code == 401
    assert client.get("/api/extension/download").status_code == 401


def test_reports_what_it_ships(client):
    token, _ = setup_admin(client)
    body = client.get("/api/extension", headers=auth_header(token)).json()
    assert body["available"] is True
    assert body["version"]  # from the vendored manifest
    assert body["size_bytes"] > 0
    assert body["filename"].endswith(".zip")


def test_download_is_a_loadable_unpacked_extension(client):
    token, _ = setup_admin(client)
    resp = client.get("/api/extension/download", headers=auth_header(token))
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "attachment" in resp.headers["content-disposition"]

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert zf.testzip() is None
    names = zf.namelist()
    # One top-level folder to point "Load unpacked" at, with the manifest in it.
    assert all(n.startswith(f"{ext.ROOT_NAME}/") for n in names)
    assert f"{ext.ROOT_NAME}/manifest.json" in names
    assert any(n.endswith("src/background.js") for n in names)
    # Nothing from the extension's own repo tooling.
    assert not any("/test/" in n or n.endswith(".gitignore") for n in names)


def test_download_is_reproducible_and_revalidates(client):
    token, _ = setup_admin(client)
    first = client.get("/api/extension/download", headers=auth_header(token))
    second = client.get("/api/extension/download", headers=auth_header(token))
    assert first.content == second.content  # byte-identical: fixed timestamps
    etag = first.headers["etag"]

    cached = client.get(
        "/api/extension/download", headers={**auth_header(token), "If-None-Match": etag}
    )
    assert cached.status_code == 304


def test_a_build_without_the_extension_says_so(client, monkeypatch):
    token, _ = setup_admin(client)
    monkeypatch.setattr(ext, "extension_dir", lambda: None)
    monkeypatch.setattr(ext, "_cache", None)
    body = client.get("/api/extension", headers=auth_header(token)).json()
    assert body["available"] is False
    assert client.get("/api/extension/download", headers=auth_header(token)).status_code == 404
