"""Global application settings stored in the `app_setting` table.

These are runtime-configurable settings (managed by admins via the API),
distinct from the environment-driven `server.config.Settings`.
"""

import ipaddress
import re
from typing import Optional

from sqlalchemy.orm import Session

from server.models import AppSetting

# Setting keys.
KEY_TAILSCALE_IMAGE = "tailscale_image"
KEY_GLUETUN_IMAGE = "gluetun_image"
KEY_WORKSPACE_LAN_ACCESS = "workspace_lan_access"
KEY_WORKSPACE_LAN_SUBNETS = "workspace_lan_subnets"
KEY_WORKSPACE_NO_NEW_PRIVILEGES = "workspace_no_new_privileges"
KEY_WORKSPACE_MAX_RUNTIME_HOURS = "workspace_max_runtime_hours"
KEY_WORKSPACE_CPU_LIMIT = "workspace_cpu_limit"
KEY_WORKSPACE_MEMORY_LIMIT_MB = "workspace_memory_limit_mb"
KEY_WORKSPACE_PIDS_LIMIT = "workspace_pids_limit"
# GPU acceleration master toggle + the host's DRI render node and its group id.
# Effective only where the host actually has that device; the render GID varies
# per host (992 on Debian, often 44/993 elsewhere) so it is admin-configurable.
KEY_WORKSPACE_GPU_ACCEL = "workspace_gpu_accel"
KEY_WORKSPACE_GPU_RENDER_NODE = "workspace_gpu_render_node"
KEY_WORKSPACE_GPU_RENDER_GID = "workspace_gpu_render_gid"
# Docker-in-Docker master toggle + the dind image the per-workspace sidecar runs.
KEY_WORKSPACE_DOCKER = "workspace_docker"
KEY_DIND_IMAGE = "dind_image"
# How long soft-deleted files stay in the file-browser trash before the periodic
# purge sweep removes them for good (0 = keep until manually emptied).
KEY_TRASH_RETENTION_DAYS = "trash_retention_days"
# Workspace caps every account gets unless an admin overrides them per account
# (server.workspace_limits). 0 = no limit.
KEY_DEFAULT_MAX_WORKSPACES = "default_max_workspaces"
KEY_DEFAULT_MAX_RUNNING = "default_max_running"
# LAN-only sign-in (server.lan_access): the networks that count as "the local
# network", and the proxies in front of Cove's Traefik whose X-Forwarded-For
# hops are skipped to find the real client (server.net).
KEY_SIGNIN_LAN_NETWORKS = "signin_lan_networks"
KEY_TRUSTED_PROXIES = "trusted_proxies"

# Defaults.
DEFAULT_TAILSCALE_IMAGE = "tailscale/tailscale:latest"
DEFAULT_GLUETUN_IMAGE = "qmcgaw/gluetun:latest"
DEFAULT_WORKSPACE_LAN_ACCESS = False
# Comma/space separated IPv4 CIDRs a workspace may reach directly over the bridge
# when both the admin master toggle (LAN_ACCESS) and the per-workspace opt-in are
# on. Empty = nothing extra is reachable (the default — direct LAN is denied).
DEFAULT_WORKSPACE_LAN_SUBNETS = ""
# Off by default: webtop desktops expect in-container sudo, which the
# no-new-privileges flag blocks. Admins can enable it to harden.
DEFAULT_WORKSPACE_NO_NEW_PRIVILEGES = False
# Auto-stop running workspaces after this many hours (0 = unlimited).
DEFAULT_WORKSPACE_MAX_RUNTIME_HOURS = 24
# Per-workspace CPU cores (float) and memory (MB) caps. 0 = unlimited (the
# historical behaviour), so containers are uncapped until an admin sets these.
DEFAULT_WORKSPACE_CPU_LIMIT = 0.0
DEFAULT_WORKSPACE_MEMORY_LIMIT_MB = 0
# Per-container process/thread cap (a fork bomb otherwise hits the host). 0 =
# unlimited. 8192 is generous for a desktop with a busy browser.
DEFAULT_WORKSPACE_PIDS_LIMIT = 8192
# GPU acceleration off by default (most hosts have no usable render node, and a
# bad device/GID would fail every launch). When an admin enables it, workspaces
# that opt in get the render node bind-mounted with DRINODE/DRI_NODE set so the
# Selkies stream can use VAAPI hardware encode (zero-copy) instead of CPU x264.
DEFAULT_WORKSPACE_GPU_ACCEL = False
DEFAULT_WORKSPACE_GPU_RENDER_NODE = "/dev/dri/renderD128"
DEFAULT_WORKSPACE_GPU_RENDER_GID = 992
# Docker-in-Docker off by default: it runs a PRIVILEGED nested daemon (dev-grade
# isolation, not a hard multi-tenant boundary), so an admin must opt the whole
# deployment in before any workspace can enable it. The host Docker socket is
# never exposed regardless — see docker_manager._launch_dind_sidecar.
DEFAULT_WORKSPACE_DOCKER = False
DEFAULT_DIND_IMAGE = "docker:dind"
# Keep trashed files for 30 days by default, then auto-purge.
DEFAULT_TRASH_RETENTION_DAYS = 30
# No caps by default: existing deployments keep behaving as before.
DEFAULT_DEFAULT_MAX_WORKSPACES = 0
DEFAULT_DEFAULT_MAX_RUNNING = 0
# Private ranges a home or office LAN uses. 172.16.0.0/12 and 127.0.0.0/8 are
# left out on purpose: Docker's userland proxy can present outside clients as
# the bridge gateway (172.17.0.1). Admins whose LAN is in 172.16/12 add it.
DEFAULT_SIGNIN_LAN_NETWORKS = "10.0.0.0/8, 192.168.0.0/16, fc00::/7, fe80::/10"
DEFAULT_TRUSTED_PROXIES = ""


def get_setting(db: Session, key: str, default: Optional[str] = None) -> Optional[str]:
    row = db.get(AppSetting, key)
    return row.value if row is not None else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def _to_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def get_tailscale_image(db: Session) -> str:
    return get_setting(db, KEY_TAILSCALE_IMAGE, DEFAULT_TAILSCALE_IMAGE) or DEFAULT_TAILSCALE_IMAGE


def get_gluetun_image(db: Session) -> str:
    return get_setting(db, KEY_GLUETUN_IMAGE, DEFAULT_GLUETUN_IMAGE) or DEFAULT_GLUETUN_IMAGE


def get_workspace_lan_access(db: Session) -> bool:
    return _to_bool(get_setting(db, KEY_WORKSPACE_LAN_ACCESS), DEFAULT_WORKSPACE_LAN_ACCESS)


def get_workspace_docker(db: Session) -> bool:
    return _to_bool(get_setting(db, KEY_WORKSPACE_DOCKER), DEFAULT_WORKSPACE_DOCKER)


def get_dind_image(db: Session) -> str:
    return get_setting(db, KEY_DIND_IMAGE, DEFAULT_DIND_IMAGE) or DEFAULT_DIND_IMAGE


def parse_lan_subnets(raw: Optional[str]) -> list[str]:
    """Validate + normalize a comma/space separated list of IPv4 CIDRs.

    Invalid or non-IPv4 entries are dropped (the egress guard is IPv4-only).
    Returns deduplicated, normalized CIDR strings (e.g. "10.12.0.0/24").
    """
    out: list[str] = []
    for part in re.split(r"[,\s]+", (raw or "").strip()):
        if not part:
            continue
        try:
            net = ipaddress.ip_network(part, strict=False)
        except ValueError:
            continue
        if net.version != 4:
            continue
        s = str(net)
        if s not in out:
            out.append(s)
    return out


def get_workspace_lan_subnets(db: Session) -> list[str]:
    return parse_lan_subnets(get_setting(db, KEY_WORKSPACE_LAN_SUBNETS, DEFAULT_WORKSPACE_LAN_SUBNETS))


def get_workspace_no_new_privileges(db: Session) -> bool:
    return _to_bool(
        get_setting(db, KEY_WORKSPACE_NO_NEW_PRIVILEGES), DEFAULT_WORKSPACE_NO_NEW_PRIVILEGES
    )


def get_workspace_max_runtime_hours(db: Session) -> int:
    raw = get_setting(db, KEY_WORKSPACE_MAX_RUNTIME_HOURS)
    if raw is None:
        return DEFAULT_WORKSPACE_MAX_RUNTIME_HOURS
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_WORKSPACE_MAX_RUNTIME_HOURS


def get_workspace_cpu_limit(db: Session) -> float:
    raw = get_setting(db, KEY_WORKSPACE_CPU_LIMIT)
    if raw is None:
        return DEFAULT_WORKSPACE_CPU_LIMIT
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return DEFAULT_WORKSPACE_CPU_LIMIT


def get_workspace_memory_limit_mb(db: Session) -> int:
    raw = get_setting(db, KEY_WORKSPACE_MEMORY_LIMIT_MB)
    if raw is None:
        return DEFAULT_WORKSPACE_MEMORY_LIMIT_MB
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_WORKSPACE_MEMORY_LIMIT_MB


def get_workspace_pids_limit(db: Session) -> int:
    raw = get_setting(db, KEY_WORKSPACE_PIDS_LIMIT)
    if raw is None:
        return DEFAULT_WORKSPACE_PIDS_LIMIT
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_WORKSPACE_PIDS_LIMIT


def get_workspace_gpu_accel(db: Session) -> bool:
    return _to_bool(get_setting(db, KEY_WORKSPACE_GPU_ACCEL), DEFAULT_WORKSPACE_GPU_ACCEL)


def get_workspace_gpu_render_node(db: Session) -> str:
    return (
        get_setting(db, KEY_WORKSPACE_GPU_RENDER_NODE, DEFAULT_WORKSPACE_GPU_RENDER_NODE)
        or DEFAULT_WORKSPACE_GPU_RENDER_NODE
    )


def get_workspace_gpu_render_gid(db: Session) -> int:
    raw = get_setting(db, KEY_WORKSPACE_GPU_RENDER_GID)
    if raw is None:
        return DEFAULT_WORKSPACE_GPU_RENDER_GID
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_WORKSPACE_GPU_RENDER_GID


# --- Per-zone resolution ----------------------------------------------------
# GPU and Docker-in-Docker depend on the host a workspace runs on. The local
# zone (id 0) uses the global settings above; a remote zone may override them
# (GPU) or must opt in (DinD). Launch, validation and the workspace modals all
# resolve through these, so they agree on what a zone offers.


def _remote_zone(db: Session, zone_id: int):
    if not zone_id:
        return None
    from server.models import Zone

    return db.get(Zone, zone_id)


def get_zone_gpu_accel(db: Session, zone_id: int) -> bool:
    zone = _remote_zone(db, zone_id)
    if zone is not None and zone.gpu_accel is not None:
        return bool(zone.gpu_accel)
    return get_workspace_gpu_accel(db)


def get_zone_gpu_render_node(db: Session, zone_id: int) -> str:
    zone = _remote_zone(db, zone_id)
    if zone is not None and zone.gpu_render_node:
        return zone.gpu_render_node
    return get_workspace_gpu_render_node(db)


def get_zone_gpu_render_gid(db: Session, zone_id: int) -> int:
    zone = _remote_zone(db, zone_id)
    if zone is not None and zone.gpu_render_gid is not None:
        return max(0, int(zone.gpu_render_gid))
    return get_workspace_gpu_render_gid(db)


def get_zone_docker(db: Session, zone_id: int) -> bool:
    """Docker-in-Docker is available on this zone: the global master toggle is
    on, and a remote zone has also been opted in by the admin. (The agent's
    own opt-in is checked separately; see ``Zone.agent_allows_docker``.)"""
    if not get_workspace_docker(db):
        return False
    if not zone_id:
        return True
    zone = _remote_zone(db, zone_id)
    return bool(zone is not None and zone.docker_enabled)


def get_trash_retention_days(db: Session) -> int:
    raw = get_setting(db, KEY_TRASH_RETENTION_DAYS)
    if raw is None:
        return DEFAULT_TRASH_RETENTION_DAYS
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_TRASH_RETENTION_DAYS


def _get_count(db: Session, key: str, default: int) -> int:
    raw = get_setting(db, key)
    if raw is None:
        return default
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return default


def get_default_max_workspaces(db: Session) -> int:
    return _get_count(db, KEY_DEFAULT_MAX_WORKSPACES, DEFAULT_DEFAULT_MAX_WORKSPACES)


def get_default_max_running(db: Session) -> int:
    return _get_count(db, KEY_DEFAULT_MAX_RUNNING, DEFAULT_DEFAULT_MAX_RUNNING)


def get_signin_lan_networks(db: Session) -> str:
    raw = get_setting(db, KEY_SIGNIN_LAN_NETWORKS)
    return DEFAULT_SIGNIN_LAN_NETWORKS if raw is None else raw


def get_trusted_proxies(db: Session) -> str:
    raw = get_setting(db, KEY_TRUSTED_PROXIES)
    return DEFAULT_TRUSTED_PROXIES if raw is None else raw


def get_all(db: Session) -> dict:
    return {
        "tailscale_image": get_tailscale_image(db),
        "gluetun_image": get_gluetun_image(db),
        "workspace_lan_access": get_workspace_lan_access(db),
        "workspace_lan_subnets": ", ".join(get_workspace_lan_subnets(db)),
        "workspace_no_new_privileges": get_workspace_no_new_privileges(db),
        "workspace_max_runtime_hours": get_workspace_max_runtime_hours(db),
        "workspace_cpu_limit": get_workspace_cpu_limit(db),
        "workspace_memory_limit_mb": get_workspace_memory_limit_mb(db),
        "workspace_pids_limit": get_workspace_pids_limit(db),
        "workspace_gpu_accel": get_workspace_gpu_accel(db),
        "workspace_gpu_render_node": get_workspace_gpu_render_node(db),
        "workspace_gpu_render_gid": get_workspace_gpu_render_gid(db),
        "workspace_docker": get_workspace_docker(db),
        "dind_image": get_dind_image(db),
        "trash_retention_days": get_trash_retention_days(db),
        "default_max_workspaces": get_default_max_workspaces(db),
        "default_max_running": get_default_max_running(db),
        "signin_lan_networks": get_signin_lan_networks(db),
        "trusted_proxies": get_trusted_proxies(db),
    }
