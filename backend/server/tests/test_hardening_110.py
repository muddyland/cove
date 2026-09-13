"""1.1.0 hardening: user-to-root inputs, per-user Docker grant, revocation
timing, orphan cleanup, atomic lifecycle transitions, reserved usernames,
symlink-safe storage, and the egress guard failing closed."""

import io
import json
import os
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import docker
import pytest

from server import storage_local
from server.config import get_settings
from server.db import SessionLocal
from server.docker_manager import DockerManager, EgressGuardError, copy_workspace_storage
from server.models import User, Workspace
from server.security import is_valid_username
from server.tests.helpers import (
    add_image,
    auth_header,
    create_user_via_admin,
    login,
    set_workspace_status,
    setup_admin,
)

# ── Tailscale flag injection ───────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["x --ssh", "x --advertise-exit-node", "-ssh", "a b", "x\t--reset", "x;y"])
def test_exit_node_rejects_flag_injection(client, bad):
    setup_admin(client)
    image_id = add_image()
    r = client.post("/api/workspaces", json={"name": "ws", "image_id": image_id, "ts_exit_node": bad})
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("good", ["100.101.102.103", "my-exit-node", "exit.tail1234.ts.net", "fd7a:115c::1"])
def test_exit_node_accepts_hosts(client, good):
    setup_admin(client)
    image_id = add_image()
    r = client.post("/api/workspaces", json={"name": "ws", "image_id": image_id, "ts_exit_node": good})
    assert r.status_code == 201, r.text
    assert r.json()["ts_exit_node"] == good


def test_exit_node_update_is_validated(client):
    setup_admin(client)
    image_id = add_image()
    ws = client.post("/api/workspaces", json={"name": "ws", "image_id": image_id}).json()
    set_workspace_status(ws["id"], "stopped")
    r = client.patch(f"/api/workspaces/{ws['id']}", json={"ts_exit_node": "x --ssh"})
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("bad", ["https://hs.example.com --ssh", "https://hs.example.com\t--reset"])
def test_login_server_rejects_flag_injection(client, bad):
    setup_admin(client)
    r = client.put("/api/users/me/tailscale", json={"login_server": bad})
    assert r.status_code == 400, r.text


def test_login_server_accepts_plain_url(client):
    setup_admin(client)
    r = client.put("/api/users/me/tailscale", json={"login_server": "https://hs.example.com:8443/path"})
    assert r.status_code == 200, r.text


# ── custom DNS: public resolvers only ──────────────────────────────────────

@pytest.mark.parametrize("bad", ["192.168.1.1", "10.0.0.53", "172.17.0.1", "127.0.0.1", "169.254.169.254"])
def test_private_dns_servers_rejected(client, bad):
    setup_admin(client)
    image_id = add_image()
    r = client.post(
        "/api/workspaces",
        json={"name": "ws", "image_id": image_id, "custom_dns": True, "dns_servers": bad},
    )
    assert r.status_code == 400, r.text


def test_public_dns_servers_accepted(client):
    setup_admin(client)
    image_id = add_image()
    r = client.post(
        "/api/workspaces",
        json={"name": "ws", "image_id": image_id, "custom_dns": True, "dns_servers": "1.1.1.1, 9.9.9.9"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["dns_servers"] == "1.1.1.1 9.9.9.9"


# ── Docker-in-Docker: per-user grant ───────────────────────────────────────

def _enable_docker(client):
    r = client.put("/api/admin/settings", json={"workspace_docker": True})
    assert r.status_code == 200, r.text


def test_dind_requires_per_user_grant(client):
    admin_tok, _ = setup_admin(client)
    _enable_docker(client)
    create_user_via_admin(client, admin_tok, "bob")
    bob = login(client, "bob", "password123").json()["access_token"]
    image_id = add_image()
    r = client.post(
        "/api/workspaces", json={"name": "ws", "image_id": image_id, "use_docker": True},
        headers=auth_header(bob),
    )
    assert r.status_code == 403, r.text
    # Admin grants it.
    users = client.get("/api/admin/users", headers=auth_header(admin_tok)).json()
    bob_id = next(u["id"] for u in users if u["username"] == "bob")
    r = client.patch(f"/api/admin/users/{bob_id}", json={"docker_allowed": True}, headers=auth_header(admin_tok))
    assert r.status_code == 200 and r.json()["docker_allowed"] is True
    r = client.post(
        "/api/workspaces", json={"name": "ws", "image_id": image_id, "use_docker": True},
        headers=auth_header(bob),
    )
    assert r.status_code == 201, r.text


def test_dind_admin_is_always_allowed(client):
    setup_admin(client)
    _enable_docker(client)
    image_id = add_image()
    r = client.post("/api/workspaces", json={"name": "ws", "image_id": image_id, "use_docker": True})
    assert r.status_code == 201, r.text


def test_dind_grant_settable_on_create(client):
    admin_tok, _ = setup_admin(client)
    r = client.post(
        "/api/admin/users",
        json={"username": "carol", "password": "password123", "docker_allowed": True},
        headers=auth_header(admin_tok),
    )
    assert r.status_code == 201 and r.json()["docker_allowed"] is True


# ── revocation: a token minted right after change-password must work ───────

def test_change_password_token_usable_immediately(client):
    setup_admin(client)
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "password123", "new_password": "newpassword123"},
    )
    assert r.status_code == 200, r.text
    tok = r.json()["access_token"]
    assert client.get("/api/auth/me", headers=auth_header(tok)).status_code == 200
    assert client.get("/api/auth/me").status_code == 200  # cookie path
    assert client.post("/api/auth/refresh").status_code == 200


def test_logout_then_login_same_second_works(client):
    setup_admin(client)
    assert client.post("/api/auth/logout").status_code == 200
    r = login(client, "admin", "password123")
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=auth_header(r.json()["access_token"])).status_code == 200


# ── passwords: bcrypt's 72-byte ceiling is a 400, not a 500 ────────────────

def test_overlong_password_is_400(client):
    r = client.post("/api/auth/setup", json={"username": "admin", "password": "x" * 100})
    assert r.status_code == 400, r.text


# ── reserved usernames ─────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [".cove-scripts", ".cove-ssh", ".trash", "_profile", "cove-agent", "_x"])
def test_reserved_usernames_rejected(client, name):
    assert not is_valid_username(name)
    r = client.post("/api/auth/setup", json={"username": name, "password": "password123"})
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("raw", [".cove-scripts", ".cove-ssh", "_profile", "cove-agent", "..", "___", "cove-"])
def test_oidc_username_sanitizer_avoids_reserved(raw):
    from server.oidc import sanitize_username

    out = sanitize_username(raw)
    assert is_valid_username(out), (raw, out)


def test_oidc_username_sanitizer_keeps_plain_names():
    from server.oidc import sanitize_username

    assert sanitize_username("alice") == "alice"
    assert sanitize_username("alice.smith") == "alice.smith"


def test_oidc_admin_group_string_claim_is_not_substring_match():
    from server.oidc import is_admin_from_claims

    get_settings.cache_clear()
    os.environ["COVE_OIDC_ADMIN_GROUP"] = "admin"
    get_settings.cache_clear()
    try:
        assert is_admin_from_claims({"groups": ["admin"]}) is True
        assert is_admin_from_claims({"groups": "not-admins"}) is False
        assert is_admin_from_claims({"groups": "admin"}) is False
    finally:
        os.environ.pop("COVE_OIDC_ADMIN_GROUP", None)
        get_settings.cache_clear()


# ── file browser: archives never follow symlinks ───────────────────────────

def test_archive_skips_symlinks(tmp_path):
    base = tmp_path / "alice"
    (base / "proj").mkdir(parents=True)
    (base / "proj" / "real.txt").write_text("hello")
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET")
    (base / "proj" / "link.txt").symlink_to(secret)
    (base / "proj" / "linkdir").symlink_to(tmp_path)
    data = b"".join(storage_local.iter_zip(base.resolve(), "proj"))
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "proj/real.txt" in names
    assert not any("link" in n or "secret" in n for n in names), names
    assert b"SECRET" not in data


def test_dir_size_skips_symlinks(tmp_path):
    base = tmp_path / "alice"
    base.mkdir()
    (base / "a.txt").write_text("12345")
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 10000)
    (base / "link").symlink_to(big)
    assert storage_local._dir_size(base) == 5


def test_nul_byte_path_is_400(tmp_path):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        storage_local.resolve(tmp_path.resolve(), "a\x00b")
    assert ei.value.status_code == 400


# ── clone never merges into a pre-existing destination ────────────────────

def test_clone_refuses_existing_destination(tmp_path, monkeypatch):
    monkeypatch.setenv("COVE_STORAGE_PATH", str(tmp_path))
    get_settings.cache_clear()
    try:
        from types import SimpleNamespace as NS

        user = NS(username="alice")
        src = NS(volume_name=None, user=user, name="src", shared_profile=False)
        dst = NS(volume_name=None, user=user, name="dst", shared_profile=False)
        (tmp_path / "alice" / "workspace-src").mkdir(parents=True)
        (tmp_path / "alice" / "workspace-src" / "f").write_text("x")
        (tmp_path / "alice" / "workspace-dst").mkdir(parents=True)
        (tmp_path / "alice" / "workspace-dst" / "s").symlink_to(tmp_path)
        with pytest.raises(ValueError, match="already exists"):
            copy_workspace_storage(src, dst)
        # Fresh destination copies fine.
        dst2 = NS(volume_name=None, user=user, name="dst2", shared_profile=False)
        copy_workspace_storage(src, dst2)
        assert (tmp_path / "alice" / "workspace-dst2" / "f").read_text() == "x"
    finally:
        get_settings.cache_clear()


# ── lifecycle: atomic transitions + orphan cleanup ─────────────────────────

def test_concurrent_start_only_launches_once(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image()
    ws = client.post("/api/workspaces", json={"name": "ws", "image_id": image_id}).json()
    set_workspace_status(ws["id"], "stopped")
    fake_docker_manager.launch_workspace.reset_mock()
    r1 = client.post(f"/api/workspaces/{ws['id']}/start")
    r2 = client.post(f"/api/workspaces/{ws['id']}/start")
    assert r1.status_code == 200
    assert r2.status_code == 400  # already "creating"
    assert fake_docker_manager.launch_workspace.call_count == 1


def test_delete_error_workspace_with_container_goes_through_manager(client, fake_docker_manager):
    setup_admin(client)
    image_id = add_image()
    ws = client.post("/api/workspaces", json={"name": "ws", "image_id": image_id}).json()
    db = SessionLocal()
    try:
        row = db.get(Workspace, ws["id"])
        row.status = "error"
        row.container_id = "deadbeef"
        db.commit()
    finally:
        db.close()
    fake_docker_manager.remove_workspace.reset_mock()
    r = client.delete(f"/api/workspaces/{ws['id']}")
    assert r.status_code == 204
    fake_docker_manager.remove_workspace.assert_called_once()


def _manager_with_fake_client():
    dm = DockerManager.__new__(DockerManager)
    dm.zone_id = 0
    dm._client = MagicMock()
    dm._pulling = set()
    import threading

    dm._pulling_lock = threading.Lock()
    return dm


def test_stop_removes_container_by_name_too():
    dm = _manager_with_fake_client()
    by_name = MagicMock()

    def _get(ref):
        if ref == "cove-ws-7":
            return by_name
        raise docker.errors.NotFound("x")

    dm._client.containers.get.side_effect = _get
    dm._remove_container_by_name("cove-ws-7")
    by_name.stop.assert_called_once()
    by_name.remove.assert_called_once_with(force=True)
    # Absent is a no-op.
    dm._remove_container_by_name("cove-ws-8")


# ── egress guard fails closed ──────────────────────────────────────────────

def test_egress_guard_raises_on_failure():
    dm = _manager_with_fake_client()
    # Helper image missing and the registry is down: the run itself fails.
    dm._client.images.get.side_effect = docker.errors.ImageNotFound("nope")
    dm._client.images.pull.side_effect = docker.errors.APIError("registry down")
    dm._client.containers.run.side_effect = docker.errors.ImageNotFound("nope")
    with pytest.raises(EgressGuardError):
        dm._apply_egress_guard(7)
    dm._client.images.get.side_effect = None
    # A rule that fails to apply (non-zero exit of the && chain).
    dm._client.containers.run.side_effect = docker.errors.ContainerError(
        MagicMock(), 1, "sh", "img", b"iptables: No chain"
    )
    with pytest.raises(EgressGuardError):
        dm._apply_egress_guard(7)


def test_egress_rules_flush_conntrack():
    dm = _manager_with_fake_client()
    script = dm._build_egress_rules(False, [])
    assert "conntrack -F" in script
    assert script.index("conntrack -F") > script.index("-j DROP")


def test_ensure_image_pulls_only_when_absent():
    dm = _manager_with_fake_client()
    dm._client.images.get.return_value = object()
    dm._ensure_image("nicolaka/netshoot:latest")
    dm._client.images.pull.assert_not_called()
    dm._client.images.get.side_effect = docker.errors.ImageNotFound("nope")
    dm._ensure_image("nicolaka/netshoot:latest")
    assert dm._client.images.pull.called or dm._client.api.pull.called


# ── admin settings: bounded numbers, render node shape, pids limit ─────────

def test_settings_reject_infinite_cpu_and_bad_render_node(client):
    setup_admin(client)
    r = client.put("/api/admin/settings", content=json.dumps({"workspace_cpu_limit": "Infinity"}),
                   headers={"Content-Type": "application/json"})
    assert r.status_code == 422, r.text
    r = client.put("/api/admin/settings", json={"workspace_gpu_render_node": "/dev/sda; rm -rf /"})
    assert r.status_code == 400, r.text
    r = client.put("/api/admin/settings", json={"workspace_pids_limit": 2048})
    assert r.status_code == 200 and r.json()["workspace_pids_limit"] == 2048


def test_resource_limits_include_pids(monkeypatch):
    import server.docker_manager as dm

    monkeypatch.setattr(dm, "get_workspace_cpu_limit", lambda db: 0.0)
    monkeypatch.setattr(dm, "get_workspace_memory_limit_mb", lambda db: 0)
    monkeypatch.setattr(dm, "get_workspace_pids_limit", lambda db: 4096)
    assert dm._resource_limits(None) == {"pids_limit": 4096}
    monkeypatch.setattr(dm, "get_workspace_pids_limit", lambda db: 0)
    assert dm._resource_limits(None) == {}


def test_user_docker_allowed_defaults_false():
    db = SessionLocal()
    try:
        u = User(username="zed", auth_provider="local")
        db.add(u)
        db.commit()
        db.refresh(u)
        assert u.docker_allowed is False
    finally:
        db.close()


def test_helper_script_staging_is_atomic(tmp_path, monkeypatch):
    import server.docker_manager as dm

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    for name in dm._HELPER_SCRIPTS:
        (src_dir / name).write_text("#!/bin/sh\necho hi\n")
    monkeypatch.setattr(dm, "_SCRIPTS_SRC_DIR", str(src_dir))
    monkeypatch.setenv("COVE_STORAGE_PATH", str(tmp_path / "store"))
    get_settings.cache_clear()
    try:
        dest = dm._stage_helper_scripts()
        assert (dest / "launch-url.sh").read_text().startswith("#!/bin/sh")
        assert not [p for p in Path(dest).iterdir() if p.name.endswith(".tmp")]
        assert oct((dest / "launch-url.sh").stat().st_mode & 0o777) == "0o755"
    finally:
        get_settings.cache_clear()
