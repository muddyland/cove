"""Tests for the files router (per-user file browser)."""

import io

from server.config import get_settings
from server.tests.helpers import setup_admin


def _user_base(username="admin"):
    settings = get_settings()
    root = settings.storage_path or (settings.data_dir / "workspaces")
    base = (root / username).resolve()
    base.mkdir(parents=True, exist_ok=True)
    return base


def test_list_shows_created_file(client):
    setup_admin(client)
    base = _user_base()
    (base / "hello.txt").write_text("hi there")
    (base / "sub").mkdir(exist_ok=True)

    resp = client.get("/api/files")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = [e["name"] for e in body["entries"]]
    assert "hello.txt" in names
    assert "sub" in names
    # Dirs sort first.
    assert body["entries"][0]["type"] == "dir"
    file_entry = next(e for e in body["entries"] if e["name"] == "hello.txt")
    assert file_entry["type"] == "file"
    assert file_entry["size"] == len("hi there")


def test_download_returns_content(client):
    setup_admin(client)
    base = _user_base()
    (base / "data.txt").write_text("payload-123")

    resp = client.get("/api/files/download", params={"path": "data.txt"})
    assert resp.status_code == 200, resp.text
    assert resp.content == b"payload-123"
    assert "attachment" in resp.headers["content-disposition"]


def test_download_directory_rejected(client):
    setup_admin(client)
    base = _user_base()
    (base / "adir").mkdir(exist_ok=True)
    resp = client.get("/api/files/download", params={"path": "adir"})
    assert resp.status_code == 400


def test_path_traversal_rejected(client):
    setup_admin(client)
    _user_base()
    resp = client.get("/api/files", params={"path": "../../etc/passwd"})
    assert resp.status_code == 400
    resp2 = client.get("/api/files/download", params={"path": "../../../etc/passwd"})
    assert resp2.status_code == 400


def test_list_missing_path_404(client):
    setup_admin(client)
    _user_base()
    resp = client.get("/api/files", params={"path": "nope"})
    assert resp.status_code == 404


def test_upload_then_list(client):
    setup_admin(client)
    _user_base()
    resp = client.post(
        "/api/files/upload",
        data={"path": ""},
        files={"file": ("up.txt", io.BytesIO(b"uploaded"), "text/plain")},
    )
    assert resp.status_code == 201, resp.text

    listed = client.get("/api/files")
    names = [e["name"] for e in listed.json()["entries"]]
    assert "up.txt" in names


def test_upload_traversal_rejected(client):
    setup_admin(client)
    _user_base()
    resp = client.post(
        "/api/files/upload",
        data={"path": "../../tmp"},
        files={"file": ("evil.txt", io.BytesIO(b"x"), "text/plain")},
    )
    assert resp.status_code == 400


def test_delete_then_404(client):
    setup_admin(client)
    base = _user_base()
    (base / "gone.txt").write_text("bye")

    resp = client.delete("/api/files", params={"path": "gone.txt"})
    assert resp.status_code == 204

    got = client.get("/api/files/download", params={"path": "gone.txt"})
    assert got.status_code == 404


def test_delete_root_rejected(client):
    setup_admin(client)
    _user_base()
    resp = client.delete("/api/files", params={"path": ""})
    assert resp.status_code == 400
