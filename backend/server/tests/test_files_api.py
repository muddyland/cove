"""Tests for the files router (per-user file browser)."""

import io
import zipfile

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


def test_upload_over_limit_returns_413(client, monkeypatch):
    setup_admin(client)
    base = _user_base()
    # Force a tiny limit: 1 MiB. Patch the cached settings instance attribute.
    settings = get_settings()
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    # 2 MiB payload exceeds the 1 MiB cap.
    payload = b"x" * (2 * 1024 * 1024)
    resp = client.post(
        "/api/files/upload",
        data={"path": ""},
        files={"file": ("big.bin", io.BytesIO(payload), "application/octet-stream")},
    )
    assert resp.status_code == 413, resp.text
    # The partial file must have been removed.
    assert not (base / "big.bin").exists()


def test_upload_under_limit_ok(client, monkeypatch):
    setup_admin(client)
    _user_base()
    settings = get_settings()
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    payload = b"y" * (512 * 1024)  # 0.5 MiB
    resp = client.post(
        "/api/files/upload",
        data={"path": ""},
        files={"file": ("ok.bin", io.BytesIO(payload), "application/octet-stream")},
    )
    assert resp.status_code == 201, resp.text


# ── Copy / move ────────────────────────────────────────────────────────────────

def test_copy_into_dir(client):
    setup_admin(client)
    base = _user_base()
    (base / "a.txt").write_text("A")
    (base / "dir").mkdir(exist_ok=True)

    resp = client.post("/api/files/copy", json={"src": "a.txt", "dst_dir": "dir"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["path"] == "dir/a.txt"
    assert (base / "a.txt").exists()  # original stays
    assert (base / "dir" / "a.txt").read_text() == "A"


def test_copy_collision_suffixes(client):
    setup_admin(client)
    base = _user_base()
    (base / "a.txt").write_text("A")
    (base / "dir").mkdir(exist_ok=True)
    (base / "dir" / "a.txt").write_text("existing")

    resp = client.post("/api/files/copy", json={"src": "a.txt", "dst_dir": "dir"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "a (2).txt"


def test_move_into_dir(client):
    setup_admin(client)
    base = _user_base()
    (base / "m.txt").write_text("M")
    (base / "dir").mkdir(exist_ok=True)

    resp = client.post("/api/files/move", json={"src": "m.txt", "dst_dir": "dir"})
    assert resp.status_code == 200, resp.text
    assert not (base / "m.txt").exists()
    assert (base / "dir" / "m.txt").read_text() == "M"


def test_move_folder_into_itself_rejected(client):
    setup_admin(client)
    base = _user_base()
    (base / "dir").mkdir(exist_ok=True)
    resp = client.post("/api/files/move", json={"src": "dir", "dst_dir": "dir"})
    assert resp.status_code == 400


# ── Folder download (zip) + folder upload ──────────────────────────────────────

def test_download_archive_of_dir(client):
    setup_admin(client)
    base = _user_base()
    (base / "tree").mkdir(exist_ok=True)
    (base / "tree" / "one.txt").write_text("one")
    (base / "tree" / "sub").mkdir(exist_ok=True)
    (base / "tree" / "sub" / "two.txt").write_text("two")

    resp = client.get("/api/files/download-archive", params={"path": "tree"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/zip")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert zf.testzip() is None
    assert set(zf.namelist()) == {"tree/one.txt", "tree/sub/two.txt"}
    assert zf.read("tree/sub/two.txt") == b"two"


def test_folder_upload_creates_nested_dirs(client):
    # A folder upload is many single-file uploads whose `path` carries the subdir.
    setup_admin(client)
    base = _user_base()
    resp = client.post(
        "/api/files/upload",
        data={"path": "myfolder/sub"},
        files={"file": ("deep.txt", io.BytesIO(b"deep"), "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    assert (base / "myfolder" / "sub" / "deep.txt").read_text() == "deep"


# ── Trash (soft delete) ────────────────────────────────────────────────────────

def test_trash_hides_and_lists(client):
    setup_admin(client)
    base = _user_base()
    (base / "doc.txt").write_text("keep me")

    resp = client.post("/api/files/trash", json={"path": "doc.txt"})
    assert resp.status_code == 201, resp.text
    entry = resp.json()
    assert entry["name"] == "doc.txt"
    assert entry["original_path"] == "doc.txt"

    # Gone from the normal listing, and .trash itself is hidden.
    names = [e["name"] for e in client.get("/api/files").json()["entries"]]
    assert "doc.txt" not in names
    assert ".trash" not in names

    # Present in the trash listing.
    trash = client.get("/api/files/trash").json()
    assert [t["name"] for t in trash] == ["doc.txt"]


def test_double_trash_returns_404_not_500(client):
    # A duplicate request (e.g. an impatient double-click) must fail cleanly, not 500.
    setup_admin(client)
    base = _user_base()
    (base / "twice.txt").write_text("x")
    first = client.post("/api/files/trash", json={"path": "twice.txt"})
    assert first.status_code == 201, first.text
    second = client.post("/api/files/trash", json={"path": "twice.txt"})
    assert second.status_code == 404


def test_trash_restore_roundtrip(client):
    setup_admin(client)
    base = _user_base()
    (base / "r.txt").write_text("restore me")
    entry = client.post("/api/files/trash", json={"path": "r.txt"}).json()
    assert not (base / "r.txt").exists()

    resp = client.post(f"/api/files/trash/{entry['id']}/restore")
    assert resp.status_code == 200, resp.text
    assert (base / "r.txt").read_text() == "restore me"
    # The trash row is gone.
    assert client.get("/api/files/trash").json() == []


def test_trash_purge_removes_permanently(client):
    setup_admin(client)
    base = _user_base()
    (base / "p.txt").write_text("purge me")
    entry = client.post("/api/files/trash", json={"path": "p.txt"}).json()

    resp = client.delete(f"/api/files/trash/{entry['id']}")
    assert resp.status_code == 204, resp.text
    assert client.get("/api/files/trash").json() == []
    # Bytes are gone from the trash bucket on disk.
    assert not any((base / ".trash").rglob("p.txt")) if (base / ".trash").exists() else True


def test_expired_trash_is_purged_by_sweep(client, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from server.db import SessionLocal
    from server.main import _purge_expired_trash
    from server.models import TrashEntry

    setup_admin(client)
    base = _user_base()
    (base / "old.txt").write_text("stale")
    entry = client.post("/api/files/trash", json={"path": "old.txt"}).json()

    # Backdate the expiry so the sweep considers it due.
    db = SessionLocal()
    try:
        row = db.get(TrashEntry, entry["id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()
    finally:
        db.close()

    _purge_expired_trash()
    assert client.get("/api/files/trash").json() == []
