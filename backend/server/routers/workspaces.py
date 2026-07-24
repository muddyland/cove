import base64
import ipaddress
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import func, select

from server.config import get_settings
from server.deps import CurrentUser, DbSession
from server.models import UserGluetun, UserTailscale, Workspace, WorkspaceImage, Zone
from server.net import client_ip
from server.schemas import (
    ContainerLogsOut,
    DockerPolicyOut,
    GpuPolicyOut,
    LanPolicyOut,
    StreamAuthOut,
    StreamReadyOut,
    TailscaleStatusOut,
    WorkspaceClone,
    WorkspaceCreate,
    WorkspaceMigrate,
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


# A browser workspace can open several URLs (one tab each). Capped to keep the
# command line + resource use sane.
_MAX_TARGET_URLS = 6


def _normalize_target_urls(raw: str, *, link: bool = False) -> str:
    """Split a (possibly multi-line) target_url into validated URLs and return the
    canonical newline-joined form. Each URL is validated individually, so the
    space-joined browser CLI can't be injected with extra args."""
    urls = [u for u in re.split(r"\s+", raw.strip()) if u]
    if not urls:
        return ""
    if link and len(urls) > 1:
        raise HTTPException(status_code=400, detail="Link workspaces support a single URL")
    if len(urls) > _MAX_TARGET_URLS:
        raise HTTPException(status_code=400, detail=f"At most {_MAX_TARGET_URLS} URLs are allowed")
    for u in urls:
        _validate_target_url(u)
    return "\n".join(urls)


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


def _validate_docker(db, use_docker: bool, zone_id: int) -> None:
    """Reject Docker-in-Docker unless the admin master toggle is on and the
    workspace runs on the local zone.

    DinD runs a privileged nested daemon, so it is gated at the deployment level
    (a per-workspace opt-in is only honoured when an admin enables the feature).
    It is also restricted to the local control-plane zone: remote zone agents
    refuse privileged container creates (server.docker_policy) to keep their
    host-escape boundary intact, so the DinD sidecar cannot run there.
    """
    from server import settings_store

    if not use_docker:
        return
    if not settings_store.get_workspace_docker(db):
        raise HTTPException(
            status_code=400,
            detail="Docker-in-Docker is disabled by the administrator",
        )
    if zone_id != 0:
        raise HTTPException(
            status_code=400,
            detail="Docker-in-Docker is only available on the local zone",
        )


def _validate_gpu(gpu_accel: bool, pixelflux_wayland: bool) -> None:
    """GPU hardware (VAAPI) encode requires the Wayland stream. Reject the combo
    that would silently fall back to software encode — a stuttering stream that
    looks like a GPU failure — so the user gets a clear message up front."""
    if gpu_accel and not pixelflux_wayland:
        raise HTTPException(
            status_code=400,
            detail="GPU acceleration requires Wayland streaming (hardware encode "
            "needs it). Enable Wayland streaming, or turn off GPU acceleration.",
        )


def _validate_zone(db, zone_id: int) -> None:
    """Ensure the target zone exists and is enrolled (ready to run workspaces).

    zone_id 0 is the always-present local zone. Any other id must reference an
    enrolled remote zone, else the launch would fail with no reachable daemon.
    """
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="Zone not found")
    if zone.status != "enrolled":
        raise HTTPException(
            status_code=409, detail=f"Zone '{zone.name}' is not ready (status: {zone.status})"
        )


def _check_gluetun_single_connection(db, user_id: int, exclude_ws_id: int | None = None) -> None:
    """A Gluetun VPN config typically allows only one simultaneous connection, so
    permit at most one active Gluetun workspace per user at a time."""
    q = select(func.count()).select_from(Workspace).where(
        Workspace.user_id == user_id,
        Workspace.use_gluetun.is_(True),
        Workspace.status.in_(("running", "creating")),
    )
    if exclude_ws_id is not None:
        q = q.where(Workspace.id != exclude_ws_id)
    if db.scalar(q):
        raise HTTPException(
            status_code=409,
            detail="A Gluetun VPN workspace is already active — only one connection is "
            "allowed at a time. Stop it first.",
        )


def _check_name_unique(db, user_id: int, name: str, *, exclude_ws_id: int | None = None) -> None:
    """Reject a name that collides with another of the user's workspaces once
    sanitized. The sanitized name is the on-disk storage key, so two workspaces
    reducing to the same key (e.g. "Brave" and "brave!") would silently share the
    same ``/config`` and corrupt each other."""
    from server.docker_manager import _sanitize

    key = _sanitize(name)
    others = db.scalars(select(Workspace).where(Workspace.user_id == user_id)).all()
    for other in others:
        if other.id == exclude_ws_id:
            continue
        if _sanitize(other.name) == key:
            raise HTTPException(
                status_code=409,
                detail=f"You already have a workspace named “{other.name}” — choose a distinct name.",
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
        target_url = _normalize_target_urls(target_url, link=image.image_type == "link") or None
    if image.image_type == "link" and not target_url:
        raise HTTPException(status_code=400, detail="target_url is required for link workspaces")

    _validate_app_fields(body.install_packages, body.proot_apps, body.appimages)

    _validate_routing(db, user.id, body.use_tailscale, body.use_gluetun)
    if body.use_gluetun:
        _check_gluetun_single_connection(db, user.id)
    _validate_docker(db, body.use_docker, body.zone_id)
    _validate_gpu(body.gpu_accel, body.pixelflux_wayland)

    _validate_zone(db, body.zone_id)
    _check_name_unique(db, user.id, body.name)

    ws = Workspace(
        user_id=user.id,
        name=body.name,
        zone_id=body.zone_id,
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
        inject_ssh_key=body.inject_ssh_key,
        pixelflux_wayland=body.pixelflux_wayland,
        clear_browser_lock=body.clear_browser_lock,
        gpu_accel=body.gpu_accel,
        use_docker=body.use_docker,
        status="creating",
        status_changed_at=datetime.now(timezone.utc),
    )
    db.add(ws)
    db.commit()
    db.refresh(ws)

    _audit(db, "workspace.launch", detail=ws.public_id, user=user, request=request)

    from server.docker_manager import get_docker_manager
    bg.add_task(get_docker_manager(ws.zone_id).launch_workspace, ws.id)

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

    ts_running = [ws for ws in running if ws.use_tailscale]

    raw: dict[int, dict] = {}
    ts_ips: dict[int, str] = {}
    workers = min(8, len(running) + len(ts_running))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        # Each workspace's stats come from its own zone's daemon.
        stat_futs = {
            ex.submit(get_docker_manager(ws.zone_id).get_stats, ws.container_id): ws
            for ws in running
        }
        ip_futs = {
            ex.submit(get_docker_manager(ws.zone_id).get_tailscale_ip, ws.id): ws
            for ws in ts_running
        }
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
    _check_name_unique(db, user.id, name)

    image = db.get(WorkspaceImage, body.image_id) if body.image_id else db.get(WorkspaceImage, src.image_id)
    if not image or not image.enabled:
        raise HTTPException(status_code=404, detail="Image not found or disabled")

    url_capable = bool(image.url_env) or image.image_type == "link"
    target_url = src.target_url if url_capable else None
    if image.image_type == "link" and not target_url:
        raise HTTPException(status_code=400, detail="target_url is required for link workspaces")

    _validate_routing(db, user.id, src.use_tailscale, src.use_gluetun)
    if src.use_gluetun:
        _check_gluetun_single_connection(db, user.id)
    _validate_docker(db, src.use_docker, src.zone_id)

    clone = Workspace(
        user_id=user.id,
        name=name,
        zone_id=src.zone_id,
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
        inject_ssh_key=src.inject_ssh_key,
        pixelflux_wayland=src.pixelflux_wayland,
        clear_browser_lock=src.clear_browser_lock,
        gpu_accel=src.gpu_accel,
        use_docker=src.use_docker,
        status="creating",
        status_changed_at=datetime.now(timezone.utc),
    )
    db.add(clone)
    db.commit()
    db.refresh(clone)

    _audit(db, "workspace.clone", detail=f"{src.public_id}->{clone.public_id}", user=user, request=request)

    from server.docker_manager import get_docker_manager
    bg.add_task(get_docker_manager(clone.zone_id).clone_and_launch, src.id, clone.id)

    return WorkspaceOut.from_workspace(clone)


@router.post("/{ws_id}/migrate", response_model=WorkspaceOut)
def migrate_workspace(
    ws_id: int, body: WorkspaceMigrate, user: CurrentUser, db: DbSession,
    bg: BackgroundTasks, request: Request,
):
    """Move a stopped workspace to another zone, copying its persistent home.

    The source must be stopped so its files are at rest. The workspace's /config
    is copied to the destination zone, the zone pin flips, and the source copy is
    removed (copy-then-delete). It is left stopped on the destination.
    """
    ws = _get_workspace_or_404(ws_id, user, db)
    target = body.target_zone_id
    if target == ws.zone_id:
        raise HTTPException(status_code=400, detail="Workspace is already on that zone")
    if ws.ephemeral:
        raise HTTPException(status_code=400, detail="Ephemeral workspaces have no storage to migrate")
    if ws.status not in ("stopped", "error"):
        raise HTTPException(
            status_code=409, detail="Stop the workspace before migrating so its files are at rest"
        )
    _validate_zone(db, target)

    src = ws.zone_id
    ws.status = "migrating"
    ws.status_changed_at = datetime.now(timezone.utc)
    ws.error_message = None
    db.commit()
    db.refresh(ws)

    _audit(
        db, "workspace.migrate", detail=f"{ws.public_id}:{src}->{target}", user=user, request=request
    )

    from server.migration import run_migration
    bg.add_task(run_migration, ws.id, src, target)

    return WorkspaceOut.from_workspace(ws)


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


@router.get("/gpu-policy", response_model=GpuPolicyOut)
def gpu_policy(user: CurrentUser, db: DbSession):
    """GPU acceleration policy for the workspace modals.

    Lets the SPA show/hide the per-workspace "GPU acceleration" checkbox.
    ``enabled`` is the admin master toggle; when off, opting a workspace in has
    no effect (and a non-GPU host never gets a failing device mount).
    """
    from server import settings_store

    return GpuPolicyOut(enabled=settings_store.get_workspace_gpu_accel(db))


@router.get("/docker-policy", response_model=DockerPolicyOut)
def docker_policy(user: CurrentUser, db: DbSession):
    """Docker-in-Docker policy for the workspace modals.

    Lets the SPA show/hide the per-workspace "Docker (dev)" checkbox. ``enabled``
    is the admin master toggle; when off, opting a workspace in is rejected (the
    feature runs a privileged nested daemon, so it is deployment-gated).
    """
    from server import settings_store

    return DockerPolicyOut(enabled=settings_store.get_workspace_docker(db))


# In-memory cache of fetched project logos (url -> (bytes, content_type)). Logos
# are small, immutable, and shared across users, so a process-lifetime cache is
# plenty — it just spares the upstream CDN a fetch per manifest request.
_LOGO_CACHE: dict[str, "tuple[bytes, str] | None"] = {}
_LOGO_MAX_BYTES = 512 * 1024


async def _fetch_logo(url: str) -> "tuple[bytes, str] | None":
    """Fetch a project logo, returning (bytes, content_type) or None. Cached."""
    if url in _LOGO_CACHE:
        return _LOGO_CACHE[url]
    result: "tuple[bytes, str] | None" = None
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            r = await client.get(url)
        ctype = (r.headers.get("content-type") or "").split(";")[0].strip()
        if r.status_code == 200 and r.content and ctype.startswith("image/"):
            if len(r.content) <= _LOGO_MAX_BYTES:
                result = (r.content, ctype or "image/png")
    except (httpx.HTTPError, ValueError):
        result = None
    if len(_LOGO_CACHE) < 256:
        _LOGO_CACHE[url] = result
    return result


def _png_size(data: bytes) -> "str | None":
    """Pixel size of a PNG ("WxH") read straight from the IHDR header, or None.

    Lets the manifest declare the logo's real dimensions (Chrome's installability
    check verifies icon size) without pulling in an image library.
    """
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        w = int.from_bytes(data[16:20], "big")
        h = int.from_bytes(data[20:24], "big")
        if w and h:
            return f"{w}x{h}"
    return None


@router.get("/{ws_id}/manifest.webmanifest")
async def workspace_manifest(ws_id: int, user: CurrentUser, db: DbSession):
    """A per-workspace PWA manifest so each workspace installs as its own app.

    Name = the workspace name, icon = the image's project logo, start_url = this
    workspace's stream. Installing from the workspace page (the SPA swaps in this
    manifest) yields a home-screen app that launches straight into the node and
    looks like the app it runs (e.g. a "Brave" icon that opens the Brave node).
    """
    ws = _get_workspace_or_404(ws_id, user, db)
    name = ws.name or "Workspace"

    icons: list[dict] = []
    img = ws.image
    if img and img.icon_png:
        # Primary: the baked icon — the project logo with a Cove watermark,
        # composited at catalog-sync time (server.icons). A real raster PNG, so
        # it installs everywhere.
        b64 = base64.b64encode(img.icon_png).decode("ascii")
        icons.append(
            {"src": f"data:image/png;base64,{b64}", "sizes": "512x512", "type": "image/png", "purpose": "any"}
        )
    else:
        # Not baked yet (e.g. sync hasn't run, or a logo Pillow couldn't decode).
        # Fall back to the plain logo, then to the bundled Cove icon.
        logo = await _fetch_logo(img.logo_url) if img and img.logo_url else None
        if logo:
            data, ctype = logo
            b64 = base64.b64encode(data).decode("ascii")
            sizes = (_png_size(data) if ctype == "image/png" else None) or "any"
            icons.append(
                {"src": f"data:{ctype};base64,{b64}", "sizes": sizes, "type": ctype, "purpose": "any"}
            )
        else:
            icons.append({"src": "/pwa-192x192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"})
            icons.append({"src": "/pwa-512x512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"})
            icons.append(
                {"src": "/pwa-maskable-512x512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}
            )

    manifest = {
        "id": f"/workspace/{ws.id}",
        "name": name,
        "short_name": name[:12],
        "description": f"{ws.image.name if ws.image else 'Workspace'} on Cove",
        "start_url": f"/workspace/{ws.id}",
        # Scope this workspace's own subtree (not "/") so it doesn't overlap the
        # dashboard app (/app) or other workspace apps — overlapping scopes stop
        # the browser from installing more than one PWA per origin.
        "scope": f"/workspace/{ws.id}",
        "display": "standalone",
        "orientation": "any",
        "theme_color": "#06060f",
        "background_color": "#06060f",
        "icons": icons,
    }
    # no-store: the manifest is per-workspace and cheap to regenerate; this avoids
    # a stale name/icon sticking after a rename.
    return Response(
        content=json.dumps(manifest),
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{ws_id}", response_model=WorkspaceOut)
def get_workspace(ws_id: int, user: CurrentUser, db: DbSession):
    ws = _get_workspace_or_404(ws_id, user, db)
    return WorkspaceOut.from_workspace(ws)


@router.get("/{ws_id}/tailscale-status", response_model=TailscaleStatusOut)
def tailscale_status(ws_id: int, user: CurrentUser, db: DbSession):
    """On-demand ``tailscale status`` from the workspace's Tailscale sidecar."""
    ws = _get_workspace_or_404(ws_id, user, db)
    if not ws.use_tailscale:
        raise HTTPException(status_code=400, detail="Workspace is not using Tailscale")
    from server.docker_manager import get_docker_manager

    out = get_docker_manager(ws.zone_id).tailscale_status(ws.id)
    return TailscaleStatusOut(available=out is not None, output=out or "")


_LOG_SOURCES = {"desktop", "tailscale", "gluetun"}


@router.get("/{ws_id}/logs", response_model=ContainerLogsOut)
def container_logs(
    ws_id: int,
    user: CurrentUser,
    db: DbSession,
    source: str = "desktop",
    tail: int = 200,
):
    """On-demand container logs for the desktop, VPN (gluetun), or Tailscale sidecar."""
    ws = _get_workspace_or_404(ws_id, user, db)
    if source not in _LOG_SOURCES:
        raise HTTPException(status_code=400, detail="Unknown log source")
    if source == "tailscale" and not ws.use_tailscale:
        raise HTTPException(status_code=400, detail="Workspace is not using Tailscale")
    if source == "gluetun" and not ws.use_gluetun:
        raise HTTPException(status_code=400, detail="Workspace is not using a VPN")
    tail = max(1, min(tail, 2000))
    from server.docker_manager import get_docker_manager

    out = get_docker_manager(ws.zone_id).container_logs(ws, source, tail)
    return ContainerLogsOut(source=source, available=out is not None, output=out or "")


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


@router.get("/{ws_id}/stream-ready", response_model=StreamReadyOut)
async def stream_ready(ws_id: int, user: CurrentUser, db: DbSession):
    """Report whether Traefik can already route to this workspace's stream.

    After a workspace flips to ``running`` there is a window before Traefik has a
    router for it — effectively instant for local workspaces (Docker provider),
    up to one HTTP-provider poll for remote-zone ones. During that window the
    stream URL returns Traefik's bare 404 (nothing matched, so the ``cove-errors``
    502-504 page can't apply). The SPA polls this so it only loads the iframe once
    the route is live, instead of showing the user a raw 404.

    We probe Traefik itself with the workspace's routing key. A **404** means no
    router matches yet; anything else (401 from ForwardAuth, 200, 502, …) means the
    workspace route exists. In subpath mode an un-published route doesn't 404 — it
    falls through to the control-plane catch-all (``PathPrefix('/')``), whose
    responses carry ``X-Cove``, so that header also counts as not-ready. The
    container is confirmed answering before ``running``, so route propagation is
    the only thing left to wait on.
    """
    ws = _get_workspace_or_404(ws_id, user, db)
    if ws.status != "running":
        return StreamReadyOut(ready=False)

    settings = get_settings()
    traefik = settings.traefik_container
    if settings.workspace_domain:
        # Subdomain mode ships with an HTTP->HTTPS redirect on :80, which would
        # answer every request (masking the 404), so probe the TLS entrypoint and
        # route by Host header. The cert is Traefik's default here (SNI is the
        # container name, not the workspace host) — verification is irrelevant to
        # the routing check, so skip it.
        url = f"https://{traefik}/"
        headers = {"host": settings.workspace_host(ws.public_id)}
        verify = False
    else:
        url = f"http://{traefik}/workspace/{ws.public_id}/"
        headers = {}
        verify = True

    try:
        async with httpx.AsyncClient(
            timeout=4.0, verify=verify, follow_redirects=False
        ) as client:
            r = await client.get(url, headers=headers)
        ready = r.status_code != 404 and "x-cove" not in r.headers
        return StreamReadyOut(ready=ready)
    except httpx.HTTPError:
        # Traefik unreachable / probe timed out — treat as not-ready; the SPA
        # keeps polling.
        return StreamReadyOut(ready=False)


@router.patch("/{ws_id}", response_model=WorkspaceOut)
def update_workspace(
    ws_id: int, body: WorkspaceUpdate, user: CurrentUser, db: DbSession, request: Request
):
    ws = _get_workspace_or_404(ws_id, user, db)
    # Config changes only take effect on the next launch, and applying them to a
    # live container leaves it inconsistent with its record — e.g. enabling
    # Tailscale on a running node has no sidecar/network, so the status monitor
    # then flags a missing sidecar and flips it to "error". Require the workspace
    # to be at rest so the edit and the container can't diverge.
    if ws.status not in ("stopped", "error"):
        raise HTTPException(
            status_code=409,
            detail="Stop the workspace before editing — changes apply on the next start.",
        )
    data = body.model_dump(exclude_unset=True)

    if data.get("name") and data["name"].strip():
        _check_name_unique(db, ws.user_id, data["name"], exclude_ws_id=ws.id)

    if data.get("target_url"):
        data["target_url"] = _normalize_target_urls(
            data["target_url"], link=ws.workspace_type == "link"
        )
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
    _validate_docker(db, data.get("use_docker", ws.use_docker), ws.zone_id)
    _validate_gpu(
        data.get("gpu_accel", ws.gpu_accel),
        data.get("pixelflux_wayland", ws.pixelflux_wayland),
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
    ws.status_changed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(ws)
    _audit(db, "workspace.stop", detail=ws.public_id, user=user, request=request)
    from server.docker_manager import get_docker_manager
    bg.add_task(get_docker_manager(ws.zone_id).stop_workspace, ws.id)
    return WorkspaceOut.from_workspace(ws)


@router.post("/{ws_id}/start", response_model=WorkspaceOut)
def start_workspace(ws_id: int, user: CurrentUser, db: DbSession, bg: BackgroundTasks, request: Request):
    ws = _get_workspace_or_404(ws_id, user, db)
    # Allow recovery from "error" too — re-launching recreates the container and
    # reuses the persistent home. Only block states that are already in flight.
    if ws.status not in ("stopped", "error"):
        raise HTTPException(status_code=400, detail=f"Cannot start workspace in state: {ws.status}")
    if ws.use_gluetun:
        _check_gluetun_single_connection(db, user.id, exclude_ws_id=ws.id)
    ws.status = "creating"
    ws.status_changed_at = datetime.now(timezone.utc)
    ws.error_message = None
    db.commit()
    db.refresh(ws)
    _audit(db, "workspace.start", detail=ws.public_id, user=user, request=request)
    from server.docker_manager import get_docker_manager
    bg.add_task(get_docker_manager(ws.zone_id).launch_workspace, ws.id)
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
    # A migration is actively streaming this workspace's home between zones and
    # will commit back to this row when it finishes. Deleting now (and, with
    # purge, rmtree-ing the source dir mid-copy) corrupts the destination and
    # leaves the migration task writing to a deleted row. Make the user wait it
    # out — a migrating workspace's storage is intact on both ends until it's done.
    if ws.status == "migrating":
        raise HTTPException(
            status_code=409, detail="Workspace is migrating — wait for it to finish before purging."
        )
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
        bg.add_task(get_docker_manager(ws.zone_id).remove_workspace, ws.id, purge_storage)
    else:
        if purge_storage:
            delete_workspace_storage(ws)
        db.delete(ws)
        db.commit()
