import ipaddress
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from sqlalchemy import select

from server.config import get_settings
from server.deps import CurrentUser, DbSession
from server.models import UserGluetun, UserTailscale, Workspace, WorkspaceImage
from server.net import client_ip
from server.schemas import (
    LanPolicyOut,
    StreamAuthOut,
    WorkspaceClone,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceStats,
    WorkspaceUpdate,
)
from server.security import create_stream_bootstrap_token

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


def _audit(db, action, *, detail=None, user=None, request=None):
    from server.main import record_audit

    ip = client_ip(request) if request is not None else None
    record_audit(db, action, detail=detail, user=user, ip=ip)


def _clean_dns(raw: str | None) -> str | None:
    """Validate + normalize a DNS server list to a space-separated IP string.

    Accepts space/comma separated IPv4/IPv6 addresses. Returns None when empty,
    raises 400 on any malformed entry. Capped at 6 servers (resolv.conf limit).
    """
    if not raw or not raw.strip():
        return None
    parts = [p for p in re.split(r"[,\s]+", raw.strip()) if p]
    cleaned: list[str] = []
    for p in parts:
        try:
            ip = ipaddress.ip_address(p)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid DNS server: {p}")
        s = str(ip)
        if s not in cleaned:
            cleaned.append(s)
    if len(cleaned) > 6:
        raise HTTPException(status_code=400, detail="At most 6 DNS servers allowed")
    return " ".join(cleaned) if cleaned else None


def _validate_target_url(url: str) -> None:
    """Reject anything that isn't a clean http(s) URL.

    Beyond scheme/host, reject whitespace and control characters: the URL is
    appended to the browser's command line (e.g. ``CHROME_CLI``), so a space
    would split it into extra argv tokens (``--proxy-server=…``,
    ``--disable-web-security``, …), silently undoing the kiosk lockdown.
    """
    if re.search(r"[\s\x00-\x1f\x7f]", url):
        raise HTTPException(status_code=400, detail="target_url must not contain whitespace")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="target_url must be a valid http/https URL")


# Package / proot-app names: alphanumerics plus a safe punctuation subset. This
# deliberately excludes every shell metacharacter and whitespace, so a token can
# never break out of the install command (the values flow into init-script env
# consumed by proot-apps / the universal-package-install mod).
_PKG_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]*$")


def _validate_package_list(raw: str, field: str) -> None:
    for tok in re.split(r"[,\s]+", raw.strip()):
        if tok and not _PKG_TOKEN_RE.match(tok):
            raise HTTPException(status_code=400, detail=f"Invalid {field} entry: {tok!r}")


def _validate_appimage_list(raw: str) -> None:
    for tok in re.split(r"[,\s]+", raw.strip()):
        if not tok:
            continue
        parsed = urlparse(tok)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise HTTPException(status_code=400, detail=f"Invalid AppImage URL: {tok!r}")


def _validate_app_fields(install_packages, proot_apps, appimages) -> None:
    """Validate the per-workspace package/app inputs (no-ops on None/empty)."""
    if install_packages:
        _validate_package_list(install_packages, "install_packages")
    if proot_apps:
        _validate_package_list(proot_apps, "proot_apps")
    if appimages:
        _validate_appimage_list(appimages)


def _validate_routing(db, user_id: int, use_tailscale: bool, use_gluetun: bool) -> None:
    """Validate the (mutually exclusive) VPN routing choice for a workspace."""
    if use_tailscale and use_gluetun:
        raise HTTPException(
            status_code=400, detail="Choose either Tailscale or Gluetun, not both"
        )
    if use_tailscale:
        ts = db.scalar(select(UserTailscale).where(UserTailscale.user_id == user_id))
        if not ts or not ts.auth_key:
            raise HTTPException(status_code=400, detail="Tailscale not configured")
    if use_gluetun:
        g = db.scalar(select(UserGluetun).where(UserGluetun.user_id == user_id))
        if not g or not g.enabled or not g.config_file:
            raise HTTPException(
                status_code=400, detail="Gluetun not configured (upload a VPN config in Preferences)"
            )


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
        _validate_target_url(target_url)
    if image.image_type == "link" and not target_url:
        raise HTTPException(status_code=400, detail="target_url is required for link workspaces")

    _validate_app_fields(body.install_packages, body.proot_apps, body.appimages)

    _validate_routing(db, user.id, body.use_tailscale, body.use_gluetun)

    ws = Workspace(
        user_id=user.id,
        name=body.name,
        workspace_type=image.image_type,
        image_id=body.image_id,
        target_url=target_url,
        kiosk=body.kiosk,
        kiosk_dark=body.kiosk_dark,
        kiosk_menu=body.kiosk_menu,
        use_tailscale=body.use_tailscale,
        use_gluetun=body.use_gluetun,
        ephemeral=body.ephemeral,
        lan_access=body.lan_access,
        ts_exit_node=body.ts_exit_node or None,
        ts_accept_routes=body.ts_accept_routes,
        ts_accept_dns=body.ts_accept_dns,
        custom_dns=body.custom_dns,
        dns_servers=_clean_dns(body.dns_servers),
        install_packages=body.install_packages or None,
        proot_apps=body.proot_apps or None,
        appimages=body.appimages or None,
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


@router.get("/stats", response_model=dict[int, WorkspaceStats])
def workspace_stats(user: CurrentUser, db: DbSession):
    """Live CPU/memory for the caller's running workspaces, keyed by id.

    Only running workspaces are included; ids absent from the map have no
    current stats. Reads run concurrently so the call stays responsive even
    with several active containers (each Docker stats sample takes ~1s).
    """
    q = select(Workspace).where(
        Workspace.user_id == user.id, Workspace.status == "running"
    )
    running = [ws for ws in db.scalars(q).all() if ws.container_id]
    if not running:
        return {}

    from server.docker_manager import get_docker_manager
    dm = get_docker_manager()

    ts_running = [ws for ws in running if ws.use_tailscale]

    raw: dict[int, dict] = {}
    ts_ips: dict[int, str] = {}
    workers = min(8, len(running) + len(ts_running))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        stat_futs = {ex.submit(dm.get_stats, ws.container_id): ws for ws in running}
        ip_futs = {ex.submit(dm.get_tailscale_ip, ws.id): ws for ws in ts_running}
        for fut in as_completed(stat_futs):
            data = fut.result()
            if data:
                raw[stat_futs[fut].id] = data
        for fut in as_completed(ip_futs):
            ip = fut.result()
            if ip:
                ts_ips[ip_futs[fut].id] = ip

    results: dict[int, WorkspaceStats] = {
        ws_id: WorkspaceStats(**data, tailscale_ip=ts_ips.get(ws_id))
        for ws_id, data in raw.items()
    }
    return results


@router.post("/{ws_id}/clone", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def clone_workspace(
    ws_id: int, body: WorkspaceClone, user: CurrentUser, db: DbSession,
    bg: BackgroundTasks, request: Request,
):
    """Clone a workspace, copying its full persistent home (/config).

    Useful for switching distros while keeping browser sessions, app config, etc.
    Optionally re-targets a different image. The source must be stopped so its
    files are at rest (avoids copying a half-written browser DB).
    """
    src = _get_workspace_or_404(ws_id, user, db)
    if src.status not in ("stopped", "error"):
        raise HTTPException(
            status_code=409, detail="Stop the workspace before cloning so its files are at rest"
        )

    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    from server.docker_manager import _sanitize
    if _sanitize(name) == _sanitize(src.name):
        raise HTTPException(status_code=400, detail="Choose a name that differs from the source")

    image = db.get(WorkspaceImage, body.image_id) if body.image_id else db.get(WorkspaceImage, src.image_id)
    if not image or not image.enabled:
        raise HTTPException(status_code=404, detail="Image not found or disabled")

    url_capable = bool(image.url_env) or image.image_type == "link"
    target_url = src.target_url if url_capable else None
    if image.image_type == "link" and not target_url:
        raise HTTPException(status_code=400, detail="target_url is required for link workspaces")

    _validate_routing(db, user.id, src.use_tailscale, src.use_gluetun)

    clone = Workspace(
        user_id=user.id,
        name=name,
        workspace_type=image.image_type,
        image_id=image.id,
        target_url=target_url,
        kiosk=src.kiosk,
        kiosk_dark=src.kiosk_dark,
        kiosk_menu=src.kiosk_menu,
        use_tailscale=src.use_tailscale,
        use_gluetun=src.use_gluetun,
        ephemeral=src.ephemeral,
        lan_access=src.lan_access,
        ts_exit_node=src.ts_exit_node,
        ts_accept_routes=src.ts_accept_routes,
        ts_accept_dns=src.ts_accept_dns,
        custom_dns=src.custom_dns,
        dns_servers=src.dns_servers,
        install_packages=src.install_packages,
        proot_apps=src.proot_apps,
        appimages=src.appimages,
        allow_sudo=src.allow_sudo,
        status="creating",
    )
    db.add(clone)
    db.commit()
    db.refresh(clone)

    _audit(db, "workspace.clone", detail=f"{src.public_id}->{clone.public_id}", user=user, request=request)

    from server.docker_manager import get_docker_manager
    bg.add_task(get_docker_manager().clone_and_launch, src.id, clone.id)

    return WorkspaceOut.from_workspace(clone)


@router.get("/lan-policy", response_model=LanPolicyOut)
def lan_policy(user: CurrentUser, db: DbSession):
    """Direct-LAN egress policy for the workspace modals.

    Lets the SPA show/hide the per-workspace "Allow direct LAN access" checkbox
    and the ranges it grants. ``enabled`` is the admin master toggle; ``subnets``
    are the admin-configured CIDRs a workspace may reach over the raw bridge.
    """
    from server import settings_store

    return LanPolicyOut(
        enabled=settings_store.get_workspace_lan_access(db),
        subnets=settings_store.get_workspace_lan_subnets(db),
    )


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

    # One-time, short-lived bootstrap token for the URL — swapped for a fresh
    # stream cookie by ForwardAuth on first hit, then dead.
    token = create_stream_bootstrap_token(user.id, ws.public_id)
    host = settings.workspace_host(ws.public_id)
    return StreamAuthOut(url=f"//{host}/?__cove_t={token}")


@router.patch("/{ws_id}", response_model=WorkspaceOut)
def update_workspace(
    ws_id: int, body: WorkspaceUpdate, user: CurrentUser, db: DbSession, request: Request
):
    ws = _get_workspace_or_404(ws_id, user, db)
    data = body.model_dump(exclude_unset=True)

    if data.get("target_url"):
        _validate_target_url(data["target_url"])
    _validate_app_fields(
        data.get("install_packages"), data.get("proot_apps"), data.get("appimages")
    )
    # Validate the effective routing choice (incoming value, else current).
    _validate_routing(
        db,
        ws.user_id,
        data.get("use_tailscale", ws.use_tailscale),
        data.get("use_gluetun", ws.use_gluetun),
    )

    nullable_text = {"target_url", "ts_exit_node", "install_packages", "proot_apps", "appimages", "dns_servers"}
    for key, value in data.items():
        if key == "name" and not (value or "").strip():
            continue  # never blank the name
        if key == "dns_servers":
            value = _clean_dns(value)
        elif key in nullable_text and isinstance(value, str) and value.strip() == "":
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
    # Allow recovery from "error" too — re-launching recreates the container and
    # reuses the persistent home. Only block states that are already in flight.
    if ws.status not in ("stopped", "error"):
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
def delete_workspace(
    ws_id: int,
    user: CurrentUser,
    db: DbSession,
    bg: BackgroundTasks,
    request: Request,
    purge_storage: bool = False,
):
    ws = _get_workspace_or_404(ws_id, user, db)
    public_id = ws.public_id
    _audit(
        db,
        "workspace.delete",
        detail=f"{public_id} purge_storage={purge_storage}",
        user=user,
        request=request,
    )
    from server.docker_manager import delete_workspace_storage, get_docker_manager

    if ws.status in ("running", "creating", "stopping"):
        bg.add_task(get_docker_manager().remove_workspace, ws.id, purge_storage)
    else:
        if purge_storage:
            delete_workspace_storage(ws)
        db.delete(ws)
        db.commit()
