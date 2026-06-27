"""Phase 6: workspace migration — tar payload, agent transfer, orchestration."""

import io

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import storage_migrate
from server.config import get_settings
from server.db import SessionLocal
from server.models import Workspace
from server.routers import agent as agent_router
from server.tests.helpers import add_image, setup_admin

# ── tar payload round-trip ─────────────────────────────────────────────────

def test_tar_roundtrip_preserves_tree_and_symlinks(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("hello")
    (src / "sub" / "b.txt").write_text("world")
    (src / "link").symlink_to("a.txt")

    data = b"".join(storage_migrate.export_tar_stream(src))
    dst = tmp_path / "dst"
    storage_migrate.import_tar(dst, io.BytesIO(data))

    assert (dst / "a.txt").read_text() == "hello"
    assert (dst / "sub" / "b.txt").read_text() == "world"
    assert (dst / "link").is_symlink()


def test_import_skips_absolute_symlink_junk(tmp_path):
    # Chromium leaves a SingletonSocket symlink pointing at an absolute path; the
    # strict 'data' tar filter rejects it. Migration must skip that junk, not fail
    # the whole import — while still extracting the real files.
    src = tmp_path / "src"
    src.mkdir()
    (src / "real.txt").write_text("keep me")
    (src / "SingletonSocket").symlink_to("/run/abs/socket")

    data = b"".join(storage_migrate.export_tar_stream(src))
    dst = tmp_path / "dst"
    storage_migrate.import_tar(dst, io.BytesIO(data))  # must not raise

    assert (dst / "real.txt").read_text() == "keep me"
    assert not (dst / "SingletonSocket").is_symlink()  # unsafe member skipped


def test_export_excludes_proot_apps(tmp_path):
    # proot-apps re-install on launch, so they must not bloat the payload (a real
    # VDI home was 14GB of proot-apps vs ~8MB of config).
    src = tmp_path / "src"
    (src / "proot-apps" / "chrome").mkdir(parents=True)
    (src / "proot-apps" / "chrome" / "blob").write_bytes(b"x" * 4096)
    (src / ".config").mkdir()
    (src / ".config" / "settings").write_text("keep")

    data = b"".join(storage_migrate.export_tar_stream(src))
    dst = tmp_path / "dst"
    storage_migrate.import_tar(dst, io.BytesIO(data))

    assert (dst / ".config" / "settings").read_text() == "keep"
    assert not (dst / "proot-apps").exists()  # regeneratable, excluded


# ── agent export/import endpoints ──────────────────────────────────────────

def _agent_root():
    s = get_settings()
    return s.storage_path or (s.data_dir / "workspaces")


def test_agent_export_then_import_under_new_name():
    app = FastAPI()
    app.include_router(agent_router.router)
    c = TestClient(app)

    base = (_agent_root() / "alice")
    foo = base / "workspace-foo"
    foo.mkdir(parents=True, exist_ok=True)
    (foo / "data.txt").write_text("payload")

    exp = c.get("/agent/migrate/export", params={"username": "alice", "ws_name": "foo"})
    assert exp.status_code == 200, exp.text

    imp = c.post(
        "/agent/migrate/import",
        params={"username": "alice", "ws_name": "bar"},
        content=exp.content,
    )
    assert imp.status_code == 204, imp.text
    assert (base / "workspace-bar" / "data.txt").read_text() == "payload"


def test_agent_export_missing_workspace_404():
    app = FastAPI()
    app.include_router(agent_router.router)
    c = TestClient(app)
    r = c.get("/agent/migrate/export", params={"username": "nobody", "ws_name": "x"})
    assert r.status_code == 404


# ── migrate endpoint preconditions ─────────────────────────────────────────

def _stopped_ws(client, zone_id=0, ephemeral=False):
    image_id = add_image(name="Desktop", image_type="desktop")
    body = {"name": "ws", "image_id": image_id, "zone_id": zone_id, "ephemeral": ephemeral}
    ws = client.post("/api/workspaces", json=body).json()
    db = SessionLocal()
    try:
        row = db.get(Workspace, ws["id"])
        row.status = "stopped"
        db.commit()
    finally:
        db.close()
    return ws["id"]


def test_migrate_rejects_same_zone(client):
    setup_admin(client)
    ws_id = _stopped_ws(client, zone_id=0)
    r = client.post(f"/api/workspaces/{ws_id}/migrate", json={"target_zone_id": 0})
    assert r.status_code == 400


def test_migrate_rejects_running(client):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post("/api/workspaces", json={"name": "ws", "image_id": image_id}).json()
    # Freshly created is "creating" (not at rest).
    r = client.post(f"/api/workspaces/{ws['id']}/migrate", json={"target_zone_id": 1})
    assert r.status_code == 409


def test_migrate_rejects_ephemeral(client):
    setup_admin(client)
    ws_id = _stopped_ws(client, ephemeral=True)
    zid = client.post(
        "/api/admin/zones", json={"name": "Z", "endpoint_host": "10.0.0.9"}
    ).json()["id"]
    r = client.post(f"/api/workspaces/{ws_id}/migrate", json={"target_zone_id": zid})
    assert r.status_code == 400


def test_migrate_rejects_unknown_zone(client):
    setup_admin(client)
    ws_id = _stopped_ws(client)
    r = client.post(f"/api/workspaces/{ws_id}/migrate", json={"target_zone_id": 999})
    assert r.status_code == 404


def test_migrate_sets_migrating_and_schedules(client):
    setup_admin(client)
    ws_id = _stopped_ws(client)
    zid = client.post(
        "/api/admin/zones", json={"name": "Z", "endpoint_host": "10.0.0.9"}
    ).json()["id"]
    r = client.post(f"/api/workspaces/{ws_id}/migrate", json={"target_zone_id": zid})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "migrating"


# ── orchestration state machine ────────────────────────────────────────────

def _make_zone(client) -> int:
    return client.post(
        "/api/admin/zones", json={"name": "Dest", "endpoint_host": "10.0.0.9"}
    ).json()["id"]


def test_run_migration_flips_zone_and_cleans_source(client, monkeypatch):
    setup_admin(client)
    ws_id = _stopped_ws(client)
    dst = _make_zone(client)
    from server import migration

    calls = {}
    monkeypatch.setattr(migration, "_relay", lambda *a, **k: calls.setdefault("relay", True))
    monkeypatch.setattr(
        migration, "_delete_source", lambda *a, **k: calls.setdefault("deleted", a)
    )

    migration.run_migration(ws_id, 0, dst)

    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        assert ws.zone_id == dst
        assert ws.status == "stopped"
        assert ws.container_id is None
    finally:
        db.close()
    assert calls.get("relay") and "deleted" in calls


def test_run_migration_rolls_back_on_failure(client, monkeypatch):
    setup_admin(client)
    ws_id = _stopped_ws(client)
    dst = _make_zone(client)
    from server import migration

    def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(migration, "_relay", _boom)
    deleted = []
    monkeypatch.setattr(migration, "_delete_source", lambda *a, **k: deleted.append(a))

    migration.run_migration(ws_id, 0, dst)

    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        assert ws.zone_id == 0  # stayed on source
        assert ws.status == "error"
    finally:
        db.close()
    assert not deleted  # source never deleted on failure
