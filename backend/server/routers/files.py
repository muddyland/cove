from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select

from server import settings_store, storage_local
from server.config import get_settings
from server.deps import CurrentUser, DbSession
from server.models import TrashEntry, User, Zone
from server.net import client_ip
from server.schemas import FileListing, FileOp, FilePathIn, TrashEntryOut

router = APIRouter(prefix="/api/files", tags=["files"])

# Sentinel far-future expiry for "keep until manually emptied" (retention == 0):
# expires_at is NOT NULL, and the purge sweep only removes rows already past it.
_NEVER = timedelta(days=36500)


def _trash_or_404(db, user: User, entry_id: int) -> TrashEntry:
    entry = db.get(TrashEntry, entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status_code=404, detail="Trash item not found")
    return entry


def _audit(db, action, *, detail=None, user=None, request=None):
    from server.main import record_audit

    ip = client_ip(request) if request is not None else None
    record_audit(db, action, detail=detail, user=user, ip=ip)


def _user_base(user: User) -> Path:
    settings = get_settings()
    root = settings.storage_path or (settings.data_dir / "workspaces")
    base = (root / user.username).resolve()
    base.mkdir(parents=True, exist_ok=True)
    return base


def _zone_or_404(db, zone_id: int) -> Zone:
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="Zone not found")
    return zone


def _agent_client(db, zone_id: int):
    """An mTLS client for a remote zone's agent file API, or None for the local
    zone (zone 0). Raises 409 if the remote zone isn't reachable over mTLS yet."""
    if zone_id == 0:
        return None
    from server.docker_manager import _zone_has_mtls, zone_agent_client

    zone = _zone_or_404(db, zone_id)
    if not _zone_has_mtls(zone):
        raise HTTPException(status_code=409, detail="Zone is not enrolled for mTLS")
    return zone_agent_client(zone)


def _raise_for_agent(resp) -> None:
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)


@router.get("", response_model=FileListing)
def list_files(user: CurrentUser, db: DbSession, path: str = "", zone_id: int = 0):
    client = _agent_client(db, zone_id)
    if client is None:
        return storage_local.list_dir(_user_base(user), path)
    with client as c:
        resp = c.get("/agent/files", params={"username": user.username, "path": path})
        _raise_for_agent(resp)
        return resp.json()


@router.get("/download")
def download_file(user: CurrentUser, db: DbSession, path: str, zone_id: int = 0):
    client = _agent_client(db, zone_id)
    if client is None:
        target = storage_local.resolve_download(_user_base(user), path)
        return FileResponse(str(target), filename=target.name, content_disposition_type="attachment")

    # Stream the file through from the agent, keeping the mTLS client open until
    # the response body is fully sent (closed by the generator's finally).
    req = client.build_request(
        "GET", "/agent/files/download", params={"username": user.username, "path": path}
    )
    resp = client.send(req, stream=True)
    if resp.status_code >= 400:
        try:
            detail = resp.read().decode()[:200]
        finally:
            resp.close()
            client.close()
        raise HTTPException(status_code=resp.status_code, detail=detail or "Agent error")

    def _body():
        try:
            yield from resp.iter_bytes()
        finally:
            resp.close()
            client.close()

    disposition = resp.headers.get("content-disposition", f'attachment; filename="{Path(path).name}"')
    return StreamingResponse(
        _body(),
        media_type=resp.headers.get("content-type", "application/octet-stream"),
        headers={"Content-Disposition": disposition},
    )


@router.post("/upload", status_code=status.HTTP_201_CREATED)
def upload_file(
    user: CurrentUser,
    db: DbSession,
    request: Request,
    path: str = Form(""),
    file: UploadFile = File(...),
    zone_id: int = 0,
):
    client = _agent_client(db, zone_id)
    if client is None:
        max_bytes = get_settings().max_upload_mb * 1024 * 1024
        result = storage_local.save_upload(
            _user_base(user), path, file.file.read, file.filename or "upload", max_bytes
        )
        _audit(db, "files.upload", detail=result["path"], user=user, request=request)
        return result

    with client as c:
        files = {"file": (file.filename or "upload", file.file, file.content_type)}
        resp = c.post(
            "/agent/files/upload",
            params={"username": user.username, "path": path},
            files=files,
        )
        _raise_for_agent(resp)
        result = resp.json()
    _audit(db, "files.upload", detail=f"zone{zone_id}:{result['path']}", user=user, request=request)
    return result


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_path(
    user: CurrentUser, db: DbSession, request: Request, path: str, zone_id: int = 0
):
    """Permanent delete (the row-level 'Delete permanently'). Soft delete is /trash."""
    client = _agent_client(db, zone_id)
    if client is None:
        storage_local.delete(_user_base(user), path)
    else:
        with client as c:
            resp = c.delete("/agent/files", params={"username": user.username, "path": path})
            _raise_for_agent(resp)
    _audit(db, "files.delete", detail=f"zone{zone_id}:{path}", user=user, request=request)


@router.get("/download-archive")
def download_archive(user: CurrentUser, db: DbSession, path: str = "", zone_id: int = 0):
    """Download a folder (or file) as a streamed zip."""
    name = Path(path).name or "archive"
    disposition = f'attachment; filename="{name}.zip"'
    client = _agent_client(db, zone_id)
    if client is None:
        return StreamingResponse(
            storage_local.iter_zip(_user_base(user), path),
            media_type="application/zip",
            headers={"Content-Disposition": disposition},
        )

    # Stream the archive through from the agent, closing the mTLS client only when
    # the body is fully sent (same pattern as download_file).
    req = client.build_request(
        "GET", "/agent/files/download-archive", params={"username": user.username, "path": path}
    )
    resp = client.send(req, stream=True)
    if resp.status_code >= 400:
        try:
            detail = resp.read().decode()[:200]
        finally:
            resp.close()
            client.close()
        raise HTTPException(status_code=resp.status_code, detail=detail or "Agent error")

    def _body():
        try:
            yield from resp.iter_bytes()
        finally:
            resp.close()
            client.close()

    return StreamingResponse(
        _body(),
        media_type="application/zip",
        headers={"Content-Disposition": resp.headers.get("content-disposition", disposition)},
    )


@router.post("/copy")
def copy_path(
    user: CurrentUser, db: DbSession, request: Request, body: FileOp, zone_id: int = 0
):
    client = _agent_client(db, zone_id)
    if client is None:
        result = storage_local.copy(_user_base(user), body.src, body.dst_dir)
    else:
        with client as c:
            resp = c.post(
                "/agent/files/copy",
                params={"username": user.username},
                data={"src": body.src, "dst_dir": body.dst_dir},
            )
            _raise_for_agent(resp)
            result = resp.json()
    _audit(db, "files.copy", detail=f"zone{zone_id}:{body.src}", user=user, request=request)
    return result


@router.post("/move")
def move_path(
    user: CurrentUser, db: DbSession, request: Request, body: FileOp, zone_id: int = 0
):
    client = _agent_client(db, zone_id)
    if client is None:
        result = storage_local.move(_user_base(user), body.src, body.dst_dir)
    else:
        with client as c:
            resp = c.post(
                "/agent/files/move",
                params={"username": user.username},
                data={"src": body.src, "dst_dir": body.dst_dir},
            )
            _raise_for_agent(resp)
            result = resp.json()
    _audit(db, "files.move", detail=f"zone{zone_id}:{body.src}", user=user, request=request)
    return result


# ── Trash (soft delete) ────────────────────────────────────────────────────────

def _trash_bytes(db, user: User, path: str, zone_id: int) -> dict:
    """Move the item into the trash bucket on its zone; return storage metadata."""
    client = _agent_client(db, zone_id)
    if client is None:
        return storage_local.trash_move(_user_base(user), path)
    with client as c:
        resp = c.post(
            "/agent/files/trash", params={"username": user.username}, data={"path": path}
        )
        _raise_for_agent(resp)
        return resp.json()


@router.post("/trash", response_model=TrashEntryOut, status_code=status.HTTP_201_CREATED)
def trash_path(
    user: CurrentUser, db: DbSession, request: Request, body: FilePathIn, zone_id: int = 0
):
    """Soft delete: move the item to the per-user trash and record its metadata."""
    info = _trash_bytes(db, user, body.path, zone_id)
    retention = settings_store.get_trash_retention_days(db)
    now = datetime.now(timezone.utc)
    entry = TrashEntry(
        user_id=user.id,
        zone_id=zone_id,
        token=info["token"],
        original_path=body.path,
        name=info["name"],
        is_dir=info["is_dir"],
        size=info["size"],
        deleted_at=now,
        expires_at=now + (timedelta(days=retention) if retention > 0 else _NEVER),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    _audit(db, "files.trash", detail=f"zone{zone_id}:{body.path}", user=user, request=request)
    return TrashEntryOut.model_validate(entry)


@router.get("/trash", response_model=list[TrashEntryOut])
def list_trash(user: CurrentUser, db: DbSession, zone_id: int = 0):
    rows = db.scalars(
        select(TrashEntry)
        .where(TrashEntry.user_id == user.id, TrashEntry.zone_id == zone_id)
        .order_by(TrashEntry.deleted_at.desc())
    ).all()
    return [TrashEntryOut.model_validate(r) for r in rows]


@router.post("/trash/{entry_id}/restore")
def restore_trash(
    user: CurrentUser, db: DbSession, request: Request, entry_id: int
):
    entry = _trash_or_404(db, user, entry_id)
    client = _agent_client(db, entry.zone_id)
    if client is None:
        result = storage_local.trash_restore(_user_base(user), entry.token, entry.original_path)
    else:
        with client as c:
            resp = c.post(
                "/agent/files/restore",
                params={"username": user.username},
                data={"token": entry.token, "original_path": entry.original_path},
            )
            _raise_for_agent(resp)
            result = resp.json()
    db.delete(entry)
    db.commit()
    _audit(db, "files.restore", detail=f"zone{entry.zone_id}:{entry.original_path}", user=user, request=request)
    return result


@router.delete("/trash/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def purge_trash(
    user: CurrentUser, db: DbSession, request: Request, entry_id: int
):
    entry = _trash_or_404(db, user, entry_id)
    _purge_trash_bytes(db, user.username, entry)
    detail = f"zone{entry.zone_id}:{entry.original_path}"
    db.delete(entry)
    db.commit()
    _audit(db, "files.purge", detail=detail, user=user, request=request)


def _purge_trash_bytes(db, username: str, entry: TrashEntry) -> None:
    """Remove a trashed item's bytes on its zone. Used by the endpoint and the sweep."""
    client = _agent_client(db, entry.zone_id)
    if client is None:
        base = (get_settings().storage_path or (get_settings().data_dir / "workspaces")) / username
        storage_local.trash_purge(base.resolve(), entry.token)
    else:
        with client as c:
            resp = c.delete(
                "/agent/files/trash",
                params={"username": username, "token": entry.token},
            )
            # A 404 (already gone) is fine when reclaiming bytes.
            if resp.status_code not in (204, 404):
                _raise_for_agent(resp)
