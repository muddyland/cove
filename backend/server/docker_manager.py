import ipaddress
import logging
import os
import re
import shutil
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

import docker
import docker.errors
import docker.tls
from sqlalchemy import select

from server.config import get_settings
from server.db import SessionLocal
from server.models import UserGluetun, UserTailscale, Workspace, WorkspaceImage, Zone
from server.security import decrypt_secret
from server.settings_store import (
    get_dind_image,
    get_gluetun_image,
    get_tailscale_image,
    get_workspace_cpu_limit,
    get_workspace_docker,
    get_workspace_gpu_accel,
    get_workspace_gpu_render_gid,
    get_workspace_gpu_render_node,
    get_workspace_lan_access,
    get_workspace_lan_subnets,
    get_workspace_max_runtime_hours,
    get_workspace_memory_limit_mb,
    get_workspace_no_new_privileges,
)

logger = logging.getLogger(__name__)

# Where the helper scripts live inside the cove container (baked by the Dockerfile
# / bind-mounted from the host checkout).
_SCRIPTS_SRC_DIR = "/app/scripts"
_HELPER_SCRIPTS = (
    "install-proot-apps.sh",
    "install-appimages.sh",
    "launch-url.sh",
    "install-ssh-key.sh",
    "install-username.sh",
    "install-cove-theme.sh",
    "clear-browser-lock.sh",
    "fix-mate-xsettings.sh",
    "install-docker-cli.sh",
)


def _stage_helper_scripts() -> "Path":
    """Stage helper scripts into the storage tree and return that directory.

    Workspace bind-mount *sources* are resolved by the Docker daemon on the HOST,
    where the cove container's ``/app/scripts`` does not exist (the daemon would
    silently create an empty directory there and mount that instead of the script).
    The storage root, by contrast, is bind-mounted at an identical path on host
    and container, so files staged under it resolve correctly on the host.
    """
    settings = get_settings()
    base = settings.storage_path or (settings.data_dir / "workspaces")
    dest = Path(base) / ".cove-scripts"
    dest.mkdir(parents=True, exist_ok=True)
    for name in _HELPER_SCRIPTS:
        src = Path(_SCRIPTS_SRC_DIR) / name
        if src.exists():
            target = dest / name
            # A previous launch may have left an empty DIRECTORY here: the Docker
            # daemon auto-creates a bind-mount source that doesn't exist yet, which
            # happens on a remote agent that hadn't staged the scripts. copyfile
            # can't overwrite a directory, so clear it before writing the script.
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            shutil.copyfile(src, target)
            os.chmod(target, 0o755)
    return dest


def _helper_script_path(name: str) -> str:
    """Host-resolvable bind-mount source for a staged helper script."""
    return str(_stage_helper_scripts() / name)


def _build_browser_cli(ws) -> str:
    """Assemble the browser CLI string (the *_CLI env value) for a URL workspace.

    ``target_url`` may hold several whitespace/newline-separated URLs; the browser
    opens each in its own tab. Each URL was validated at the API boundary
    (http/https + host, no whitespace/control chars), so the space-joined result
    can't smuggle extra CLI args.

    Chromium/Brave flags:
      --kiosk           full-screen, locked (no context menu / shortcuts / tab bar)
      --start-fullscreen full-screen but keeps the menu + tab bar
      --force-dark-mode --enable-features=WebContentsForceDark  force dark pages
    """
    urls = (ws.target_url or "").split()
    flags = []
    if len(urls) > 1:
        # Multiple tabs need the tab bar — locked --kiosk would hide it, so we
        # never use it for multi-URL launches (use functional full-screen instead).
        flags.append("--start-fullscreen")
    elif ws.kiosk:
        flags.append("--start-fullscreen" if ws.kiosk_menu else "--kiosk")
    if ws.kiosk_dark:
        flags += ["--force-dark-mode", "--enable-features=WebContentsForceDark"]
    return " ".join([*flags, *urls])

# Helper image used to apply egress firewall rules inside a workspace netns.
# netshoot ships iptables; it runs briefly and is removed immediately.
EGRESS_GUARD_IMAGE = "nicolaka/netshoot:latest"

# Destinations blocked for EVERY workspace, even Tailscale ones and even when
# direct LAN access is granted: cloud/link-local metadata and the Docker-internal
# range where the Cove backend, socket proxies, Traefik, and OTHER workspaces
# live. Keeps container/backend isolation intact regardless of routing mode.
_ALWAYS_BLOCK = [
    "169.254.0.0/16",  # link-local / cloud metadata (169.254.169.254)
    "172.16.0.0/12",   # Docker bridge networks (backend, proxies, Traefik, peers)
]
# Remaining private + CGNAT ranges. Blocked on the raw bridge unless a workspace
# is granted direct access to a specific subnet within them. For Tailscale
# workspaces the tailnet (carried on tailscale0) is allowed first, so tailnet
# peers and subnet-router-advertised LAN inside these ranges still work.
_LAN_BLOCK = [
    "10.0.0.0/8",
    "192.168.0.0/16",
    "100.64.0.0/10",
]
# The blockable-but-allowable ranges as networks, for matching a target_url host.
# (172.16/12 + link-local are in _ALWAYS_BLOCK, never auto-allowed.)
_LAN_BLOCK_NETS = [ipaddress.ip_network(c) for c in _LAN_BLOCK]


def _one_url_lan_ips(url: str) -> list[str]:
    host = urlparse(url).hostname
    if not host:
        return []
    try:
        candidates = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            candidates = [
                ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)
            ]
        except OSError:
            return []
    out: list[str] = []
    for ip in candidates:
        if ip.version == 4 and any(ip in net for net in _LAN_BLOCK_NETS):
            cidr = f"{ip}/32"
            if cidr not in out:
                out.append(cidr)
    return out


def _target_url_lan_ips(target_url: str | None) -> list[str]:
    """Private /32s a workspace must reach to load its LAN ``target_url``(s).

    A browser workspace pointed at a LAN address won't load while the egress
    guard blocks RFC1918, so we punch through exactly the target host(s) (each as
    a /32 — narrow enough that it can't be a general-purpose LAN pivot). Only the
    allowlist-governed ranges (10/8, 192.168/16, CGNAT) qualify; the Docker-
    internal and metadata ranges stay blocked. Hostnames are resolved
    best-effort. ``target_url`` may list several URLs (multi-tab); each is checked.
    """
    if not target_url:
        return []
    out: list[str] = []
    for url in target_url.split():
        for cidr in _one_url_lan_ips(url):
            if cidr not in out:
                out.append(cidr)
    return out


def _sanitize(name: str) -> str:
    """Make a name safe for use as a directory component."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", name).strip("-").lower() or "workspace"


def _split_packages(text: str | None) -> list[str]:
    """Parse a free-text package list into a clean list of names.

    Splits on commas and any whitespace, dropping empty tokens. Returns [] for
    None/empty/whitespace-only input.
    """
    if not text:
        return []
    return [tok for tok in re.split(r"[,\s]+", text.strip()) if tok]


def build_ts_extra_args(
    *,
    exit_node: str | None,
    accept_routes: bool,
    accept_dns: bool,
    login_server: str | None,
) -> list[str]:
    """Build the ``tailscale up`` extra args from per-workspace routing options.

    exit_node/accept_routes/accept_dns come from the Workspace; login_server
    comes from the user's UserTailscale config.

    When an exit node is set we also pass ``--exit-node-allow-lan-access``: without
    it the container routes local-subnet replies through the tunnel, so Traefik
    (on the local Docker network) can't reach the workspace's stream.
    """
    extra_args: list[str] = []
    if exit_node:
        extra_args.append(f"--exit-node={exit_node}")
        extra_args.append("--exit-node-allow-lan-access")
    if accept_routes:
        extra_args.append("--accept-routes")
    extra_args.append(f"--accept-dns={'true' if accept_dns else 'false'}")
    if login_server:
        extra_args.append(f"--login-server={login_server}")
    return extra_args


def _resolve_mount(ws) -> tuple[str, bool]:
    """Return (mount_source, is_bind_mount) for a workspace's persistent /config.

    If COVE_STORAGE_PATH is set, creates {path}/{username}/workspace-{name}/ on
    the host and returns it as a bind mount source (same absolute path inside the
    container — the user must mount it at the same path in docker-compose.yml).
    If unset, falls back to a path inside /app/data (already mounted) so no extra
    volume entry is needed.

    Once a workspace has a recorded ``volume_name`` we reuse it verbatim instead
    of re-deriving from the (mutable) name — otherwise a rename would point the
    next launch at a fresh empty dir and strand the existing home under the old
    name. Only a volume_name that stays within the storage base is honored, as
    defense-in-depth against a tampered value.
    """
    settings = get_settings()
    base = settings.storage_path or (settings.data_dir / "workspaces")
    base_r = base.resolve()

    if ws.volume_name:
        pinned = Path(ws.volume_name)
        try:
            pinned_r = pinned.resolve()
        except OSError:
            pinned_r = None
        if pinned_r is not None and pinned_r != base_r and base_r in pinned_r.parents:
            pinned.mkdir(parents=True, exist_ok=True)
            return str(pinned), True

    host_path = base / ws.user.username / f"workspace-{_sanitize(ws.name)}"
    host_path.mkdir(parents=True, exist_ok=True)
    return str(host_path), True


def delete_workspace_storage(ws) -> None:
    """Best-effort removal of a workspace's persistent home directory.

    Only deletes a path strictly *under* the configured storage base, so a
    crafted name (or a tampered ``volume_name``) can't escape it. No-op when the
    directory doesn't exist (e.g. a workspace that was never launched).
    """
    settings = get_settings()
    base = (settings.storage_path or (settings.data_dir / "workspaces")).resolve()

    # Prefer the recorded mount source; fall back to the derived path.
    if ws.volume_name:
        candidate = Path(ws.volume_name)
    elif ws.user and ws.user.username:
        candidate = base / ws.user.username / f"workspace-{_sanitize(ws.name)}"
    else:
        return

    try:
        candidate = candidate.resolve()
    except OSError:
        return

    if candidate == base or base not in candidate.parents:
        logger.warning("Refusing to purge storage outside base: %s", candidate)
        return
    if candidate.is_dir():
        shutil.rmtree(candidate, ignore_errors=True)
        logger.info("Purged workspace storage %s", candidate)


def copy_workspace_storage(src_ws, dst_ws) -> None:
    """Copy a workspace's persistent home (/config) into the clone's home.

    Both paths are derived fresh from the storage base + username +
    sanitized name (so neither can escape the base), and the copy is refused if
    it would target the source itself. No-op when the source was never launched
    (no dir yet) — the clone then just starts with a fresh home. Symlinks are
    preserved as-is. Ownership is left to the container's init, which chowns
    /config to PUID/PGID on first boot.
    """
    settings = get_settings()
    base = (settings.storage_path or (settings.data_dir / "workspaces")).resolve()

    def _path(ws):
        # Prefer the pinned dir (the source may have been renamed since launch);
        # a never-launched clone has no volume_name and gets its name-based dir.
        if ws.volume_name:
            return Path(ws.volume_name)
        if not (ws.user and ws.user.username):
            return None
        return base / ws.user.username / f"workspace-{_sanitize(ws.name)}"

    src = _path(src_ws)
    dst = _path(dst_ws)
    if src is None or dst is None:
        return
    if not src.is_dir():
        return  # source never launched; clone starts fresh
    src_r = src.resolve()
    if src_r == base or base not in src_r.parents:
        logger.warning("Refusing to clone storage outside base: %s", src_r)
        return
    if dst == src_r:
        raise ValueError("clone destination resolves to the source storage")
    shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True, ignore_dangling_symlinks=True)
    logger.info("Cloned workspace storage %s -> %s", src_r, dst)


def _gluetun_config_path(ws_id: int) -> Path:
    """Host-resolvable path for a workspace's decrypted Gluetun config file."""
    settings = get_settings()
    base = settings.storage_path or (settings.data_dir / "workspaces")
    return Path(base) / ".cove-gluetun" / f"cove-gluetun-{ws_id}.conf"


def _stage_gluetun_config(ws_id: int, content: str) -> str:
    """Write the decrypted VPN config to a 0600 host file for mounting into the
    Gluetun sidecar; return its path. Decrypted at runtime like the Tailscale auth
    key (the host is trusted); the DB copy stays encrypted at rest."""
    path = _gluetun_config_path(ws_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    os.chmod(path, 0o600)
    return str(path)


def _remove_gluetun_config(ws_id: int) -> None:
    try:
        _gluetun_config_path(ws_id).unlink()
    except (FileNotFoundError, OSError):
        pass


def _ssh_key_dir(ws_id: int) -> Path:
    """Host-resolvable dir holding a workspace's staged SSH key files."""
    settings = get_settings()
    base = settings.storage_path or (settings.data_dir / "workspaces")
    return Path(base) / ".cove-ssh" / f"cove-ssh-{ws_id}"


def _stage_ssh_key(ws_id: int, private_key: str, public_key: str, key_type: str) -> str:
    """Write the decrypted private key (and public key) to a 0600/0644 host dir for
    read-only mounting into the workspace; return the dir path. Decrypted at runtime
    like the Gluetun config (the host is trusted); the DB copy stays encrypted."""
    from server.ssh_keys import key_filename

    d = _ssh_key_dir(ws_id)
    # Re-create clean so a changed key type doesn't leave a stale id_* file behind.
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    name = key_filename(key_type)
    priv = d / name
    priv.write_text(private_key if private_key.endswith("\n") else private_key + "\n")
    os.chmod(priv, 0o600)
    if public_key:
        pub = d / f"{name}.pub"
        pub.write_text(public_key.rstrip("\n") + "\n")
        os.chmod(pub, 0o644)
    return str(d)


def _remove_ssh_key(ws_id: int) -> None:
    shutil.rmtree(_ssh_key_dir(ws_id), ignore_errors=True)


# Public resolvers used when a workspace opts into custom DNS without naming any.
_DEFAULT_PUBLIC_DNS = ["1.1.1.1", "9.9.9.9"]


def _dns_list(ws) -> list[str] | None:
    """DNS servers to apply to a workspace, or None to use the Docker default.

    When ``custom_dns`` is set, the container resolves via the listed servers
    (falling back to public defaults if none were given) instead of the local
    network's DNS.
    """
    if not getattr(ws, "custom_dns", False):
        return None
    raw = (getattr(ws, "dns_servers", None) or "").strip()
    servers = [s for s in re.split(r"[,\s]+", raw) if s]
    return servers or list(_DEFAULT_PUBLIC_DNS)


def _resource_limits(db) -> dict:
    """Docker run kwargs for the admin-configured CPU/memory caps.

    Returns ``nano_cpus`` (CPU cores → billionths) and/or ``mem_limit`` (MB →
    "<n>m"). Empty when a limit is 0/unset, leaving the container uncapped.
    """
    limits: dict = {}
    cpus = get_workspace_cpu_limit(db)
    if cpus > 0:
        limits["nano_cpus"] = int(cpus * 1_000_000_000)
    mem_mb = get_workspace_memory_limit_mb(db)
    if mem_mb > 0:
        limits["mem_limit"] = f"{mem_mb}m"
    return limits


def _parse_stats(raw: dict) -> dict | None:
    """Reduce a Docker ``stats(stream=False)`` payload to CPU% + memory.

    Returns ``{cpu_pct, mem_used, mem_limit, mem_pct}`` or ``None`` if the
    payload is too incomplete to compute a CPU delta (e.g. container just
    started). Memory excludes page cache so the figure tracks real usage.
    """
    try:
        cpu = raw["cpu_stats"]
        precpu = raw["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - precpu["cpu_usage"]["total_usage"]
        system_delta = cpu.get("system_cpu_usage", 0) - precpu.get("system_cpu_usage", 0)
        online = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or []) or 1
        cpu_pct = (cpu_delta / system_delta) * online * 100.0 if system_delta > 0 else 0.0

        mem = raw["memory_stats"]
        usage = mem.get("usage", 0)
        # cgroup v2 reports inactive_file; v1 reports cache. Subtract whichever
        # exists so the number reflects active RSS rather than reclaimable cache.
        detail = mem.get("stats", {}) or {}
        cache = detail.get("inactive_file", detail.get("cache", 0))
        mem_used = max(usage - cache, 0)
        mem_limit = mem.get("limit", 0)
        mem_pct = (mem_used / mem_limit) * 100.0 if mem_limit > 0 else 0.0
    except (KeyError, TypeError, ZeroDivisionError):
        return None

    return {
        "cpu_pct": round(cpu_pct, 1),
        "mem_used": int(mem_used),
        "mem_limit": int(mem_limit),
        "mem_pct": round(mem_pct, 1),
    }


def _zone_cert_dir(zone_id: int) -> Path:
    settings = get_settings()
    return Path(settings.data_dir) / "zone-certs" / str(zone_id)


def _zone_has_mtls(zone) -> bool:
    """True once a zone carries the mTLS material to dial it (CA + client cert +
    encrypted client key)."""
    return bool(zone.ca_cert_pem and zone.client_cert_pem and zone.client_key_enc)


def stage_zone_certs(zone) -> tuple[str, str, str]:
    """Materialize a zone's mTLS client cert/key + CA to 0600 files and return
    ``(client_cert_path, client_key_path, ca_cert_path)``.

    docker-py's TLSConfig (and httpx, for the file/migration proxy) want file
    paths, not in-memory PEMs. The client private key is decrypted at runtime —
    the host is trusted; the DB copy stays encrypted at rest. Files are owner-only.
    """
    d = _zone_cert_dir(zone.id)
    d.mkdir(parents=True, exist_ok=True)
    ca = d / "ca.crt"
    cert = d / "client.crt"
    key = d / "client.key"
    ca.write_text(zone.ca_cert_pem)
    cert.write_text(zone.client_cert_pem)
    key.write_text(decrypt_secret(zone.client_key_enc))
    for p in (ca, cert, key):
        os.chmod(p, 0o600)
    return str(cert), str(key), str(ca)


def zone_agent_base_url(zone) -> str:
    """The base URL of a zone agent's mTLS API (served by the agent's Traefik on
    its single port, alongside the workspace streams and Docker proxy)."""
    return f"https://{zone.endpoint_host}:{zone.endpoint_port}"


def zone_agent_client(zone, **kwargs):
    """An httpx client that dials a zone agent's API with the control plane's
    per-zone mTLS client cert (verified against the zone's CA). Used by the file
    browser proxy and workspace migration relay."""
    import httpx

    cert, key, ca = stage_zone_certs(zone)
    return httpx.Client(
        base_url=zone_agent_base_url(zone), cert=(cert, key), verify=ca, **kwargs
    )


class DockerManager:
    def __init__(self, zone_id: int = 0):
        # One manager per zone (cached by get_docker_manager). zone_id 0 is the
        # local control-plane daemon; any other id is a remote agent node the
        # control plane dials. All ~30 self._client call sites below are
        # daemon-agnostic, so pointing the client at a zone's endpoint runs the
        # entire workspace lifecycle on that node unchanged.
        self.zone_id = zone_id
        # Honor DOCKER_API_VERSION so the client skips version negotiation through
        # the socket proxy (which can otherwise fall back to an API version the
        # daemon rejects). Falls back to default behavior when unset.
        api_version = os.environ.get("DOCKER_API_VERSION")
        if zone_id == 0:
            self._client = docker.from_env(version=api_version) if api_version else docker.from_env()
        else:
            self._client = self._build_zone_client(zone_id, api_version)
        self._lock = Lock()
        # Image refs with a manual pull currently in flight (for the Images UI).
        self._pulling: set[str] = set()
        self._pulling_lock = Lock()
        # Cache of detected DRI render-node group GIDs (per node path) on this
        # zone's host. The device group is stable, so we probe it once; None
        # results (probe infra failure) are not cached so they retry.
        self._render_gid_cache: dict[str, "int | str"] = {}

    def _build_zone_client(self, zone_id: int, api_version: str | None) -> "docker.DockerClient":
        """Build a Docker client pointed at a remote zone's endpoint.

        When the zone has mTLS material (CA + client cert/key, populated by
        enrollment) the connection is HTTPS with a client cert verified against
        the CA. Otherwise it falls back to plain TCP (Phase 1 / manual testing).
        """
        db = self._get_db()
        try:
            zone = db.get(Zone, zone_id)
            if zone is None or not zone.endpoint_host:
                raise RuntimeError(f"zone {zone_id} has no endpoint configured")
            host = zone.endpoint_host
            port = zone.endpoint_port
            tls = None
            if _zone_has_mtls(zone):
                cert, key, ca = stage_zone_certs(zone)
                tls = docker.tls.TLSConfig(client_cert=(cert, key), ca_cert=ca, verify=True)
        finally:
            db.close()
        scheme = "https" if tls else "tcp"
        # Bounded timeout so an unreachable/misconfigured agent fails in ~30s with
        # a clear error instead of hanging on docker-py's 60s default. Streaming
        # pulls are unaffected (the timeout is per-read; progress keeps it alive).
        kwargs: dict = {"base_url": f"{scheme}://{host}:{port}", "timeout": 30}
        if tls is not None:
            kwargs["tls"] = tls
        if api_version:
            kwargs["version"] = api_version
        return docker.DockerClient(**kwargs)

    def _get_db(self):
        return SessionLocal()

    def ping(self) -> bool:
        """Liveness check for this zone's daemon. Raises if unreachable."""
        return self._client.ping()

    def disk_usage(self) -> dict:
        """This zone's Docker disk usage, broken down like ``docker system df``.

        Reclaimable is computed the same way the CLI does: images not referenced
        by any container (minus shared layers), stopped containers' writable
        layers, unreferenced volumes, and build-cache entries not in use. Volumes
        are reported for visibility only — prune() never touches them.
        """
        d = self._client.df()
        images = d.get("Images") or []
        containers = d.get("Containers") or []
        volumes = d.get("Volumes") or []
        build = d.get("BuildCache") or []

        def _vol_size(v) -> int:
            return int((v.get("UsageData") or {}).get("Size") or 0)

        def _vol_refs(v) -> int:
            return int((v.get("UsageData") or {}).get("RefCount") or 0)

        img_reclaim = sum(
            (i.get("Size") or 0) - (i.get("SharedSize") or 0)
            for i in images
            if not (i.get("Containers") or 0) > 0
        )
        con_reclaim = sum(
            c.get("SizeRw") or 0 for c in containers if c.get("State") != "running"
        )
        bc_reclaim = sum(b.get("Size") or 0 for b in build if not b.get("InUse"))

        return {
            "categories": [
                {
                    "key": "images",
                    "label": "Images",
                    "total": len(images),
                    "active": sum(1 for i in images if (i.get("Containers") or 0) > 0),
                    "size": d.get("LayersSize") or 0,
                    "reclaimable": max(int(img_reclaim), 0),
                },
                {
                    "key": "containers",
                    "label": "Containers",
                    "total": len(containers),
                    "active": sum(1 for c in containers if c.get("State") == "running"),
                    "size": sum(c.get("SizeRw") or 0 for c in containers),
                    "reclaimable": int(con_reclaim),
                },
                {
                    "key": "volumes",
                    "label": "Local Volumes",
                    "total": len(volumes),
                    "active": sum(1 for v in volumes if _vol_refs(v) > 0),
                    "size": sum(_vol_size(v) for v in volumes),
                    "reclaimable": sum(_vol_size(v) for v in volumes if _vol_refs(v) <= 0),
                },
                {
                    "key": "build_cache",
                    "label": "Build Cache",
                    "total": len(build),
                    "active": sum(1 for b in build if b.get("InUse")),
                    "size": sum(b.get("Size") or 0 for b in build),
                    "reclaimable": int(bc_reclaim),
                },
            ],
        }

    def prune(self, deep: bool = False) -> dict:
        """Reclaim disk on this zone's daemon. Never removes volumes.

        Safe (default): dangling (untagged) images + build cache. Deep: also all
        images not referenced by a container and every stopped container — this
        forces slow re-pulls/rebuilds, so it is gated behind an explicit confirm.
        """
        reclaimed = 0
        # Images: dangling-only by default, all-unused when deep.
        img = self._client.images.prune(
            filters={"dangling": False} if deep else {"dangling": True}
        )
        reclaimed += img.get("SpaceReclaimed") or 0
        # Build cache (best-effort; older daemons may not support it).
        try:
            bc = self._client.api.prune_builds()
            reclaimed += bc.get("SpaceReclaimed") or 0
        except Exception:
            pass
        if deep:
            con = self._client.containers.prune()
            reclaimed += con.get("SpaceReclaimed") or 0
        return {"space_reclaimed": int(reclaimed)}

    def save_image_stream(self, ref: str):
        """A ``docker save`` byte stream for an image, used to provision a zone
        agent with a locally-built image that isn't in any registry."""
        return self._client.api.get_image(ref)

    def load_image(self, data) -> None:
        """``docker load`` a ``docker save`` byte stream into this zone's daemon —
        the receiving side of ``save_image_stream``, used to push an updated agent
        image to a zone over the per-zone Docker channel (no registry involved)."""
        self._client.images.load(data)

    def container_exists(self, name: str) -> bool:
        try:
            self._client.containers.get(name)
            return True
        except docker.errors.NotFound:
            return False

    def exec_detached(self, container_name: str, cmd: list[str]) -> None:
        """Fire-and-forget a command inside an existing container. Used to trigger
        an agent self-recreate from a sidecar that outlives the agent container, so
        the work continues after the proxy this call rode through is torn down."""
        container = self._client.containers.get(container_name)
        container.exec_run(cmd, detach=True)

    def _ensure_named_volume(self, volume_name: str) -> None:
        try:
            self._client.volumes.get(volume_name)
        except docker.errors.NotFound:
            self._client.volumes.create(name=volume_name)
            logger.info("Created volume %s", volume_name)

    @staticmethod
    def _split_ref(ref: str) -> tuple[str, str | None]:
        """Split an image reference into (repository, tag). A ':' only counts as a
        tag separator when it's after the final '/' (so registry:port is safe)."""
        last = ref.rsplit("/", 1)[-1]
        if ":" in last:
            repo, tag = ref.rsplit(":", 1)
            return repo, tag
        return ref, None

    def _pull_image(self, ref: str) -> None:
        """Always pull so a restarted workspace gets the latest image. Best-effort:
        if the pull fails (e.g. offline) we fall back to any local copy."""
        repo, tag = self._split_ref(ref)
        try:
            self._client.images.pull(repo, tag=tag)
            logger.info("Pulled image %s", ref)
        except docker.errors.APIError as exc:
            logger.warning("Could not pull %s (%s); using local image if present", ref, exc)

    def image_present(self, ref: str) -> bool:
        """True if the image is available locally (no registry round-trip)."""
        try:
            self._client.images.get(ref)
            return True
        except (docker.errors.ImageNotFound, docker.errors.NotFound):
            return False
        except docker.errors.APIError as exc:
            logger.debug("image_present check failed for %s: %s", ref, exc)
            return False

    def remove_image(self, ref: str) -> str:
        """Delete a local image from disk.

        Returns one of:
          'removed'  — the image was deleted
          'absent'   — nothing to do (it wasn't pulled)
          'in_use'   — a container still references it (409); left in place
          'error'    — any other daemon failure
        Never raises, so callers can map the status to an HTTP response.
        """
        try:
            self._client.images.remove(ref)
            logger.info("Removed image %s", ref)
            return "removed"
        except (docker.errors.ImageNotFound, docker.errors.NotFound):
            return "absent"
        except docker.errors.APIError as exc:
            resp = getattr(exc, "response", None)
            if resp is not None and resp.status_code == 409:
                logger.info("Image %s in use, not removed", ref)
                return "in_use"
            logger.warning("Failed to remove image %s: %s", ref, exc)
            return "error"

    def is_pulling(self, ref: str) -> bool:
        with self._pulling_lock:
            return ref in self._pulling

    def pull_status(self, ref: str) -> str:
        """One of 'pulling', 'present', or 'absent'."""
        if self.is_pulling(ref):
            return "pulling"
        return "present" if self.image_present(ref) else "absent"

    def pull_image_blocking(self, ref: str) -> bool:
        """Pull an image, tracking it as in-flight. Returns True on success.

        Intended to run as a background task behind the manual-pull endpoint; the
        ``_pulling`` set lets the status endpoint report progress meanwhile.
        """
        with self._pulling_lock:
            self._pulling.add(ref)
        try:
            repo, tag = self._split_ref(ref)
            self._client.images.pull(repo, tag=tag)
            logger.info("Manually pulled image %s", ref)
            return True
        except docker.errors.APIError as exc:
            logger.warning("Manual pull failed for %s: %s", ref, exc)
            return False
        finally:
            with self._pulling_lock:
                self._pulling.discard(ref)

    @staticmethod
    def _ws_network_name(ws_id: int) -> str:
        """Name of the per-workspace isolated network."""
        return f"cove-ws-net-{ws_id}"

    @staticmethod
    def _ts_sidecar_name(ws_id: int) -> str:
        """Name of the per-workspace Tailscale sidecar container."""
        return f"cove-ts-{ws_id}"

    @staticmethod
    def _ts_volume_name(ws_id: int) -> str:
        """Name of the per-workspace Tailscale state volume."""
        return f"cove-ts-state-{ws_id}"

    @staticmethod
    def _gluetun_sidecar_name(ws_id: int) -> str:
        """Name of the per-workspace Gluetun VPN sidecar container."""
        return f"cove-gluetun-{ws_id}"

    @staticmethod
    def _dind_sidecar_name(ws_id: int) -> str:
        """Name of the per-workspace Docker-in-Docker daemon sidecar container."""
        return f"cove-dind-{ws_id}"

    @staticmethod
    def _dind_volume_name(ws_id: int) -> str:
        """Name of the per-workspace DinD state volume (its /var/lib/docker)."""
        return f"cove-dind-state-{ws_id}"

    def _ensure_ws_network(self, network_name: str) -> None:
        """Create the per-workspace bridge network if it does not exist.

        IPv6 is explicitly disabled: the egress guard (_apply_egress_guard) only
        installs IPv4 iptables rules, so allowing IPv6 would let a workspace reach
        IPv6 link-local/ULA/internal/metadata addresses unfiltered, bypassing the
        WAN-only (LAN-block) policy.
        """
        try:
            self._client.networks.get(network_name)
        except docker.errors.NotFound:
            self._client.networks.create(network_name, driver="bridge", enable_ipv6=False)
            logger.info("Created network %s", network_name)

    def _cleanup_ws_network(self, network_name: str) -> None:
        """Disconnect Traefik from the per-workspace network and remove it.

        Only removes the network once no workspace container remains attached.
        All steps are best-effort and never raise.
        """
        settings = get_settings()
        try:
            net = self._client.networks.get(network_name)
        except (docker.errors.NotFound, docker.errors.APIError):
            return

        try:
            net.disconnect(settings.traefik_container, force=True)
        except docker.errors.APIError:
            pass

        # Only remove the network if no containers remain on it.
        try:
            net.reload()
            if net.attrs.get("Containers"):
                return
        except docker.errors.APIError:
            pass

        try:
            net.remove()
        except docker.errors.APIError:
            pass

    def _cleanup_tailscale_sidecar(self, ws_id: int) -> None:
        """Stop+remove the Tailscale sidecar and its state volume. Best-effort."""
        sidecar_name = self._ts_sidecar_name(ws_id)
        try:
            sidecar = self._client.containers.get(sidecar_name)
            try:
                sidecar.stop(timeout=10)
            except docker.errors.APIError:
                pass
            sidecar.remove(force=True)
        except (docker.errors.NotFound, docker.errors.APIError):
            pass

        try:
            vol = self._client.volumes.get(self._ts_volume_name(ws_id))
            vol.remove(force=True)
        except (docker.errors.NotFound, docker.errors.APIError):
            pass

    def _cleanup_gluetun_sidecar(self, ws_id: int) -> None:
        """Stop+remove the Gluetun sidecar and delete its staged config. Best-effort."""
        try:
            sidecar = self._client.containers.get(self._gluetun_sidecar_name(ws_id))
            try:
                sidecar.stop(timeout=10)
            except docker.errors.APIError:
                pass
            sidecar.remove(force=True)
        except (docker.errors.NotFound, docker.errors.APIError):
            pass
        _remove_gluetun_config(ws_id)

    def _cleanup_docker_sidecar(self, ws_id: int) -> None:
        """Stop+remove the DinD sidecar and its state volume. Best-effort.

        The volume holds the nested daemon's /var/lib/docker (its images/layers/
        containers); it is per-workspace and, like the container itself, is
        discarded on halt — a workspace's nested Docker state does not survive a
        stop, matching how the workspace container is removed on halt."""
        try:
            sidecar = self._client.containers.get(self._dind_sidecar_name(ws_id))
            try:
                sidecar.stop(timeout=10)
            except docker.errors.APIError:
                pass
            sidecar.remove(force=True)
        except (docker.errors.NotFound, docker.errors.APIError):
            pass

        try:
            vol = self._client.volumes.get(self._dind_volume_name(ws_id))
            vol.remove(force=True)
        except (docker.errors.NotFound, docker.errors.APIError):
            pass

    def _wait_for_ready(self, container, timeout: int = 120) -> bool:
        """Docker-API-only readiness check (no workspace network access).

        Ready when the container is "running" and, if it defines a healthcheck,
        once health is "healthy". For containers without a healthcheck, ready
        once running plus a short grace period.
        """
        deadline = time.time() + timeout
        grace = 5
        while time.time() < deadline:
            try:
                container.reload()
            except docker.errors.NotFound:
                return False
            except docker.errors.APIError:
                time.sleep(2)
                continue

            state = container.attrs.get("State", {})
            status = state.get("Status")
            if status in ("exited", "dead"):
                return False
            if status != "running":
                time.sleep(2)
                continue

            health = state.get("Health")
            if health:
                if health.get("Status") == "healthy":
                    return True
                if health.get("Status") == "unhealthy":
                    return False
                time.sleep(2)
                continue

            # No healthcheck defined: running + short grace is enough.
            time.sleep(grace)
            try:
                container.reload()
            except docker.errors.NotFound:
                return False
            return container.attrs.get("State", {}).get("Status") == "running"
        return False

    def _wait_for_http_ready(self, ws_id: int, port: int, timeout: int) -> bool:
        """Wait until the workspace's web GUI actually answers on ``port``.

        The container reports "running" almost immediately, but LinuxServer init
        scripts (custom-cont-init.d — including our proot-apps / apt installs)
        block the web service from starting, so the stream would 502 if we marked
        the workspace running too early. We poll ``localhost:port`` from inside the
        workspace's network namespace using a short-lived helper container (the
        same netns trick as the egress guard), since the backend has no direct
        route onto the per-workspace network.

        Returns True once it answers, False on timeout. Fail-open (returns True)
        if the probe itself can't run, so a probe failure never blocks launches.
        """
        target = f"cove-ws-{ws_id}"
        script = (
            f"end=$(($(date +%s)+{timeout})); "
            f"while [ $(date +%s) -lt $end ]; do "
            f"curl -sf -o /dev/null http://localhost:{port}/ && exit 0; sleep 3; done; exit 1"
        )
        try:
            self._pull_image(EGRESS_GUARD_IMAGE)
            self._client.containers.run(
                EGRESS_GUARD_IMAGE,
                network_mode=f"container:{target}",
                remove=True,
                detach=False,
                entrypoint="/bin/sh",
                command=["-c", script],
            )
            return True  # exited 0 => GUI answered
        except docker.errors.ContainerError:
            logger.warning("Workspace %s web GUI not ready after %ds", ws_id, timeout)
            return False
        except Exception as exc:
            logger.warning("HTTP readiness probe failed for workspace %s: %s", ws_id, exc)
            return True  # don't block the launch on probe infrastructure issues

    def _detect_render_gid(self, render_node: str) -> "int | str | None":
        """Group GID that owns the DRI render node on this zone's host.

        Returns the GID (int) to ``group_add`` so the workspace user can open the
        device, ``"missing"`` if the node doesn't exist on the host (caller fails
        the launch with a clear message), or ``None`` if the probe couldn't run
        (fall back to the admin-configured GID; never block on probe infra).

        The device's group varies per host (e.g. 990 on Debian, 44/993 elsewhere)
        and a wrong GID silently breaks VAAPI — the classic "GPU on ⇒ stutter"
        failure — so probing the real device beats a fixed default. Cached since
        the device group is stable.
        """
        cached = self._render_gid_cache.get(render_node)
        if cached is not None:
            return cached
        if not render_node.startswith("/dev/"):
            return None  # unusual path; leave it to the configured GID
        # Bind /dev read-only so we can stat the node even when it's absent (a
        # non-existent --device mount can't be created; a /dev bind can).
        inside = "/hostdev" + render_node[len("/dev"):]
        script = f'if [ -e "{inside}" ]; then stat -c "%g" "{inside}"; else echo MISSING; fi'
        try:
            self._pull_image(EGRESS_GUARD_IMAGE)
            out = self._client.containers.run(
                EGRESS_GUARD_IMAGE,
                entrypoint="/bin/sh",
                command=["-c", script],
                volumes={"/dev": {"bind": "/hostdev", "mode": "ro"}},
                network_mode="none",
                remove=True,
                detach=False,
            )
        except Exception as exc:
            logger.warning("Render-node probe failed for %s (zone %s): %s", render_node, self.zone_id, exc)
            return None
        text = out.decode() if isinstance(out, (bytes, bytearray)) else str(out)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        val = lines[-1] if lines else ""
        result: "int | str | None"
        if val == "MISSING":
            result = "missing"
        elif val.isdigit():
            result = int(val)
        else:
            logger.warning("Unexpected render-node probe output for %s: %r", render_node, text)
            return None
        self._render_gid_cache[render_node] = result
        return result

    def launch_workspace(self, ws_id: int) -> None:
        db = self._get_db()
        try:
            ws = db.get(Workspace, ws_id)
            if not ws:
                return
            image: WorkspaceImage = ws.image

            settings = get_settings()
            container_name = f"cove-ws-{ws.id}"
            net_name = self._ws_network_name(ws.id)

            # Remote zone: confirm the agent is reachable up front so a bad
            # endpoint/cert/firewall fails fast with a clear message instead of
            # hanging partway through launch.
            if self.zone_id != 0:
                try:
                    self._client.ping()
                except Exception as exc:
                    raise RuntimeError(f"zone agent unreachable: {exc}") from exc

            self._ensure_ws_network(net_name)

            # Ephemeral workspaces get NO persistent bind mount: /config lives in
            # the container's writable layer and is discarded when the container is
            # removed (which happens on halt), so nothing is saved between sessions.
            mount_source = (
                None if ws.ephemeral else _resolve_mount(ws)[0]
            )

            env = {
                "PUID": str(settings.workspace_puid),
                "PGID": str(settings.workspace_pgid),
                "TZ": settings.workspace_tz,
                # LinuxServer Selkies images default their web GUI to different
                # HTTP ports per image — webtops/browsers use 3000, but some apps
                # (e.g. Calibre) default to 8080. Force the image to serve on the
                # port Cove probes + routes to (internal_port) via the baseimage's
                # CUSTOM_PORT override, so readiness detection and the Traefik
                # target are correct regardless of the image's own default.
                "CUSTOM_PORT": str(image.internal_port),
            }
            # Browser images open a startup URL via their native CLI env var
            # (e.g. CHROME_CLI / BRAVE_CLI / FIREFOX_CLI), which is appended to the
            # browser command. Assemble kiosk/full-screen + dark-mode flags.
            if ws.target_url and image.url_env:
                env[image.url_env] = _build_browser_cli(ws)
            elif ws.workspace_type == "link" and ws.target_url:
                # Legacy webtop-based link workspaces use a custom init script.
                env["LAUNCH_URL"] = ws.target_url
            # Pixelflux/Selkies images stream over Wayland by default; only set
            # the override when the user opts into the X11 fallback (harmless on
            # images that don't read it).
            if not ws.pixelflux_wayland:
                env["PIXELFLUX_WAYLAND"] = "false"

            # GPU (VAAPI) hardware acceleration: only when the admin master toggle
            # is on AND this workspace opted in. Bind-mount the host's DRI render
            # node and point DRINODE (EGL render) + DRI_NODE (VAAPI encode) at the
            # same node so Selkies does zero-copy hardware encode instead of CPU
            # x264. The abc user needs the render group's gid to open the device
            # (LSIO images don't reliably add it), so pass group_add. Hardware
            # encode also needs the Wayland stream (the default); DRI3 rendering
            # works in either mode.
            gpu_kwargs: dict = {}
            if ws.gpu_accel and get_workspace_gpu_accel(db):
                # Hardware VAAPI encode requires the Wayland stream. Refuse the
                # degraded combo loudly instead of shipping a stuttering,
                # software-encoded stream that looks like a GPU failure.
                if not ws.pixelflux_wayland:
                    raise RuntimeError(
                        "GPU acceleration needs Wayland streaming (hardware encode "
                        "requires it). Enable Wayland streaming, or turn off GPU "
                        "acceleration for this workspace."
                    )
                render_node = get_workspace_gpu_render_node(db)
                # Detect the render node's real group on the host. A wrong GID is
                # the #1 silent GPU failure (VAAPI can't open the device), so we
                # probe and use the actual group instead of a fixed default.
                detected = self._detect_render_gid(render_node)
                if detected == "missing":
                    raise RuntimeError(
                        f"GPU acceleration is on, but no render node exists at "
                        f"{render_node} on this host's GPU. Turn off GPU acceleration "
                        f"for this workspace, or set the correct render node in "
                        f"Admin → Settings."
                    )
                if isinstance(detected, int):
                    gid = detected
                    configured = get_workspace_gpu_render_gid(db)
                    if configured != detected:
                        logger.info(
                            "Workspace %s: render node %s is group %d on the host; "
                            "using it (admin-configured GID was %d)",
                            ws.id, render_node, detected, configured,
                        )
                else:
                    gid = get_workspace_gpu_render_gid(db)  # probe couldn't run
                env["DRINODE"] = render_node
                env["DRI_NODE"] = render_node
                gpu_kwargs = {
                    "devices": [f"{render_node}:{render_node}"],
                    "group_add": [str(gid)],
                }

            # Docker-in-Docker: only when the admin master toggle is on AND this
            # workspace opted in. Point the workspace at its (about-to-be-launched)
            # DinD sidecar and install the docker client; the sidecar itself is
            # started after the container is up (it needs to join its netns).
            docker_enabled = ws.use_docker and get_workspace_docker(db)

            volumes = {}
            if mount_source:
                volumes[mount_source] = {"bind": "/config", "mode": "rw"}
            if ws.workspace_type == "link" and not image.url_env:
                volumes[_helper_script_path("launch-url.sh")] = {
                    "bind": "/custom-cont-init.d/99-launch-url.sh",
                    "mode": "ro",
                }

            # Per-workspace package installation (applies to both launch paths).
            self._apply_username(env, volumes, ws)
            self._apply_package_env(env, ws.install_packages)
            self._apply_proot_apps(env, volumes, ws.proot_apps)
            self._apply_appimages(env, volumes, ws.appimages)
            self._apply_ssh_key(ws, volumes)
            self._apply_cove_theme(volumes)
            self._apply_mate_theme_fix(volumes)
            self._apply_browser_lock_cleanup(ws, volumes)
            if docker_enabled:
                self._apply_docker_cli(env, volumes)

            labels = self._build_traefik_labels(ws, image, net_name)

            try:
                # Remove any stale container with the same name
                try:
                    old = self._client.containers.get(container_name)
                    old.remove(force=True)
                except docker.errors.NotFound:
                    pass

                # Always pull so a halted-then-restarted workspace gets the latest.
                self._pull_image(image.docker_image)

                # Common container hardening. no-new-privileges blocks in-container
                # sudo (setuid): applied when the admin setting forces it OR the
                # workspace itself opts out of sudo.
                hardening = self._build_hardening(
                    no_new_privileges_setting=get_workspace_no_new_privileges(db),
                    allow_sudo=ws.allow_sudo,
                )

                # Custom DNS: forwarders for the workspace resolver. Only for
                # non-Tailscale workspaces — Tailscale routes DNS through tailscaled
                # (which owns the shared netns resolv.conf), so it isn't overridable.
                dns = None if ws.use_tailscale else _dns_list(ws)

                # Admin-configured CPU/memory caps (empty = uncapped).
                limits = _resource_limits(db)

                # Direct (raw-bridge) LAN subnets this workspace may reach: only
                # when the admin master toggle AND the per-workspace opt-in are
                # both on. Tailnet-routed LAN (Tailscale) is independent of this.
                lan_subnets = (
                    get_workspace_lan_subnets(db)
                    if ws.lan_access and get_workspace_lan_access(db)
                    else []
                )
                # Always let a workspace reach the specific LAN host its target_url
                # points at (as a /32), so "open a LAN website" works without the
                # admin LAN toggle. Docker-internal/metadata stay blocked (those
                # ranges are dropped before these accepts in the egress rules).
                lan_subnets = list(
                    dict.fromkeys(lan_subnets + _target_url_lan_ips(ws.target_url))
                )

                if ws.use_tailscale:
                    ts_cfg = db.scalar(
                        select(UserTailscale).where(UserTailscale.user_id == ws.user_id)
                    )
                    ts_image = get_tailscale_image(db)
                    self._launch_tailscale_sidecar(
                        ws, image, net_name, labels, ts_cfg, ts_image
                    )
                    # Firewall the sidecar's netns BEFORE the workspace joins it,
                    # so rules are in place before the workload emits any traffic
                    # (closes the startup race). tailscale0 traffic is allowed, so
                    # the tailnet/exit-node/subnet routes keep working.
                    self._apply_egress_guard(
                        ws.id,
                        tailscale=True,
                        lan_subnets=lan_subnets,
                        target=self._ts_sidecar_name(ws.id),
                    )
                    # The workspace container shares the sidecar's netns. No own
                    # network, no traefik labels (the sidecar carries routing).
                    container = self._client.containers.run(
                        image.docker_image,
                        name=container_name,
                        detach=True,
                        environment=env,
                        volumes=volumes,
                        network_mode=f"container:{self._ts_sidecar_name(ws.id)}",
                        shm_size="1g",
                        **gpu_kwargs,
                        **limits,
                        **hardening,
                    )
                elif ws.use_gluetun:
                    g_cfg = db.scalar(
                        select(UserGluetun).where(UserGluetun.user_id == ws.user_id)
                    )
                    if not g_cfg or not g_cfg.config_file:
                        ws.status = "error"
                        ws.error_message = "Gluetun is not configured (upload a VPN config)"
                        db.commit()
                        return
                    # Carry the Traefik labels on the gluetun sidecar; pass the
                    # workspace network's subnet so its killswitch firewall lets
                    # Traefik reach the stream port.
                    self._launch_gluetun_sidecar(
                        ws, image, net_name, labels, g_cfg,
                        get_gluetun_image(db), self._ws_network_subnet(net_name),
                    )
                    # gluetun's killswitch is the egress control; the workspace
                    # joins its netns and inherits the VPN tunnel.
                    container = self._client.containers.run(
                        image.docker_image,
                        name=container_name,
                        detach=True,
                        environment=env,
                        volumes=volumes,
                        network_mode=f"container:{self._gluetun_sidecar_name(ws.id)}",
                        shm_size="1g",
                        **gpu_kwargs,
                        **limits,
                        **hardening,
                    )
                else:
                    container = self._client.containers.run(
                        image.docker_image,
                        name=container_name,
                        detach=True,
                        environment=env,
                        volumes=volumes,
                        labels=labels,
                        network=net_name,
                        shm_size="1g",
                        **({"dns": dns} if dns else {}),
                        **gpu_kwargs,
                        **limits,
                        **hardening,
                    )
                logger.info("Started container %s for workspace %s", container.id[:12], ws_id)

                # Connect Traefik to this isolated network so it can route to the
                # workspace. The backend itself is never attached.
                try:
                    self._client.networks.get(net_name).connect(settings.traefik_container)
                except docker.errors.APIError:
                    # Already connected (or transient) is fine.
                    pass

                ws.container_id = container.id
                ws.container_name = container_name
                ws.volume_name = mount_source
                db.commit()

                # EGRESS GUARD for plain workspaces. Tailscale workspaces are
                # guarded above (before start); Gluetun workspaces rely on
                # gluetun's own killswitch firewall (adding our OUTPUT drops would
                # fight it), so both are skipped here.
                if not ws.use_tailscale and not ws.use_gluetun:
                    self._apply_egress_guard(ws.id, lan_subnets=lan_subnets)

                # DinD sidecar: launched now that the netns owner (the workspace
                # container, or its routing sidecar) is running and the egress
                # guard is in place. Best-effort — a DinD failure leaves the
                # workspace usable (docker commands will just report no daemon).
                if docker_enabled:
                    netns_owner = (
                        self._ts_sidecar_name(ws.id)
                        if ws.use_tailscale
                        else self._gluetun_sidecar_name(ws.id)
                        if ws.use_gluetun
                        else container_name
                    )
                    try:
                        self._launch_dind_sidecar(ws, netns_owner, get_dind_image(db))
                        self._apply_dind_egress_guard(
                            ws.id, lan_subnets, tailscale=ws.use_tailscale
                        )
                    except Exception as exc:
                        logger.warning("DinD setup failed for workspace %s: %s", ws.id, exc)

                started = self._wait_for_ready(container)
                ws = db.get(Workspace, ws_id)  # re-fetch after wait
                # A Halt is allowed while "creating" and runs concurrently with
                # this launch. If it moved the workspace out of "creating" while we
                # waited, don't clobber that with running/error — the stop task now
                # owns the final state (and will remove this container).
                if not ws or ws.status != "creating":
                    logger.info("Workspace %s left 'creating' during launch — not overriding", ws_id)
                    return
                if not started:
                    ws.status = "error"
                    ws.error_message = "Container did not start"
                    db.commit()
                else:
                    # Fast path: give the GUI a short window to answer so simple
                    # workspaces flip to running promptly. Big package/proot
                    # installs run before the GUI starts and can take many minutes
                    # — those stay "creating" (the provisioning screen) and the
                    # status monitor promotes them once the GUI answers, so we
                    # never give up just because an install is slow.
                    if self._wait_for_http_ready(ws.id, image.internal_port, 60):
                        ws.status = "running"
                        ws.started_at = datetime.now(timezone.utc)
                        db.commit()

            except docker.errors.APIError as exc:
                logger.error("Docker error launching workspace %s: %s", ws_id, exc)
                ws = db.get(Workspace, ws_id)
                ws.status = "error"
                ws.error_message = str(exc)
                db.commit()
        except Exception as exc:
            # Anything not caught above — most importantly connection/timeout
            # errors reaching a remote zone's agent (these are requests/urllib3
            # exceptions, NOT docker.errors.APIError). Without this the launch
            # task would die silently and the workspace would sit in "creating"
            # forever. Surface it as a readable error instead.
            logger.exception("Failed to launch workspace %s", ws_id)
            ws = db.get(Workspace, ws_id)
            if ws:
                ws.status = "error"
                ws.error_message = str(exc) or exc.__class__.__name__
                db.commit()
        finally:
            db.close()

    def clone_and_launch(self, src_id: int, dst_id: int) -> None:
        """Background task: copy the source's persistent home, then launch the clone."""
        db = self._get_db()
        try:
            src = db.get(Workspace, src_id)
            dst = db.get(Workspace, dst_id)
            if not dst:
                return
            try:
                copy_workspace_storage(src, dst)
            except Exception as exc:
                logger.error("Clone storage copy failed %s -> %s: %s", src_id, dst_id, exc)
                dst.status = "error"
                dst.error_message = "Clone failed while copying storage"
                db.commit()
                return
        finally:
            db.close()
        # Launch in the same flow as a normal create (own DB session).
        self.launch_workspace(dst_id)

    def get_stats(self, container_id: str) -> dict | None:
        """Live CPU/memory for one running container, or None if unavailable."""
        try:
            container = self._client.containers.get(container_id)
            if container.status != "running":
                return None
            raw = container.stats(stream=False)
        except (docker.errors.NotFound, docker.errors.APIError):
            return None
        except Exception as exc:  # defensive: never let a stats read break the API
            logger.debug("stats read failed for %s: %s", container_id[:12], exc)
            return None
        return _parse_stats(raw)

    def get_tailscale_ip(self, ws_id: int) -> str | None:
        """The tailnet IPv4 of a workspace's Tailscale sidecar, or None.

        The 100.x address lives on a tailscale interface *inside* the sidecar's
        network namespace, so Docker inspect can't see it — we ask tailscaled
        directly via ``tailscale ip -4``. Returns None until the sidecar has
        finished authenticating/joining the tailnet (or if it isn't running).
        """
        sidecar_name = self._ts_sidecar_name(ws_id)
        try:
            sidecar = self._client.containers.get(sidecar_name)
            if sidecar.status != "running":
                return None
            code, out = sidecar.exec_run(["tailscale", "ip", "-4"])
        except (docker.errors.NotFound, docker.errors.APIError):
            return None
        except Exception as exc:  # defensive: never let this break the stats API
            logger.debug("tailscale ip read failed for ws %s: %s", ws_id, exc)
            return None
        if code != 0 or not out:
            return None
        ip = out.decode(errors="ignore").strip().splitlines()
        return ip[0].strip() if ip and ip[0].strip() else None

    def tailscale_status(self, ws_id: int) -> str | None:
        """Output of ``tailscale status`` from a workspace's Tailscale sidecar.

        Returns the raw text (which is exactly what we want to surface even when
        the command exits non-zero, e.g. logged-out), or None if the sidecar is
        missing / not running / produced nothing.
        """
        sidecar_name = self._ts_sidecar_name(ws_id)
        try:
            sidecar = self._client.containers.get(sidecar_name)
            if sidecar.status != "running":
                return None
            _code, out = sidecar.exec_run(["tailscale", "status"])
        except (docker.errors.NotFound, docker.errors.APIError):
            return None
        except Exception as exc:  # defensive: never let this break the API
            logger.debug("tailscale status read failed for ws %s: %s", ws_id, exc)
            return None
        text = out.decode(errors="ignore").strip() if out else ""
        return text or None

    def _diag_container_name(self, ws: Workspace, source: str) -> "str | None":
        """Resolve a diagnostics log source to a concrete container name/id, or
        None if the source doesn't apply to this workspace."""
        if source == "desktop":
            return ws.container_id or f"cove-ws-{ws.id}"
        if source == "tailscale" and ws.use_tailscale:
            return self._ts_sidecar_name(ws.id)
        if source == "gluetun" and ws.use_gluetun:
            return self._gluetun_sidecar_name(ws.id)
        return None

    def container_logs(self, ws: Workspace, source: str, tail: int = 200) -> str | None:
        """Recent logs from one of a workspace's containers — the desktop itself
        or its Tailscale / Gluetun sidecar. Returns decoded text (possibly empty),
        or None if the container is missing or the source doesn't apply.
        """
        name = self._diag_container_name(ws, source)
        if not name:
            return None
        try:
            container = self._client.containers.get(name)
            raw = container.logs(tail=tail, timestamps=False)
        except (docker.errors.NotFound, docker.errors.APIError):
            return None
        except Exception as exc:  # defensive: never let a log read break the API
            logger.debug("logs read failed for ws %s/%s: %s", ws.id, source, exc)
            return None
        return raw.decode(errors="ignore") if raw else ""

    def _sidecar_failure(self, ws: Workspace) -> "str | None":
        """If this workspace's required routing sidecar has definitively failed,
        return a user-facing message; otherwise None.

        For Gluetun, an *unhealthy* sidecar means the VPN tunnel never came up —
        and Traefik's Docker provider drops unhealthy containers from routing, so
        the stream would 404 with no explanation. We surface that as a workspace
        error instead. A sidecar that is still "starting" is treated as pending
        (not a failure). Tailscale has no healthcheck (and its stream works even
        when egress is down), so we only flag a sidecar that has actually died.
        """
        if not (ws.use_gluetun or ws.use_tailscale):
            return None
        gluetun = ws.use_gluetun
        name = (
            self._gluetun_sidecar_name(ws.id)
            if gluetun
            else self._ts_sidecar_name(ws.id)
        )
        label = "VPN" if gluetun else "Tailscale"
        try:
            sidecar = self._client.containers.get(name)
        except docker.errors.NotFound:
            return f"{label} sidecar is not running"
        except docker.errors.APIError:
            return None  # transient; don't flap the status on a Docker hiccup
        state = sidecar.attrs.get("State", {})
        if state.get("Status") in ("exited", "dead"):
            hint = "check your Gluetun config" if gluetun else "check your auth key"
            return f"{label} sidecar stopped — {hint}"
        if gluetun:
            health = (state.get("Health") or {}).get("Status")
            if health == "unhealthy":
                return "VPN failed to connect — check your Gluetun config"
        return None

    def stop_workspace(self, ws_id: int) -> None:
        db = self._get_db()
        try:
            ws = db.get(Workspace, ws_id)
            if not ws:
                return
            # Tear the DinD sidecar down FIRST: for plain workspaces it shares the
            # workspace container's netns, so the container can't be removed while
            # the sidecar is still attached.
            try:
                self._cleanup_docker_sidecar(ws.id)
            except Exception as exc:
                logger.warning("DinD cleanup failed for workspace %s: %s", ws.id, exc)
            if ws.container_id:
                try:
                    container = self._client.containers.get(ws.container_id)
                    container.stop(timeout=10)
                    container.remove()
                except docker.errors.NotFound:
                    pass
                except docker.errors.APIError as exc:
                    logger.warning("Error stopping container %s: %s", ws.container_id[:12], exc)
                except Exception as exc:
                    logger.warning("Error stopping container for ws %s: %s", ws.id, exc)

            # Best-effort cleanup — must not block marking the workspace stopped
            # (a workspace on an unreachable zone would otherwise stick in
            # "stopping" forever).
            try:
                self._cleanup_tailscale_sidecar(ws.id)
                self._cleanup_gluetun_sidecar(ws.id)
                self._cleanup_ws_network(self._ws_network_name(ws.id))
                _remove_ssh_key(ws.id)
            except Exception as exc:
                logger.warning("Stop cleanup failed for workspace %s: %s", ws.id, exc)

            ws.status = "stopped"
            ws.stopped_at = datetime.now(timezone.utc)
            db.commit()
        finally:
            db.close()

    def remove_workspace(self, ws_id: int, purge_storage: bool = False) -> None:
        """Stop container and delete workspace record.

        When ``purge_storage`` is set, also delete the workspace's persistent
        home directory (otherwise it is left on disk for reuse).
        """
        db = self._get_db()
        try:
            ws = db.get(Workspace, ws_id)
            if not ws:
                return
            # Best-effort teardown — it must NEVER block deleting the DB record.
            # Otherwise a workspace on an unreachable zone (connection errors are
            # not docker.errors.APIError) can't be purged: the cleanup raises
            # before db.delete and the row reappears on the next poll.
            try:
                # DinD sidecar first — it shares the workspace container's netns,
                # so the container can't be removed while it is still attached.
                self._cleanup_docker_sidecar(ws.id)
                if ws.container_id:
                    try:
                        container = self._client.containers.get(ws.container_id)
                        container.stop(timeout=5)
                        container.remove()
                    except (docker.errors.NotFound, docker.errors.APIError):
                        pass
                self._cleanup_tailscale_sidecar(ws.id)
                self._cleanup_gluetun_sidecar(ws.id)
                self._cleanup_ws_network(self._ws_network_name(ws.id))
                _remove_ssh_key(ws.id)
            except Exception as exc:
                logger.warning(
                    "Best-effort teardown failed for workspace %s (deleting record anyway): %s",
                    ws_id, exc,
                )
            if purge_storage:
                try:
                    delete_workspace_storage(ws)
                except Exception as exc:
                    logger.warning("Storage purge failed for workspace %s: %s", ws_id, exc)
            db.delete(ws)
            db.commit()
        finally:
            db.close()

    # Max time a workspace may sit in "creating" before it's declared failed.
    # Generous because big proot-apps / package installs (a dozen+ apps, plus
    # ghcr rate-limiting) run before the GUI starts. Even if exceeded, an "error"
    # workspace is auto-recovered once its GUI finally answers (see sync below).
    _PROVISION_DEADLINE_SECONDS = 3600
    # A normal Halt clears "stopping" in seconds; a row still stopping past this
    # means the stop task died (e.g. a control-plane restart), so re-drive it.
    _STOPPING_DEADLINE_SECONDS = 120
    # A migration can legitimately take a while (multi-GB /config over mTLS), so
    # only a row stuck this long is treated as a dead task and failed back.
    _MIGRATING_DEADLINE_SECONDS = 1800

    def sync_workspace_statuses(self, zone_id: int | None = None) -> None:
        """Reconcile workspace status with reality, for one zone's workspaces.

        - running workspaces whose container has died -> error.
        - creating workspaces whose GUI now answers -> running (self-healing
          promotion, so slow installs that outlast the launch-time probe still
          come up); expired ones -> error.
        - workspaces wedged in stopping/migrating past their deadline (their owning
          background task died) -> re-driven to stopped / failed to error.

        Only workspaces pinned to this manager's zone are reconciled — querying a
        remote workspace's container against the local daemon would wrongly mark
        it gone. ``zone_id`` defaults to this manager's own zone.
        """
        zone = self.zone_id if zone_id is None else zone_id
        db = self._get_db()
        try:
            from sqlalchemy import select
            running = db.scalars(
                select(Workspace).where(
                    Workspace.status == "running", Workspace.zone_id == zone
                )
            ).all()
            for ws in running:
                if not ws.container_id:
                    continue
                try:
                    container = self._client.containers.get(ws.container_id)
                    if container.status in ("exited", "dead"):
                        ws.status = "error"
                        ws.error_message = f"Container exited unexpectedly (status: {container.status})"
                        db.commit()
                        continue
                except docker.errors.NotFound:
                    ws.status = "error"
                    ws.error_message = "Container not found"
                    db.commit()
                    continue
                # The workload is up, but a routing sidecar (Gluetun/Tailscale)
                # may have failed — e.g. a Gluetun tunnel that never connected.
                # Traefik drops the unhealthy sidecar, so the stream 404s; surface
                # that as an error rather than leaving the workspace "running".
                sidecar_msg = self._sidecar_failure(ws)
                if sidecar_msg:
                    ws.status = "error"
                    ws.error_message = sidecar_msg
                    db.commit()

            # Promote provisioning workspaces once their GUI answers — and also
            # recover ones we previously marked "error" (e.g. a very large install
            # that outran the deadline but did finish and is now serving).
            pending = db.scalars(
                select(Workspace).where(
                    Workspace.status.in_(("creating", "error")),
                    Workspace.zone_id == zone,
                )
            ).all()
            now = datetime.now(timezone.utc)

            def _age(w) -> float:
                """Seconds since the workspace last changed status (falls back to
                created_at for pre-upgrade rows with no status_changed_at)."""
                anchor = w.status_changed_at or w.created_at
                if anchor is None:
                    return 0.0
                if anchor.tzinfo is None:
                    anchor = anchor.replace(tzinfo=timezone.utc)
                return (now - anchor).total_seconds()

            for ws in pending:
                if not ws.container_id:
                    # No container recorded. Usually just a launch in flight — but
                    # if the launch task died before starting one, the row would
                    # sit in "creating" forever, so fail it once past the deadline.
                    if ws.status == "creating" and _age(ws) > self._PROVISION_DEADLINE_SECONDS:
                        ws.status = "error"
                        ws.error_message = "Launch did not start in time"
                        ws.status_changed_at = now
                        db.commit()
                    continue  # otherwise: launch still in flight, leave as-is
                try:
                    container = self._client.containers.get(ws.container_id)
                except docker.errors.NotFound:
                    continue  # mid-launch or genuinely gone; leave status as-is
                if container.status in ("exited", "dead"):
                    if ws.status == "creating":
                        ws.status = "error"
                        ws.error_message = "Container exited during startup"
                        db.commit()
                    continue
                if container.status != "running":
                    continue
                # A failed routing sidecar keeps the workspace in error even if the
                # GUI answers on localhost (it answers regardless of VPN health).
                sidecar_msg = self._sidecar_failure(ws)
                if sidecar_msg:
                    if ws.status != "error" or ws.error_message != sidecar_msg:
                        ws.status = "error"
                        ws.error_message = sidecar_msg
                        db.commit()
                    continue
                port = ws.image.internal_port if ws.image else 3000
                if self._wait_for_http_ready(ws.id, port, 5):
                    ws.status = "running"
                    ws.started_at = now
                    ws.error_message = None
                    db.commit()
                    continue
                # Still starting: only fail a "creating" one past the (generous)
                # deadline. An "error" one stays error until its GUI answers.
                if ws.status == "creating" and _age(ws) > self._PROVISION_DEADLINE_SECONDS:
                    ws.status = "error"
                    ws.error_message = "Workspace did not become ready in time"
                    ws.status_changed_at = now
                    db.commit()

            # Recover workspaces wedged in a transition whose owning background
            # task died (e.g. the control plane restarted mid-stop/mid-migrate).
            # Anchored on status_changed_at so a normal in-flight stop/migrate is
            # untouched — only rows stuck past a generous deadline are cleared.
            stuck = db.scalars(
                select(Workspace).where(
                    Workspace.status.in_(("stopping", "migrating")),
                    Workspace.zone_id == zone,
                )
            ).all()
            for ws in stuck:
                if ws.status == "stopping" and _age(ws) > self._STOPPING_DEADLINE_SECONDS:
                    # Re-drive the stop to completion — it's idempotent (tears down
                    # container/sidecars/network, then marks the row stopped).
                    logger.warning(
                        "Workspace %s wedged in 'stopping' for %ds — re-driving stop",
                        ws.id, int(_age(ws)),
                    )
                    self.stop_workspace(ws.id)
                elif ws.status == "migrating" and _age(ws) > self._MIGRATING_DEADLINE_SECONDS:
                    # The migration task never finished. It's copy-then-delete, so
                    # the source storage + zone pin are intact — fail back to error
                    # (the user can retry the migration or boot it where it is).
                    logger.warning(
                        "Workspace %s wedged in 'migrating' for %ds — marking error",
                        ws.id, int(_age(ws)),
                    )
                    ws.status = "error"
                    ws.error_message = "Migration did not finish — please retry"
                    ws.status_changed_at = now
                    db.commit()
        except Exception as exc:
            logger.warning("sync_workspace_statuses error: %s", exc)
        finally:
            db.close()

    def enforce_runtime_limits(self, zone_id: int | None = None) -> None:
        """Auto-stop this zone's running workspaces older than the configured
        max runtime."""
        zone = self.zone_id if zone_id is None else zone_id
        db = self._get_db()
        try:
            from sqlalchemy import select

            hours = get_workspace_max_runtime_hours(db)
            if hours <= 0:
                return
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            running = db.scalars(
                select(Workspace).where(
                    Workspace.status == "running", Workspace.zone_id == zone
                )
            ).all()
            expired_ids = []
            for ws in running:
                started = ws.started_at
                if started is None:
                    continue
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                if started < cutoff:
                    expired_ids.append(ws.id)
        except Exception as exc:
            logger.warning("enforce_runtime_limits error: %s", exc)
            return
        finally:
            db.close()

        for ws_id in expired_ids:
            logger.info("Auto-stopping workspace %s (exceeded %dh runtime)", ws_id, hours)
            try:
                self.stop_workspace(ws_id)
            except Exception as exc:
                logger.warning("Auto-stop of workspace %s failed: %s", ws_id, exc)

    @staticmethod
    def _build_egress_rules(tailscale: bool, lan_subnets: list[str]) -> str:
        """Build the iptables OUTPUT script for a workspace netns.

        Filtering is by DESTINATION on OUTPUT, so the WAN stays reachable through
        the RFC1918 default gateway (the gateway is only the L2 next hop — the
        packet's destination is the public host, which is allowed).

        Rule order matters:
          1. loopback + embedded Docker DNS + ESTABLISHED/RELATED → ACCEPT.
          2. (Tailscale only) anything egressing tailscale0 → ACCEPT. This covers
             the tailnet, tailnet peers (incl. other Tailscale workspaces), exit
             nodes, and subnet-router-advertised LAN — all before any DROP.
          3. _ALWAYS_BLOCK (metadata + Docker-internal) → DROP. Applied to every
             workspace so container/backend isolation holds regardless of mode.
          4. Admin-granted LAN subnets → ACCEPT (raw-bridge direct LAN).
          5. _LAN_BLOCK (remaining private + CGNAT) → DROP.
          6. Default policy ACCEPT → WAN.
        """
        rules = [
            "iptables -P OUTPUT ACCEPT",
            "iptables -A OUTPUT -o lo -j ACCEPT",
        ]
        if tailscale:
            # tailscale0 may not exist yet when this runs; iptables accepts the
            # interface name regardless and the rule matches once it appears.
            rules.append("iptables -A OUTPUT -o tailscale0 -j ACCEPT")
        rules += [
            "iptables -A OUTPUT -d 127.0.0.11 -j ACCEPT",
            "iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
        ]
        for cidr in _ALWAYS_BLOCK:
            rules.append(f"iptables -A OUTPUT -d {cidr} -j DROP")
        for cidr in lan_subnets:
            rules.append(f"iptables -A OUTPUT -d {cidr} -j ACCEPT")
        for cidr in _LAN_BLOCK:
            rules.append(f"iptables -A OUTPUT -d {cidr} -j DROP")
        return " && ".join(rules)

    def _apply_egress_guard(
        self,
        ws_id: int,
        *,
        tailscale: bool = False,
        lan_subnets: list[str] | None = None,
        target: str | None = None,
    ) -> None:
        """Install the egress firewall in a workspace's network namespace.

        Runs a short-lived helper container sharing the target's netns and
        applies the rules from :meth:`_build_egress_rules`. ``target`` is the
        container whose netns to enter — the workspace container for plain
        workspaces, or the Tailscale sidecar (which owns the shared netns) for
        Tailscale workspaces.

        Best-effort: any failure is logged and never aborts the launch. For
        non-Tailscale workspaces this runs just after the container starts, so a
        packet in that brief window could slip through; for Tailscale workspaces
        it runs against the sidecar BEFORE the workspace container starts, so the
        rules are in place before the workload can emit any traffic.
        """
        target = target or f"cove-ws-{ws_id}"
        script = self._build_egress_rules(tailscale, lan_subnets or [])

        try:
            self._pull_image(EGRESS_GUARD_IMAGE)
            self._client.containers.run(
                EGRESS_GUARD_IMAGE,
                network_mode=f"container:{target}",
                cap_add=["NET_ADMIN"],
                remove=True,
                detach=False,
                entrypoint="/bin/sh",
                command=["-c", script],
            )
            logger.info(
                "Applied egress guard for workspace %s (tailscale=%s, lan=%s)",
                ws_id, tailscale, ",".join(lan_subnets or []) or "none",
            )
        except Exception as exc:
            logger.warning("Egress guard failed for workspace %s: %s", ws_id, exc)

    def _launch_tailscale_sidecar(
        self,
        ws: Workspace,
        image: WorkspaceImage,
        net_name: str,
        labels: dict,
        ts_cfg: "UserTailscale | None",
        ts_image: str = "tailscale/tailscale:latest",
    ) -> None:
        """Launch the Tailscale sidecar that carries this workspace's netns + routing.

        The sidecar holds the Traefik labels; the workspace container later joins
        its network namespace via network_mode=container:<sidecar>.
        """
        sidecar_name = self._ts_sidecar_name(ws.id)
        volume_name = self._ts_volume_name(ws.id)

        # Remove any stale sidecar with the same name.
        try:
            old = self._client.containers.get(sidecar_name)
            old.remove(force=True)
        except docker.errors.NotFound:
            pass

        self._ensure_named_volume(volume_name)

        # auth_key + login_server are per-user; the routing options are per-workspace.
        # The auth_key is stored encrypted at rest; decrypt it for the sidecar.
        auth_key = decrypt_secret(ts_cfg.auth_key) if ts_cfg else None
        login_server = ts_cfg.login_server if ts_cfg else None
        extra_args = build_ts_extra_args(
            exit_node=ws.ts_exit_node,
            accept_routes=ws.ts_accept_routes,
            accept_dns=ws.ts_accept_dns,
            login_server=login_server,
        )

        environment = {
            "TS_AUTHKEY": auth_key or "",
            "TS_USERSPACE": "false",
            "TS_STATE_DIR": "/var/lib/tailscale",
            "TS_HOSTNAME": f"cove-{ws.id}",
            "TS_EXTRA_ARGS": " ".join(extra_args),
        }

        # Custom DNS is intentionally NOT applied here: Tailscale workspaces route
        # DNS through tailscaled (MagicDNS / accept-dns), which owns the netns
        # resolv.conf. Custom DNS only applies to non-Tailscale workspaces.
        self._pull_image(ts_image)
        self._client.containers.run(
            ts_image,
            name=sidecar_name,
            detach=True,
            environment=environment,
            cap_add=["NET_ADMIN"],
            devices=["/dev/net/tun:/dev/net/tun"],
            volumes={volume_name: {"bind": "/var/lib/tailscale", "mode": "rw"}},
            labels=labels,
            network=net_name,
        )
        logger.info("Started Tailscale sidecar %s for workspace %s", sidecar_name, ws.id)

    def _ws_network_subnet(self, net_name: str) -> "str | None":
        """The IPv4 subnet CIDR Docker assigned to a per-workspace network."""
        try:
            cfgs = self._client.networks.get(net_name).attrs.get("IPAM", {}).get("Config") or []
            for c in cfgs:
                sn = c.get("Subnet")
                if sn and ":" not in sn:  # IPv4 only
                    return sn
        except (docker.errors.APIError, KeyError):
            pass
        return None

    @staticmethod
    def _gluetun_mount_target(vpn_type: str) -> str:
        """Where gluetun reads a custom config: OpenVPN at /gluetun/custom.conf,
        Wireguard at /gluetun/wireguard/wg0.conf."""
        return "/gluetun/wireguard/wg0.conf" if vpn_type == "wireguard" else "/gluetun/custom.conf"

    @staticmethod
    def _build_gluetun_env(
        vpn_type: str,
        image_port: int,
        subnet: "str | None",
        *,
        openvpn_user: "str | None" = None,
        openvpn_password: "str | None" = None,
        wireguard_private_key: "str | None" = None,
    ) -> dict:
        """Env for a custom-provider gluetun sidecar. FIREWALL_INPUT_PORTS +
        FIREWALL_OUTBOUND_SUBNETS let Traefik (on the local docker bridge) reach the
        workspace's port through gluetun's killswitch. Direct secrets override the
        values inside the mounted config file."""
        env = {
            "VPN_SERVICE_PROVIDER": "custom",
            "VPN_TYPE": vpn_type,
            "FIREWALL_INPUT_PORTS": str(image_port),
        }
        if subnet:
            env["FIREWALL_OUTBOUND_SUBNETS"] = subnet
        if vpn_type == "wireguard":
            if wireguard_private_key:
                env["WIREGUARD_PRIVATE_KEY"] = wireguard_private_key
        else:  # openvpn
            env["OPENVPN_CUSTOM_CONFIG"] = "/gluetun/custom.conf"
            if openvpn_user:
                env["OPENVPN_USER"] = openvpn_user
            if openvpn_password:
                env["OPENVPN_PASSWORD"] = openvpn_password
        return env

    def _launch_gluetun_sidecar(
        self,
        ws: Workspace,
        image: WorkspaceImage,
        net_name: str,
        labels: dict,
        g_cfg: "UserGluetun",
        gluetun_image: str,
        subnet: "str | None",
    ) -> None:
        """Launch the Gluetun VPN sidecar that carries this workspace's netns.

        The sidecar holds the Traefik labels; the workspace joins its netns via
        network_mode=container:<sidecar>. The (encrypted-at-rest) config file is
        decrypted and mounted read-only; gluetun's own killswitch handles egress.
        """
        sidecar_name = self._gluetun_sidecar_name(ws.id)
        try:
            old = self._client.containers.get(sidecar_name)
            old.remove(force=True)
        except docker.errors.NotFound:
            pass

        vpn_type = (g_cfg.vpn_type or "openvpn") if g_cfg else "openvpn"
        config = decrypt_secret(g_cfg.config_file) if g_cfg else None
        if not config:
            raise RuntimeError("Gluetun config file is not set")
        host_path = _stage_gluetun_config(ws.id, config)

        environment = self._build_gluetun_env(
            vpn_type,
            image.internal_port,
            subnet,
            openvpn_user=decrypt_secret(g_cfg.openvpn_user),
            openvpn_password=decrypt_secret(g_cfg.openvpn_password),
            wireguard_private_key=decrypt_secret(g_cfg.wireguard_private_key),
        )
        volumes = {host_path: {"bind": self._gluetun_mount_target(vpn_type), "mode": "ro"}}

        self._pull_image(gluetun_image)
        self._client.containers.run(
            gluetun_image,
            name=sidecar_name,
            detach=True,
            environment=environment,
            cap_add=["NET_ADMIN"],
            devices=["/dev/net/tun:/dev/net/tun"],
            volumes=volumes,
            labels=labels,
            network=net_name,
        )
        logger.info("Started Gluetun sidecar %s for workspace %s (%s)", sidecar_name, ws.id, vpn_type)

    @staticmethod
    def _apply_docker_cli(env: dict, volumes: dict) -> None:
        """Point the workspace at its DinD sidecar and install the docker client.

        Sets DOCKER_HOST to the sidecar's loopback daemon (shared netns) and mounts
        an init script that fetches the static ``docker`` CLI if the image lacks
        one. Mutates both dicts in place. Mirrors ``_apply_proot_apps`` /
        ``_apply_appimages``."""
        env["DOCKER_HOST"] = "tcp://127.0.0.1:2375"
        volumes[_helper_script_path("install-docker-cli.sh")] = {
            "bind": "/custom-cont-init.d/96-install-docker-cli.sh",
            "mode": "ro",
        }

    def _launch_dind_sidecar(self, ws: Workspace, netns_owner: str, dind_image: str) -> None:
        """Launch the per-workspace Docker-in-Docker daemon sidecar.

        The sidecar runs a PRIVILEGED nested ``dockerd`` and joins the workspace's
        (already egress-guarded) network namespace, exposing the daemon ONLY on
        127.0.0.1:2375 inside that shared netns — never on the workspace bridge, so
        Traefik and other containers can't reach it, and TLS is unnecessary. The
        host Docker socket is never mounted; a breakout stays confined to this
        throwaway sidecar. State lives in a per-workspace volume discarded on halt.
        """
        sidecar_name = self._dind_sidecar_name(ws.id)
        volume_name = self._dind_volume_name(ws.id)

        # Remove any stale sidecar with the same name.
        try:
            old = self._client.containers.get(sidecar_name)
            old.remove(force=True)
        except docker.errors.NotFound:
            pass

        self._ensure_named_volume(volume_name)

        self._pull_image(dind_image)
        self._client.containers.run(
            dind_image,
            name=sidecar_name,
            detach=True,
            privileged=True,
            network_mode=f"container:{netns_owner}",
            # DOCKER_TLS_CERTDIR="" disables the entrypoint's TLS setup. The
            # command MUST start with the literal "dockerd": the dind entrypoint
            # only injects its own "--host=tcp://0.0.0.0:2375" default when the
            # command is empty or starts with a flag — passing "dockerd" explicitly
            # skips that (which would otherwise collide on port 2375) while still
            # running its tini/iptables/cgroup setup. We then bind ONLY the loopback
            # of the shared netns (plus the unix socket) so nothing off-box — not
            # even Traefik on the workspace bridge — can reach the daemon.
            environment={"DOCKER_TLS_CERTDIR": ""},
            command=[
                "dockerd",
                "--host=unix:///var/run/docker.sock",
                "--host=tcp://127.0.0.1:2375",
                "--tls=false",
                "--storage-driver=overlay2",
            ],
            volumes={volume_name: {"bind": "/var/lib/docker", "mode": "rw"}},
            shm_size="1g",
        )
        logger.info("Started DinD sidecar %s for workspace %s", sidecar_name, ws.id)

    def _build_dind_guard_script(
        self, lan_subnets: list[str] | None, tailscale: bool = False
    ) -> str:
        """The DOCKER-USER iptables script for :meth:`_apply_dind_egress_guard`.

        dockerd may wire DOCKER-USER into either the legacy or nft iptables backend
        (it picks based on the netns's existing rules), and that isn't always the
        backend the sidecar's default ``iptables`` points at. So we probe each
        candidate binary and use the one whose FORWARD chain actually jumps to
        DOCKER-USER — the definitive signal of the live backend — instead of
        assuming ``iptables`` is correct (which silently misses on legacy hosts).

        For Tailscale workspaces we accept anything egressing ``tailscale0`` before
        the DROPs — mirroring rule #2 of :meth:`_build_egress_rules`. Without it the
        ``100.64.0.0/10`` DROP would swallow nested containers' queries to Tailscale
        MagicDNS (``100.100.100.100``, routed out ``tailscale0``), which the
        workspace's Tailscale-generated ``resolv.conf`` hands them — so nested
        ``docker build`` / ``apt`` would fail to resolve any mirror."""
        blocked = list(_ALWAYS_BLOCK)
        allowed = lan_subnets or []
        lan_block = [c for c in _LAN_BLOCK if c not in allowed]
        parts = [
            'end=$(($(date +%s)+30)); IPT=""',
            "while [ $(date +%s) -lt $end ]; do "
            "for c in iptables-legacy iptables-nft iptables; do "
            "$c -S FORWARD 2>/dev/null | grep -q DOCKER-USER && { IPT=$c; break; }; "
            "done; [ -n \"$IPT\" ] && break; sleep 2; done",
            '[ -z "$IPT" ] && exit 1',
            "$IPT -A DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
        ]
        if tailscale:
            parts.append("$IPT -A DOCKER-USER -o tailscale0 -j ACCEPT")
        for cidr in allowed:
            parts.append(f"$IPT -A DOCKER-USER -d {cidr} -j ACCEPT")
        for cidr in blocked + lan_block:
            parts.append(f"$IPT -A DOCKER-USER -d {cidr} -j DROP")
        return " ; ".join(parts)

    def _apply_dind_egress_guard(
        self, ws_id: int, lan_subnets: list[str] | None, tailscale: bool = False
    ) -> None:
        """Extend the egress policy to containers the nested daemon runs.

        The per-workspace OUTPUT guard only filters locally-generated traffic;
        containers started inside the DinD daemon egress via the kernel FORWARD
        path, which OUTPUT rules don't see. Docker reserves the DOCKER-USER chain
        for exactly this — admin filters evaluated before its own FORWARD rules.
        We wait for the nested daemon to create that chain, then drop the same
        metadata/Docker-internal/LAN ranges (honouring admin-granted subnets), so
        nested containers inherit the workspace's egress policy.

        Runs *inside the DinD sidecar itself* rather than a helper container: the
        sidecar's ``iptables`` is auto-selected (legacy vs nft) by its entrypoint
        to match the backend ``dockerd`` used for DOCKER-USER, so the rules always
        land in the right table. A helper image (e.g. nft-only netshoot) can miss a
        legacy DOCKER-USER chain entirely and silently leave nested traffic
        unfiltered. Best-effort."""
        script = self._build_dind_guard_script(lan_subnets, tailscale=tailscale)
        try:
            sidecar = self._client.containers.get(self._dind_sidecar_name(ws_id))
            code, out = sidecar.exec_run(["/bin/sh", "-c", script])
            if code == 0:
                logger.info("Applied DinD egress guard for workspace %s", ws_id)
            else:
                logger.warning(
                    "DinD egress guard exit %s for workspace %s: %s",
                    code, ws_id, (out or b"").decode(errors="ignore")[:300],
                )
        except Exception as exc:
            logger.warning("DinD egress guard failed for workspace %s: %s", ws_id, exc)

    @staticmethod
    def _build_hardening(*, no_new_privileges_setting: bool, allow_sudo: bool) -> dict:
        """Build the container hardening kwargs (cap_drop/cap_add + security_opt).

        no-new-privileges blocks in-container sudo (setuid). It is applied when the
        admin global setting forces it OR when the workspace itself does not request
        sudo. So: allow_sudo=True + setting False => sudo works; setting True =>
        sudo always disabled regardless of the workspace.
        """
        hardening: dict = {
            "cap_drop": ["ALL"],
            "cap_add": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID", "KILL"],
        }
        if no_new_privileges_setting or not allow_sudo:
            hardening["security_opt"] = ["no-new-privileges:true"]
        return hardening

    @staticmethod
    def _apply_package_env(env: dict, install_packages: str | None) -> None:
        """Wire distro packages into ``env`` via the universal-package-install Mod.

        Mutates ``env`` in place. No-op when there are no packages.
        """
        pkgs = _split_packages(install_packages)
        if not pkgs:
            return
        mod = "linuxserver/mods:universal-package-install"
        existing = env.get("DOCKER_MODS")
        env["DOCKER_MODS"] = f"{existing}|{mod}" if existing else mod
        env["INSTALL_PACKAGES"] = "|".join(pkgs)

    @staticmethod
    def _apply_proot_apps(env: dict, volumes: dict, proot_apps: str | None) -> None:
        """Wire proot-apps into ``env``/``volumes``.

        Sets PROOT_APPS (space-separated) and mounts the install init script.
        Mutates both dicts in place. No-op when there are no apps.
        """
        apps = _split_packages(proot_apps)
        if not apps:
            return
        env["PROOT_APPS"] = " ".join(apps)
        volumes[_helper_script_path("install-proot-apps.sh")] = {
            "bind": "/custom-cont-init.d/98-install-proot-apps.sh",
            "mode": "ro",
        }

    @staticmethod
    def _apply_appimages(env: dict, volumes: dict, appimages: str | None) -> None:
        """Wire AppImage app URLs into ``env``/``volumes``.

        Sets COVE_APPIMAGES (space-separated URLs) and mounts the install init
        script, which downloads + extracts each AppImage and writes a desktop
        launcher. Mutates both dicts in place. No-op when there are no URLs.
        """
        urls = _split_packages(appimages)
        if not urls:
            return
        env["COVE_APPIMAGES"] = " ".join(urls)
        volumes[_helper_script_path("install-appimages.sh")] = {
            "bind": "/custom-cont-init.d/97-install-appimages.sh",
            "mode": "ro",
        }

    @staticmethod
    def _apply_username(env: dict, volumes: dict, ws) -> None:
        """Make the desktop user show the owner's Cove username.

        LinuxServer images hardwire the 'abc' user by name throughout their own
        s6 services, so a true rename breaks the desktop. Instead we pass the
        Cove username and mount an init script that adds an alias passwd/group
        entry sharing abc's UID/GID — so whoami / the shell prompt / ``ls -l``
        show the Cove name while ``s6-setuidgid abc`` keeps working. Mutates both
        dicts in place. No-op for the reserved 'abc'/'root' names. Runs early
        (01-) so later init scripts (e.g. ssh-key) see the alias in place.
        """
        user = ws.user
        if not user or not user.username or user.username in ("abc", "root"):
            return
        env["COVE_USERNAME"] = user.username
        volumes[_helper_script_path("install-username.sh")] = {
            "bind": "/custom-cont-init.d/01-install-username.sh",
            "mode": "ro",
        }

    @staticmethod
    def _apply_ssh_key(ws, volumes: dict) -> None:
        """Inject the owner's account SSH key into the container's ~/.ssh.

        Stages the decrypted key to a host dir and mounts it read-only alongside
        an init script that copies it into /config/.ssh with strict perms. No-op
        when the workspace opts out or the owner has no key on file. Mutates
        ``volumes`` in place.
        """
        if not ws.inject_ssh_key:
            return
        user = ws.user
        if not user or not user.ssh_private_key:
            return
        private = decrypt_secret(user.ssh_private_key)
        if not private:
            return
        key_dir = _stage_ssh_key(
            ws.id, private, user.ssh_public_key or "", user.ssh_key_type or "ed25519"
        )
        volumes[key_dir] = {"bind": "/cove/ssh-key", "mode": "ro"}
        volumes[_helper_script_path("install-ssh-key.sh")] = {
            "bind": "/custom-cont-init.d/96-install-ssh-key.sh",
            "mode": "ro",
        }

    @staticmethod
    def _apply_browser_lock_cleanup(ws, volumes: dict) -> None:
        """Clear a stale browser single-instance lock before the browser starts.

        Opt-in per workspace: mounts an init script that removes a leftover
        SingletonLock (chromium family) / lock (Firefox) from the persistent
        /config profile, which an unclean halt leaves behind and which otherwise
        stops the browser from launching on the next boot. Mutates ``volumes``.
        """
        if not getattr(ws, "clear_browser_lock", False):
            return
        volumes[_helper_script_path("clear-browser-lock.sh")] = {
            "bind": "/custom-cont-init.d/95-clear-browser-lock.sh",
            "mode": "ro",
        }

    @staticmethod
    def _apply_cove_theme(volumes: dict) -> None:
        """Restyle the in-stream Selkies dashboard/menu with Cove's cyberpunk theme.

        Mounts an init script that appends Cove's neon palette over the Selkies
        dashboard's CSS variables. Mounted unconditionally: the script self-guards
        on the dashboard files existing, so it's a harmless no-op on any
        non-Selkies image. Mutates ``volumes`` in place.
        """
        volumes[_helper_script_path("install-cove-theme.sh")] = {
            "bind": "/custom-cont-init.d/50-cove-theme.sh",
            "mode": "ro",
        }

    @staticmethod
    def _apply_mate_theme_fix(volumes: dict) -> None:
        """Make GTK apps honor MATE's dark/light theme in MATE desktop workspaces.

        The LinuxServer base runs its own xsettingsd (the only working XSETTINGS
        manager on MATE) but never sets a theme name, so GTK app content ignores
        MATE Appearance and renders light — "dark mode reverts to white". Mounts an
        init script that installs a per-session agent syncing the chosen MATE theme
        into xsettingsd. Mounted unconditionally: the script self-guards on MATE +
        xsettingsd + X11, so it's a harmless no-op on any other image. Mutates
        ``volumes`` in place.
        """
        volumes[_helper_script_path("fix-mate-xsettings.sh")] = {
            "bind": "/custom-cont-init.d/51-mate-xsettings.sh",
            "mode": "ro",
        }

    @staticmethod
    def _build_traefik_labels(
        ws: Workspace, image: WorkspaceImage, network_name: str
    ) -> dict:
        """Build Traefik labels for a workspace container.

        Router/service/middleware names use the stable, label-safe integer id;
        the public_id is only used in the user-facing route path/host.

        Subdomain mode (COVE_WORKSPACE_DOMAIN set): the app is served at ``/`` on
        its own host ``{public_id}.{domain}`` — no stripprefix. Subpath mode
        (unset): the current PathPrefix + stripprefix behavior is preserved.
        """
        settings = get_settings()
        ws_id = ws.id
        name = f"cove-ws-{ws_id}"

        # Per-workspace headers middleware: the stream must be embeddable in the
        # SPA's iframe. In subdomain mode the iframe is cross-origin, so we strip
        # any upstream X-Frame-Options (which would block it) and instead allow
        # framing only from the SPA origin via CSP frame-ancestors.
        hdr = f"{name}-hdr"
        frame_ancestors = "frame-ancestors 'self'"
        if settings.app_origin:
            frame_ancestors += f" {settings.app_origin}"
        header_labels = {
            f"traefik.http.middlewares.{hdr}.headers.customResponseHeaders.X-Frame-Options": "",
            f"traefik.http.middlewares.{hdr}.headers.contentSecurityPolicy": frame_ancestors,
            f"traefik.http.middlewares.{hdr}.headers.contentTypeNosniff": "true",
        }

        base = {
            "traefik.enable": "true",
            f"traefik.http.routers.{name}.entrypoints": "web,websecure",
            f"traefik.http.routers.{name}.service": name,
            f"traefik.http.services.{name}.loadbalancer.server.port": str(image.internal_port),
            "traefik.docker.network": network_name,
            "cove.workspace_id": str(ws_id),
            "cove.user_id": str(ws.user_id),
            **header_labels,
        }

        if settings.workspace_domain:
            host = settings.workspace_host(ws.public_id)
            base[f"traefik.http.routers.{name}.rule"] = f"Host(`{host}`)"
            base[f"traefik.http.routers.{name}.middlewares"] = (
                f"cove-errors@docker,cove-auth@docker,{hdr}"
            )
            # Only request TLS termination in prod/HTTPS deployments.
            if settings.cookie_secure:
                base[f"traefik.http.routers.{name}.tls"] = "true"
            return base

        prefix = f"/workspace/{ws.public_id}"
        base[f"traefik.http.routers.{name}.rule"] = f"PathPrefix(`{prefix}/`)"
        base[f"traefik.http.routers.{name}.middlewares"] = (
            f"cove-errors@docker,cove-auth@docker,{hdr},{name}-strip"
        )
        base[f"traefik.http.middlewares.{name}-strip.stripprefix.prefixes"] = prefix
        return base


_managers: dict[int, DockerManager] = {}
_managers_lock = Lock()


def get_docker_manager(zone_id: int = 0) -> DockerManager:
    """Return the cached DockerManager for a zone (one client per zone).

    zone_id 0 is the local control-plane daemon. Callers tied to a workspace pass
    ``ws.zone_id`` so every container operation lands on the node the workspace is
    pinned to.
    """
    with _managers_lock:
        mgr = _managers.get(zone_id)
        if mgr is None:
            mgr = DockerManager(zone_id=zone_id)
            _managers[zone_id] = mgr
        return mgr


def reset_docker_manager(zone_id: int | None = None) -> None:
    """Drop cached manager(s) so the next call rebuilds the client — used when a
    zone's endpoint or mTLS certs change (re-enrollment, rotation)."""
    with _managers_lock:
        if zone_id is None:
            _managers.clear()
        else:
            _managers.pop(zone_id, None)
