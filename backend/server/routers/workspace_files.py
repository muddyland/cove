"""Files inside a running workspace, for the stream page's file browser.

Reads and writes go through the workspace container (docker exec / archive
API), so they work the same for persistent, ephemeral and remote-zone
workspaces, and uploads land owned by the workspace user. Paths are relative
to the workspace home (/config) and can't climb out of it.
"""

from datetime import datetime
from tempfile import SpooledTemporaryFile
from typing import NoReturn
from urllib.parse import quote

import docker.errors
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from server.config import get_settings
from server.deps import CurrentUser, DbSession
from server.net import client_ip

router = APIRouter(prefix="/api/workspaces/{ws_id}/fs", tags=["workspace-files"])


class WsFileEntry(BaseModel):
    name: str
    type: str  # dir | file | link
    size: int
    modified: datetime


class WsListing(BaseModel):
    path: str
    entries: list[WsFileEntry]


class WsUploadOut(BaseModel):
    path: str
    name: str
    size: int


class WsMkdirIn(BaseModel):
    path: str


def _running_ws(ws_id: int, user, db):
    from server.routers.workspaces import _get_workspace_or_404

    ws = _get_workspace_or_404(ws_id, user, db)
    if ws.status != "running":
        raise HTTPException(status_code=409, detail="Start the workspace to browse its files")
    from server.docker_manager import get_docker_manager

    return ws, get_docker_manager(ws.zone_id)


def _fail(exc: Exception) -> NoReturn:
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc) or "Bad path")
    if isinstance(exc, IsADirectoryError):
        raise HTTPException(status_code=400, detail="That's a folder")
    if isinstance(exc, (FileNotFoundError, docker.errors.NotFound)):
        raise HTTPException(status_code=404, detail="Not found")
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc) or "Not allowed")
    if isinstance(exc, docker.errors.APIError):
        raise HTTPException(status_code=502, detail="The workspace didn't respond")
    raise exc


def _clean_rel(path: str) -> str:
    from server.docker_manager import WS_HOME, ws_abs_path

    return ws_abs_path(path)[len(WS_HOME):].lstrip("/")


def _audit(db, action, detail, user, request):
    from server.main import record_audit

    record_audit(db, action, detail=detail, user=user, ip=client_ip(request))


@router.get("", response_model=WsListing)
def list_ws_files(ws_id: int, user: CurrentUser, db: DbSession, path: str = ""):
    ws, dm = _running_ws(ws_id, user, db)
    try:
        rel = _clean_rel(path)
        return WsListing(path=rel, entries=dm.ws_list(ws, rel))
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP errors
        _fail(exc)


@router.get("/download")
def download_ws_file(ws_id: int, user: CurrentUser, db: DbSession, request: Request, path: str):
    ws, dm = _running_ws(ws_id, user, db)
    try:
        name, size, chunks = dm.ws_open_file(ws, _clean_rel(path))
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    _audit(db, "workspace.files.download", f"{ws.public_id}:{_clean_rel(path)}", user, request)
    return StreamingResponse(
        chunks,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "Content-Length": str(size),
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/upload", response_model=WsUploadOut, status_code=201)
def upload_ws_file(
    ws_id: int,
    user: CurrentUser,
    db: DbSession,
    request: Request,
    path: str = Form(""),
    file: UploadFile = File(...),
):
    ws, dm = _running_ws(ws_id, user, db)
    limit = get_settings().max_upload_mb * 1024 * 1024
    size = 0
    with SpooledTemporaryFile(max_size=32 * 1024 * 1024) as buf:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise HTTPException(status_code=413, detail=f"Over the {get_settings().max_upload_mb} MB upload limit")
            buf.write(chunk)
        try:
            rel = _clean_rel(path)
            name = dm.ws_put_file(ws, rel, file.filename or "upload", buf, size)
        except Exception as exc:  # noqa: BLE001
            _fail(exc)
    _audit(db, "workspace.files.upload", f"{ws.public_id}:{rel}/{name}".replace("//", "/"), user, request)
    return WsUploadOut(path=rel, name=name, size=size)


@router.post("/mkdir", status_code=201)
def mkdir_ws(ws_id: int, body: WsMkdirIn, user: CurrentUser, db: DbSession):
    ws, dm = _running_ws(ws_id, user, db)
    try:
        rel = _clean_rel(body.path)
        if not rel:
            raise ValueError("Give the folder a name")
        dm.ws_mkdir(ws, rel)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return {"path": rel}
