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
# GPU acceleration master toggle + the host's DRI render node and its group id.
# Effective only where the host actually has that device; the render GID varies
# per host (992 on Debian, often 44/993 elsewhere) so it is admin-configurable.
KEY_WORKSPACE_GPU_ACCEL = "workspace_gpu_accel"
KEY_WORKSPACE_GPU_RENDER_NODE = "workspace_gpu_render_node"
KEY_WORKSPACE_GPU_RENDER_GID = "workspace_gpu_render_gid"
# Docker-in-Docker master toggle + the dind image the per-workspace sidecar runs.
KEY_WORKSPACE_DOCKER = "workspace_docker"
KEY_DIND_IMAGE = "dind_image"

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
        "workspace_gpu_accel": get_workspace_gpu_accel(db),
        "workspace_gpu_render_node": get_workspace_gpu_render_node(db),
        "workspace_gpu_render_gid": get_workspace_gpu_render_gid(db),
        "workspace_docker": get_workspace_docker(db),
        "dind_image": get_dind_image(db),
    }
