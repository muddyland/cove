from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from sqlalchemy import select

from server.config import get_settings
from server.deps import CurrentUser, DbSession
from server.models import UserTailscale, Workspace, WorkspaceImage
from server.net import client_ip
from server.schemas import StreamAuthOut, WorkspaceCreate, WorkspaceOut, WorkspaceUpdate
from server.security import create_stream_token

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


def _audit(db, action, *, detail=None, user=None, request=None):
    from server.main import record_audit

    ip = client_ip(request) if request is not None else None
    record_audit(db, action, detail=detail, user=user, ip=ip)


def _get_workspace_or_404(ws_id: int, user, db) -> Workspace:
    ws = db.get(Workspace, ws_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if not user.is_admin and ws.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return ws


@router.get("", response_model=list[WorkspaceOut])
def list_workspaces(user: CurrentUser, db: DbSession):
    # The dashboard always shows only the caller's own workspaces — even for
    # admins. Admins manage everyone's sessions via /api/admin/sessions.
    q = select(Workspace).where(Workspace.user_id == user.id)
    workspaces = db.scalars(q.order_by(Workspace.created_at.desc())).all()
    return [WorkspaceOut.from_workspace(ws) for ws in workspaces]


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def create_workspace(body: WorkspaceCreate, user: CurrentUser, db: DbSession, bg: BackgroundTasks, request: Request):
    image = db.get(WorkspaceImage, body.image_id)
    if not image or not image.enabled:
        raise HTTPException(status_code=404, detail="Image not found or disabled")

    # A startup URL is allowed when the image supports one (browser images with a
    # url_env, or the legacy "link" type). Validate the URL format if provided.
    url_capable = bool(image.url_env) or image.image_type == "link"
    target_url = body.target_url if url_capable else None
    if target_url:
        parsed = urlparse(target_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise HTTPException(status_code=400, detail="target_url must be a valid http/https URL")
    if image.image_type == "link" and not target_url:
        raise HTTPException(status_code=400, detail="target_url is required for link workspaces")

    if body.use_tailscale:
        ts = db.scalar(select(UserTailscale).where(UserTailscale.user_id == user.id))
        if not ts or not ts.auth_key:
            raise HTTPException(status_code=400, detail="Tailscale not configured")

    ws = Workspace(
        user_id=user.id,
        name=body.name,
        workspace_type=image.image_type,
        image_id=body.image_id,
        target_url=target_url,
        use_tailscale=body.use_tailscale,
        ts_exit_node=body.ts_exit_node or None,
        ts_accept_routes=body.ts_accept_routes,
        ts_accept_dns=body.ts_accept_dns,
        install_packages=body.install_packages or None,
        proot_apps=body.proot_apps or None,
        allow_sudo=body.allow_sudo,
        status="creating",
    )
    db.add(ws)
    db.commit()
    db.refresh(ws)

    _audit(db, "workspace.launch", detail=ws.public_id, user=user, request=request)

    from server.docker_manager import get_docker_manager
    bg.add_task(get_docker_manager().launch_workspace, ws.id)

    return WorkspaceOut.from_workspace(ws)


@router.get("/{ws_id}", response_model=WorkspaceOut)
def get_workspace(ws_id: int, user: CurrentUser, db: DbSession):
    ws = _get_workspace_or_404(ws_id, user, db)
    return WorkspaceOut.from_workspace(ws)


@router.post("/{ws_id}/stream-auth", response_model=StreamAuthOut)
def stream_auth(ws_id: int, user: CurrentUser, db: DbSession):
    """Mint the iframe URL the SPA uses to open a workspace stream.

    Subdomain mode: returns ``//{host}/?__cove_t=<token>`` where the one-time
    token bootstraps a per-workspace, host-only stream cookie (so the SPA's
    session cookie is never sent to the workspace origin). Subpath mode: returns
    the plain same-origin path (the session cookie authorizes it directly).
    """
    ws = _get_workspace_or_404(ws_id, user, db)
    if ws.status != "running":
        raise HTTPException(status_code=409, detail="Workspace is not running")

    settings = get_settings()
    if not settings.workspace_domain:
        return StreamAuthOut(url=f"/workspace/{ws.public_id}/")

    token = create_stream_token(user.id, ws.public_id)
    host = settings.workspace_host(ws.public_id)
    return StreamAuthOut(url=f"//{host}/?__cove_t={token}")


@router.patch("/{ws_id}", response_model=WorkspaceOut)
def update_workspace(
    ws_id: int, body: WorkspaceUpdate, user: CurrentUser, db: DbSession, request: Request
):
    ws = _get_workspace_or_404(ws_id, user, db)
    data = body.model_dump(exclude_unset=True)

    if data.get("target_url"):
        parsed = urlparse(data["target_url"])
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise HTTPException(status_code=400, detail="target_url must be a valid http/https URL")
    if data.get("use_tailscale"):
        ts = db.scalar(select(UserTailscale).where(UserTailscale.user_id == ws.user_id))
        if not ts or not ts.auth_key:
            raise HTTPException(status_code=400, detail="Tailscale not configured")

    nullable_text = {"target_url", "ts_exit_node", "install_packages", "proot_apps"}
    for key, value in data.items():
        if key == "name" and not (value or "").strip():
            continue  # never blank the name
        if key in nullable_text and isinstance(value, str) and value.strip() == "":
            value = None
        setattr(ws, key, value)
    db.commit()
    db.refresh(ws)
    _audit(db, "workspace.update", detail=ws.public_id, user=user, request=request)
    return WorkspaceOut.from_workspace(ws)


@router.post("/{ws_id}/stop", response_model=WorkspaceOut)
def stop_workspace(ws_id: int, user: CurrentUser, db: DbSession, bg: BackgroundTasks, request: Request):
    ws = _get_workspace_or_404(ws_id, user, db)
    if ws.status not in ("running", "creating"):
        raise HTTPException(status_code=400, detail=f"Cannot stop workspace in state: {ws.status}")
    ws.status = "stopping"
    db.commit()
    db.refresh(ws)
    _audit(db, "workspace.stop", detail=ws.public_id, user=user, request=request)
    from server.docker_manager import get_docker_manager
    bg.add_task(get_docker_manager().stop_workspace, ws.id)
    return WorkspaceOut.from_workspace(ws)


@router.post("/{ws_id}/start", response_model=WorkspaceOut)
def start_workspace(ws_id: int, user: CurrentUser, db: DbSession, bg: BackgroundTasks, request: Request):
    ws = _get_workspace_or_404(ws_id, user, db)
    if ws.status != "stopped":
        raise HTTPException(status_code=400, detail=f"Cannot start workspace in state: {ws.status}")
    ws.status = "creating"
    ws.error_message = None
    db.commit()
    db.refresh(ws)
    _audit(db, "workspace.start", detail=ws.public_id, user=user, request=request)
    from server.docker_manager import get_docker_manager
    bg.add_task(get_docker_manager().launch_workspace, ws.id)
    return WorkspaceOut.from_workspace(ws)


@router.delete("/{ws_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(ws_id: int, user: CurrentUser, db: DbSession, bg: BackgroundTasks, request: Request):
    ws = _get_workspace_or_404(ws_id, user, db)
    public_id = ws.public_id
    _audit(db, "workspace.delete", detail=public_id, user=user, request=request)
    if ws.status in ("running", "creating", "stopping"):
        from server.docker_manager import get_docker_manager
        bg.add_task(get_docker_manager().remove_workspace, ws.id)
    else:
        db.delete(ws)
        db.commit()
