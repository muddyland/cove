"""Zone-agent API (served only when COVE_AGENT_MODE is set).

In agent mode this Cove process is not a user-facing control plane: it exposes
just enough for the control plane to drive it over mTLS. The Docker daemon itself
is reached over a separate mTLS Docker port (ghostunnel → socket-proxy); this API
covers what the Docker API cannot — file browsing (Phase 5) and workspace
migration (Phase 6). For now it carries a health probe.
"""

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse

from server import storage_local, storage_migrate
from server.config import get_settings
from server.schemas import FileListing
from server.security import decode_stream_token, is_valid_username

router = APIRouter(prefix="/agent", tags=["agent"])


def _agent_user_base(username: str) -> Path:
    """The storage base for a user on this agent. The username is supplied by the
    control plane (the only party that can reach this mTLS-gated API); validate it
    so it cannot escape the storage root."""
    if not is_valid_username(username):
        raise HTTPException(status_code=400, detail="Invalid username")
    settings = get_settings()
    root = settings.storage_path or (settings.data_dir / "workspaces")
    base = (root / username).resolve()
    base.mkdir(parents=True, exist_ok=True)
    return base


@router.get("/health")
def agent_health():
    return {"status": "ok", "role": "agent"}


def _public_id_from_host(host: str | None, settings) -> str | None:
    """Extract the workspace public_id from a ``{public_id}.{domain}`` host.

    Mirrors the control plane's helper; the agent knows its workspace domain from
    COVE_WORKSPACE_DOMAIN (provisioned at enrollment)."""
    if not host or not settings.workspace_domain:
        return None
    host = host.split(":", 1)[0]
    suffix = f".{settings.workspace_domain}"
    if host.endswith(suffix):
        label = host[: -len(suffix)]
        if label and "." not in label:
            return label
    return None


@router.get("/auth/forward")
def agent_forward_auth(request: Request):
    """Defense-in-depth ForwardAuth on the agent's own Traefik.

    The central edge already authenticated (and, in subdomain mode, set the
    ``cove_stream`` cookie). The agent re-verifies that the request carries a
    valid, unexpired stream token scoped to the workspace host — using only the
    provisioned stream-signing key, never the control plane's app secret. This
    blocks any request that somehow reaches the agent without passing the edge.

    The agent has no user DB, so it cannot check token revocation (the central
    edge does); it validates signature, type, expiry, and workspace scope.
    """
    settings = get_settings()
    host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host")
    public_id = _public_id_from_host(host, settings)
    if not public_id:
        return Response(status_code=401)
    cookie = request.cookies.get(settings.cookie_stream_name)
    if not cookie:
        return Response(status_code=401)
    payload = decode_stream_token(cookie)
    if not payload or payload.get("type") != "stream" or payload.get("ws") != public_id:
        return Response(status_code=401)
    return Response(status_code=200)


# ── File API (proxied to by the control plane's file browser over mTLS) ─────────


@router.get("/files", response_model=FileListing)
def agent_list_files(username: str, path: str = ""):
    return storage_local.list_dir(_agent_user_base(username), path)


@router.get("/files/download")
def agent_download_file(username: str, path: str):
    target = storage_local.resolve_download(_agent_user_base(username), path)
    return FileResponse(str(target), filename=target.name, content_disposition_type="attachment")


@router.post("/files/upload", status_code=status.HTTP_201_CREATED)
def agent_upload_file(
    username: str, path: str = Form(""), file: UploadFile = File(...)
):
    base = _agent_user_base(username)
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    return storage_local.save_upload(
        base, path, file.file.read, file.filename or "upload", max_bytes
    )


@router.delete("/files", status_code=status.HTTP_204_NO_CONTENT)
def agent_delete_file(username: str, path: str):
    storage_local.delete(_agent_user_base(username), path)


# ── Migration (workspace /config transfer, relayed by the control plane) ───────


@router.get("/migrate/export")
def agent_migrate_export(username: str, ws_name: str):
    base = _agent_user_base(username)
    src = base / storage_migrate.workspace_dirname(ws_name)
    if not src.is_dir():
        raise HTTPException(status_code=404, detail="Workspace storage not found")
    return StreamingResponse(
        storage_migrate.export_tar_stream(src), media_type="application/gzip"
    )


@router.post("/migrate/import", status_code=status.HTTP_204_NO_CONTENT)
async def agent_migrate_import(request: Request, username: str, ws_name: str):
    base = _agent_user_base(username)
    dst = base / storage_migrate.workspace_dirname(ws_name)
    with tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024) as tmp:
        async for chunk in request.stream():
            tmp.write(chunk)
        tmp.seek(0)
        storage_migrate.import_tar(dst, tmp)
