from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse

from server import storage_local
from server.config import get_settings
from server.deps import CurrentUser, DbSession
from server.models import User, Zone
from server.net import client_ip
from server.schemas import FileListing

router = APIRouter(prefix="/api/files", tags=["files"])


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
    client = _agent_client(db, zone_id)
    if client is None:
        storage_local.delete(_user_base(user), path)
    else:
        with client as c:
            resp = c.delete("/agent/files", params={"username": user.username, "path": path})
            _raise_for_agent(resp)
    _audit(db, "files.delete", detail=f"zone{zone_id}:{path}", user=user, request=request)
