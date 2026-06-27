"""Workspace migration between zones, relayed through the control plane.

A workspace is pinned to one zone; migration copies its ``/config`` to the
destination zone, flips the pin, then deletes the source copy (copy-then-delete,
so any failure before the flip leaves the source intact for a clean rollback).
All transfers ride the existing mTLS channels; when both endpoints are remote the
bytes relay through the control plane (source agent -> CP -> dest agent), which
works even when the two zones cannot reach each other.
"""

import logging
import tempfile
from pathlib import Path

from server import storage_local, storage_migrate
from server.config import get_settings
from server.db import SessionLocal
from server.docker_manager import zone_agent_client
from server.models import Workspace, Zone

logger = logging.getLogger(__name__)


def _cp_storage_root() -> Path:
    s = get_settings()
    return Path(s.storage_path or (s.data_dir / "workspaces"))


def _local_user_base(username: str) -> Path:
    return _cp_storage_root() / username


def _local_ws_dir(username: str, ws_name: str) -> Path:
    return _local_user_base(username) / storage_migrate.workspace_dirname(ws_name)


def _zone(db, zone_id: int) -> Zone:
    return db.get(Zone, zone_id)


def _push_to_remote(db, zone_id: int, username: str, ws_name: str, byte_iter) -> None:
    with zone_agent_client(_zone(db, zone_id)) as c:
        resp = c.post(
            "/agent/migrate/import",
            params={"username": username, "ws_name": ws_name},
            content=byte_iter,
        )
        resp.raise_for_status()


def _import_local(username: str, ws_name: str, byte_iter) -> None:
    dst = _local_ws_dir(username, ws_name)
    with tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024) as tmp:
        for chunk in byte_iter:
            tmp.write(chunk)
        tmp.seek(0)
        storage_migrate.import_tar(dst, tmp)


def _relay(db, src_zone: int, dst_zone: int, username: str, ws_name: str) -> None:
    """Copy the workspace storage from src_zone to dst_zone. At least one side is
    remote (same-zone migration is rejected upstream)."""
    params = {"username": username, "ws_name": ws_name}
    if src_zone == 0:
        # Local source -> remote dest.
        src = _local_ws_dir(username, ws_name)
        gen = storage_migrate.export_tar_stream(src) if src.is_dir() else iter(())
        _push_to_remote(db, dst_zone, username, ws_name, gen)
    elif dst_zone == 0:
        # Remote source -> local dest.
        with zone_agent_client(_zone(db, src_zone)) as c:
            with c.stream("GET", "/agent/migrate/export", params=params) as r:
                r.raise_for_status()
                _import_local(username, ws_name, r.iter_bytes())
    else:
        # Remote -> remote, relayed through the control plane.
        with zone_agent_client(_zone(db, src_zone)) as cs:
            with cs.stream("GET", "/agent/migrate/export", params=params) as r:
                r.raise_for_status()
                _push_to_remote(db, dst_zone, username, ws_name, r.iter_bytes())


def _delete_source(db, src_zone: int, username: str, ws_name: str) -> None:
    dirname = storage_migrate.workspace_dirname(ws_name)
    if src_zone == 0:
        storage_local.delete(_local_user_base(username), dirname)
    else:
        with zone_agent_client(_zone(db, src_zone)) as c:
            c.delete("/agent/files", params={"username": username, "path": dirname})


def run_migration(ws_id: int, src_zone: int, dst_zone: int) -> None:
    """Background task: relay storage, flip the zone pin, then clean up the source.

    The workspace is left ``stopped`` on the destination (the user starts it when
    ready). On any relay failure the pin stays on the source and the workspace is
    marked ``error`` — its source storage was only copied, never moved, so it is
    intact."""
    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        if ws is None:
            return
        username = ws.user.username
        ws_name = ws.name

        try:
            _relay(db, src_zone, dst_zone, username, ws_name)
        except Exception as exc:
            logger.warning("Migration relay failed for workspace %s: %s", ws_id, exc)
            ws.zone_id = src_zone
            ws.status = "error"
            ws.error_message = f"Migration failed: {exc}"
            db.commit()
            return

        # Commit point: the workspace now belongs to the destination zone.
        ws.zone_id = dst_zone
        ws.container_id = None
        ws.container_name = None
        ws.volume_name = None
        ws.status = "stopped"
        ws.error_message = None
        db.commit()

        # Copy-then-delete: best-effort removal of the now-stale source copy.
        try:
            _delete_source(db, src_zone, username, ws_name)
        except Exception as exc:
            logger.warning("Post-migration source cleanup failed for %s: %s", ws_id, exc)
    finally:
        db.close()
