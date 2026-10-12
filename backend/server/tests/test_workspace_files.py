"""The stream page's file browser: /api/workspaces/{id}/fs and the container
helpers behind it."""

import io
import tarfile
from types import SimpleNamespace

import pytest

from server.docker_manager import DockerManager, _parse_listing, ws_abs_path, ws_safe_name
from server.tests.helpers import add_image, set_workspace_status, setup_admin


def test_paths_stay_in_the_home():
    assert ws_abs_path("") == "/config"
    assert ws_abs_path("/Desktop/") == "/config/Desktop"
    assert ws_abs_path("../../etc/passwd") == "/config/etc/passwd"
    assert ws_abs_path("a/../../..") == "/config"
    with pytest.raises(ValueError):
        ws_abs_path("a\x00b")


def test_safe_name_strips_folders_and_rejects_junk():
    assert ws_safe_name("C:\\Users\\me\\report.pdf") == "report.pdf"
    assert ws_safe_name("../../x.txt") == "x.txt"
    for bad in ("", ".", "..", "a/..", "x" * 300):
        with pytest.raises(ValueError):
            ws_safe_name(bad)


def test_parse_listing_sorts_folders_first_and_skips_junk():
    out = _parse_listing(
        "regular file|12|1760000000|notes.txt\n"
        "directory|4096|1760000000|Desktop\n"
        "symbolic link|7|1760000000|link\n"
        "regular empty file|0|1760000000|a|b.txt\n"
        "garbage line\n"
        "directory|x|y|bad\n"
    )
    assert [(e["name"], e["type"]) for e in out] == [
        ("Desktop", "dir"), ("a|b.txt", "file"), ("link", "link"), ("notes.txt", "file"),
    ]


class _Ctr:
    status = "running"

    def __init__(self):
        self.execs, self.archives = [], []
        self.files = {"/config/a.txt": b"hello world"}

    def exec_run(self, cmd, user=None):
        self.execs.append((cmd, user))
        if cmd[0] == "/bin/sh" and "printf %s" in cmd[2]:
            return 0, b"report (2).pdf"
        return 0, b""

    def put_archive(self, path, data):
        self.archives.append((path, data))
        return True

    def get_archive(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as t:
            info = tarfile.TarInfo(path.rsplit("/", 1)[-1])
            info.size = len(self.files[path])
            t.addfile(info, io.BytesIO(self.files[path]))
        raw = buf.getvalue()
        return iter([raw[:100], raw[100:]]), {"mode": 0o644}


def _dm(ctr):
    dm = DockerManager.__new__(DockerManager)
    dm._client = SimpleNamespace(containers=SimpleNamespace(get=lambda cid: ctr))
    return dm


def test_put_file_writes_owned_by_the_workspace_user_without_overwriting():
    ctr = _Ctr()
    ws = SimpleNamespace(id=1, container_id="c")
    name = _dm(ctr).ws_put_file(ws, "Desktop", "../report.pdf", io.BytesIO(b"PDF!"), 4)
    assert name == "report (2).pdf"
    cmd, user = ctr.execs[0]
    assert user == "1000:1000" and cmd[-2:] == ["/config/Desktop", "report.pdf"]
    folder, data = ctr.archives[0]
    assert folder == "/config/Desktop"
    member = tarfile.open(fileobj=io.BytesIO(data)).getmembers()[0]
    assert (member.name, member.size, member.uid, member.gid, member.mode) == ("report (2).pdf", 4, 1000, 1000, 0o644)


def test_open_file_streams_the_member():
    ctr = _Ctr()
    name, size, chunks = _dm(ctr).ws_open_file(SimpleNamespace(id=1, container_id="c"), "a.txt")
    assert (name, size, b"".join(chunks)) == ("a.txt", 11, b"hello world")


def test_open_file_refuses_folders():
    ctr = _Ctr()
    ctr.get_archive = lambda p: (iter([b""]), {"mode": (1 << 31) | 0o755})
    with pytest.raises(IsADirectoryError):
        _dm(ctr).ws_open_file(SimpleNamespace(id=1, container_id="c"), "Desktop")


def test_stopped_container_is_refused():
    ctr = _Ctr()
    ctr.status = "exited"
    with pytest.raises(FileNotFoundError):
        _dm(ctr).ws_list(SimpleNamespace(id=1, container_id="c"), "")


# ── API ─────────────────────────────────────────────────────────────────────

def _running(client):
    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post("/api/workspaces", json={"name": "box", "image_id": image_id}).json()
    set_workspace_status(ws["id"], "running")
    return ws


def test_list_upload_download_mkdir(client, fake_docker_manager):
    from datetime import datetime, timezone

    setup_admin(client)
    ws = _running(client)
    base = f"/api/workspaces/{ws['id']}/fs"
    fake_docker_manager.ws_list.return_value = [
        {"name": "Desktop", "type": "dir", "size": 4096, "modified": datetime(2026, 1, 1, tzinfo=timezone.utc)},
    ]
    r = client.get(base, params={"path": "../.."})
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "" and r.json()["entries"][0]["name"] == "Desktop"
    assert fake_docker_manager.ws_list.call_args.args[1] == ""

    fake_docker_manager.ws_put_file.return_value = "photo.jpg"
    r = client.post(f"{base}/upload", data={"path": "Desktop"}, files={"file": ("photo.jpg", b"\xff\xd8data")})
    assert r.status_code == 201, r.text
    assert r.json() == {"path": "Desktop", "name": "photo.jpg", "size": 6}
    args = fake_docker_manager.ws_put_file.call_args.args
    assert args[1:3] == ("Desktop", "photo.jpg") and args[4] == 6

    fake_docker_manager.ws_open_file.return_value = ("a b.txt", 3, iter([b"abc"]))
    r = client.get(f"{base}/download", params={"path": "a b.txt"})
    assert r.content == b"abc"
    assert "filename*=UTF-8''a%20b.txt" in r.headers["content-disposition"]

    assert client.post(f"{base}/mkdir", json={"path": "Desktop/new"}).status_code == 201
    assert client.post(f"{base}/mkdir", json={"path": ".."}).status_code == 400

    audit = client.get("/api/admin/audit", params={"action": "workspace.files.upload"}).json()
    rows = audit if isinstance(audit, list) else audit["items"]
    assert any("Desktop/photo.jpg" in (e.get("detail") or "") for e in rows)


def test_errors_map_to_http(client, fake_docker_manager):
    setup_admin(client)
    ws = _running(client)
    base = f"/api/workspaces/{ws['id']}/fs"
    fake_docker_manager.ws_list.side_effect = FileNotFoundError("folder not found")
    assert client.get(base, params={"path": "nope"}).status_code == 404
    fake_docker_manager.ws_open_file.side_effect = IsADirectoryError()
    assert client.get(f"{base}/download", params={"path": "Desktop"}).status_code == 400
    fake_docker_manager.ws_put_file.side_effect = PermissionError("can't write to /config/x")
    r = client.post(f"{base}/upload", data={"path": "x"}, files={"file": ("a", b"1")})
    assert r.status_code == 403 and "can't write" in r.json()["detail"]


def test_upload_size_limit(client, fake_docker_manager, monkeypatch):
    from server.config import get_settings

    setup_admin(client)
    ws = _running(client)
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    r = client.post(f"/api/workspaces/{ws['id']}/fs/upload", data={"path": ""}, files={"file": ("a", b"12")})
    assert r.status_code == 413
    fake_docker_manager.ws_put_file.assert_not_called()


def test_needs_a_running_workspace_you_own(client, fake_docker_manager):
    from server.tests.helpers import create_user_via_admin, login

    token, _ = setup_admin(client)
    ws = _running(client)
    set_workspace_status(ws["id"], "stopped")
    assert client.get(f"/api/workspaces/{ws['id']}/fs").status_code == 409
    set_workspace_status(ws["id"], "running")
    create_user_via_admin(client, token, "bob")
    client.cookies.clear()
    login(client, "bob", "password123")
    assert client.get(f"/api/workspaces/{ws['id']}/fs").status_code == 403
    r = client.post(f"/api/workspaces/{ws['id']}/fs/upload", data={"path": ""}, files={"file": ("a", b"1")})
    assert r.status_code == 403
    fake_docker_manager.ws_list.assert_not_called()
    fake_docker_manager.ws_put_file.assert_not_called()
