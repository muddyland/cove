import re as _re
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Sentinel used to distinguish an omitted field from one explicitly set to clear.
_UNSET = "\x00__unset__\x00"

# ── Auth ──────────────────────────────────────────────────────────────────────

class AuthConfig(BaseModel):
    oidc_enabled: bool
    oidc_provider_name: str
    needs_setup: bool
    oidc_only: bool = False


class SetupRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    username: str
    is_admin: bool
    docker_allowed: bool = False
    # False: every zone. True: only allowed_zone_ids (see Admin -> Users).
    zones_restricted: bool = False
    allowed_zone_ids: list[int] = []
    auth_provider: str
    created_at: datetime
    last_login_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# ── Workspace Images ──────────────────────────────────────────────────────────

class ImageCreate(BaseModel):
    name: str
    docker_image: str
    image_type: str = "desktop"
    description: Optional[str] = None
    internal_port: int = 3000
    url_env: Optional[str] = None
    logo_url: Optional[str] = None


class ImageUpdate(BaseModel):
    name: Optional[str] = None
    docker_image: Optional[str] = None
    image_type: Optional[str] = None
    description: Optional[str] = None
    internal_port: Optional[int] = None
    url_env: Optional[str] = None
    logo_url: Optional[str] = None
    enabled: Optional[bool] = None


class BrowserFeatures(BaseModel):
    """Startup options this browser image can honour (see catalog.browser_features).
    The launcher hides the ones it can't, rather than passing a flag the browser
    ignores."""

    kiosk: bool = False  # --kiosk: full-screen with the UI locked away
    fullscreen: bool = False  # --start-fullscreen: full-screen, menu kept
    dark: bool = False  # forced dark page rendering


class ImageOut(BaseModel):
    id: int
    name: str
    docker_image: str
    image_type: str
    description: Optional[str]
    enabled: bool
    internal_port: int = 3000
    url_env: Optional[str] = None
    logo_url: Optional[str] = None
    created_at: datetime
    browser: BrowserFeatures = BrowserFeatures()

    model_config = {"from_attributes": True}

    @classmethod
    def from_image(cls, image) -> "ImageOut":
        from server.catalog import browser_features

        out = cls.model_validate(image)
        out.browser = BrowserFeatures(**browser_features(image.url_env))
        return out


# ── Workspaces ────────────────────────────────────────────────────────────────

class WorkspaceCreate(BaseModel):
    name: str
    image_id: int
    # Which zone (node) to run the workspace on. 0 = the local control-plane
    # daemon (default). Must reference an enrolled zone.
    zone_id: int = 0
    workspace_type: str = "desktop"
    target_url: Optional[str] = None
    kiosk: bool = False
    kiosk_dark: bool = False
    kiosk_menu: bool = False
    use_tailscale: bool = False
    # Route egress through the user's Gluetun VPN (mutually exclusive with Tailscale).
    use_gluetun: bool = False
    # Which saved profile to use; omitted -> the user's first one.
    tailscale_profile_id: Optional[int] = None
    gluetun_profile_id: Optional[int] = None
    # Ephemeral: no persistent storage — nothing is saved between sessions.
    ephemeral: bool = False
    # Delete the workspace record once its container stops (requires ephemeral).
    auto_remove: bool = False
    # Opt-in for direct LAN egress (only effective when the admin enables LAN
    # access and configures subnets). Tailnet-routed LAN is independent of this.
    lan_access: bool = False
    # Per-workspace Tailscale routing options (stored regardless of use_tailscale).
    ts_exit_node: Optional[str] = None
    ts_accept_routes: bool = True
    ts_accept_dns: bool = True
    # Use custom (public) DNS resolvers; dns_servers is a space/comma list of IPs.
    custom_dns: bool = False
    dns_servers: Optional[str] = None
    # Per-workspace package installation + sudo control.
    install_packages: Optional[str] = None
    proot_apps: Optional[str] = None
    appimages: Optional[str] = None
    # Default off: workspaces get no-new-privileges (no in-container sudo/setuid
    # escalation) unless the creator explicitly opts in. Shrinks the blast radius
    # of an in-container compromise.
    allow_sudo: bool = False
    # Inject the owner's account SSH key into ~/.ssh (on by default).
    inject_ssh_key: bool = True
    # Stream over Wayland (on by default); off forces the X11 fallback.
    pixelflux_wayland: bool = True
    # Clear a stale browser single-instance lock before launch (opt-in, off).
    clear_browser_lock: bool = False
    # GPU (VAAPI) hardware acceleration for the stream (opt-in, off). Only
    # effective when the admin master toggle is on and the host has a GPU.
    gpu_accel: bool = False
    # Docker-in-Docker: run a privileged nested Docker daemon sidecar for dev
    # container workflows (opt-in, off). Only effective when the admin master
    # toggle (workspace_docker) is on.
    use_docker: bool = False
    # Share the user's persistent /config profile across their shared-profile
    # workspaces (opt-in, off). Survives container/workspace deletion.
    shared_profile: bool = False


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = None
    target_url: Optional[str] = None
    kiosk: Optional[bool] = None
    kiosk_dark: Optional[bool] = None
    kiosk_menu: Optional[bool] = None
    use_tailscale: Optional[bool] = None
    use_gluetun: Optional[bool] = None
    tailscale_profile_id: Optional[int] = None
    gluetun_profile_id: Optional[int] = None
    ephemeral: Optional[bool] = None
    auto_remove: Optional[bool] = None
    lan_access: Optional[bool] = None
    ts_exit_node: Optional[str] = None
    ts_accept_routes: Optional[bool] = None
    ts_accept_dns: Optional[bool] = None
    custom_dns: Optional[bool] = None
    dns_servers: Optional[str] = None
    install_packages: Optional[str] = None
    proot_apps: Optional[str] = None
    appimages: Optional[str] = None
    allow_sudo: Optional[bool] = None
    inject_ssh_key: Optional[bool] = None
    pixelflux_wayland: Optional[bool] = None
    clear_browser_lock: Optional[bool] = None
    gpu_accel: Optional[bool] = None
    use_docker: Optional[bool] = None
    shared_profile: Optional[bool] = None


class LanPolicyOut(BaseModel):
    # Admin master toggle for direct (raw-bridge) LAN egress, plus the CIDRs a
    # workspace may reach when it opts in. Surfaced to non-admin users so the
    # launch/edit modals can show the per-workspace checkbox + its ranges.
    enabled: bool
    subnets: list[str]


class GpuPolicyOut(BaseModel):
    # Whether GPU (VAAPI) hardware acceleration is on for the requested zone (the
    # admin master toggle, or a remote zone's own override). Surfaced to
    # non-admin users so the launch/edit modals only show the per-workspace GPU
    # checkbox where an admin has enabled it.
    enabled: bool


class DockerPolicyOut(BaseModel):
    # Whether Docker-in-Docker is available on the requested zone (the admin
    # master toggle, plus the zone's own opt-in on a remote zone). Surfaced to
    # non-admin users so the launch/edit modals only show the per-workspace
    # "Docker (dev)" checkbox where an admin has enabled the privileged feature.
    enabled: bool


class WorkspaceClone(BaseModel):
    # New name for the copy (must differ from the source). Optional image_id lets
    # you clone onto a different distro while keeping the persistent home.
    name: str
    image_id: Optional[int] = None


class WorkspaceMigrate(BaseModel):
    # The zone to move the (stopped) workspace to. Its /config is copied to the
    # destination, the pin is flipped, and the source copy is removed.
    target_zone_id: int


class StreamAuthOut(BaseModel):
    # The URL the SPA should point the workspace iframe at. In subdomain mode it
    # carries a one-time ``?__cove_t`` token that bootstraps the per-workspace
    # stream cookie; in subpath mode it is the plain same-origin stream path.
    url: str


class StreamReadyOut(BaseModel):
    # True once Traefik actually has a router for this workspace's stream, so the
    # iframe URL won't 404. False during the brief window after a workspace flips
    # to "running" but before Traefik picks up its route (instant for local
    # workspaces, up to one HTTP-provider poll for remote-zone ones).
    ready: bool


class StreamLeaseIn(BaseModel):
    # Random id of one open stream page (per page load), from the SPA.
    viewer_id: str = Field(..., min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$")


class StreamLeaseOut(BaseModel):
    # Whether the asking page still holds the workspace's stream. False once
    # another browser or thin client opened it (Selkies disconnected this one).
    holder: bool
    # Where the holder is ("Desk (thin client)", "Firefox on Linux") and since when.
    label: Optional[str] = None
    since: Optional[datetime] = None


class WorkspaceStats(BaseModel):
    cpu_pct: float
    mem_used: int
    mem_limit: int
    mem_pct: float
    # Present only for Tailscale-routed workspaces once the sidecar has joined
    # the tailnet; None otherwise.
    tailscale_ip: str | None = None


def _pull_progress_for(ws) -> "PullProgressOut | None":
    """In-flight image download for a workspace that is still starting."""
    if ws.status != "creating" or not ws.image:
        return None
    from server.docker_manager import pull_progress

    p = pull_progress(ws.zone_id, ws.image.docker_image)
    return PullProgressOut(**p) if p else None


class PullProgressOut(BaseModel):
    # Bytes downloaded / bytes known so far across all layers. The total grows as
    # the daemon discovers layers, so it is an estimate until the pull finishes.
    current: int
    total: int
    layers: int = 0


class WorkspaceOut(BaseModel):
    id: int
    public_id: str
    user_id: int
    name: str
    status: str
    workspace_type: str
    container_id: Optional[str]
    container_name: Optional[str]
    zone_id: int
    zone_name: Optional[str]
    image_id: int
    image_name: str
    image_logo: Optional[str]
    target_url: Optional[str]
    kiosk: bool
    kiosk_dark: bool
    kiosk_menu: bool
    use_tailscale: bool
    use_gluetun: bool
    tailscale_profile_id: Optional[int] = None
    gluetun_profile_id: Optional[int] = None
    # Name of the profile the routing sidecar uses (for the card/detail view).
    routing_profile_name: Optional[str] = None
    ephemeral: bool
    auto_remove: bool
    lan_access: bool
    ts_exit_node: Optional[str]
    ts_accept_routes: bool
    ts_accept_dns: bool
    custom_dns: bool
    dns_servers: Optional[str]
    install_packages: Optional[str]
    proot_apps: Optional[str]
    appimages: Optional[str]
    allow_sudo: bool
    inject_ssh_key: bool
    pixelflux_wayland: bool
    clear_browser_lock: bool
    gpu_accel: bool
    use_docker: bool
    shared_profile: bool
    stream_url: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    stopped_at: Optional[datetime]
    error_message: Optional[str]
    # When this workspace's screen capture was taken, or None if it has none.
    # The frame itself is served separately (GET /{id}/preview.jpg) so listing
    # workspaces doesn't drag a JPEG per row through the JSON payload; this field
    # is what tells the card whether to request one, and busts its cache.
    preview_at: Optional[datetime]
    # When this workspace's site favicon was fetched, or None if it has none (not
    # a browser workspace, several sites open, or nothing decodable found). Same
    # arrangement as preview_at: the PNG is served from GET /{id}/favicon.png and
    # this field is what tells the UI to ask for it instead of the browser logo.
    favicon_at: Optional[datetime]
    # What startup options this workspace's browser supports, so the edit form can
    # hide the ones it doesn't (empty for a non-browser workspace).
    browser: BrowserFeatures = BrowserFeatures()
    # Running and ready for a client: its stream has produced a first frame (or
    # the fallback window for uncapturable images has passed). Clients wait on
    # this rather than status, since a stream opened too early never recovers.
    connectable: bool = False
    # Set while the workspace's image is being downloaded during a launch, so the
    # UI can say "Downloading image 1.2/4.7 GB" instead of a bare "Starting".
    pull_progress: Optional[PullProgressOut] = None
    # When the user last extended the max-runtime auto-halt (see /extend).
    runtime_extended_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_workspace(cls, ws) -> "WorkspaceOut":
        from server.catalog import browser_features
        from server.config import get_settings
        from server.preview import is_connectable

        stream_url = None
        if ws.status == "running":
            settings = get_settings()
            if settings.workspace_domain:
                # Subdomain mode: protocol-relative absolute origin URL.
                stream_url = f"//{settings.workspace_host(ws.public_id)}/"
            else:
                stream_url = f"/workspace/{ws.public_id}/"
        return cls(
            id=ws.id,
            public_id=ws.public_id,
            user_id=ws.user_id,
            name=ws.name,
            status=ws.status,
            workspace_type=ws.kind,
            container_id=ws.container_id,
            container_name=ws.container_name,
            zone_id=ws.zone_id,
            zone_name=ws.zone.name if ws.zone else None,
            image_id=ws.image_id,
            image_name=ws.image.name if ws.image else "",
            image_logo=ws.image.logo_url if ws.image else None,
            target_url=ws.target_url,
            kiosk=ws.kiosk,
            kiosk_dark=ws.kiosk_dark,
            kiosk_menu=ws.kiosk_menu,
            use_tailscale=ws.use_tailscale,
            use_gluetun=ws.use_gluetun,
            tailscale_profile_id=ws.tailscale_profile_id,
            gluetun_profile_id=ws.gluetun_profile_id,
            routing_profile_name=(
                ws.gluetun_profile.name if ws.use_gluetun and ws.gluetun_profile
                else ws.tailscale_profile.name if ws.use_tailscale and ws.tailscale_profile
                else None
            ),
            ephemeral=ws.ephemeral,
            auto_remove=ws.auto_remove,
            lan_access=ws.lan_access,
            ts_exit_node=ws.ts_exit_node,
            ts_accept_routes=ws.ts_accept_routes,
            ts_accept_dns=ws.ts_accept_dns,
            custom_dns=ws.custom_dns,
            dns_servers=ws.dns_servers,
            install_packages=ws.install_packages,
            proot_apps=ws.proot_apps,
            appimages=ws.appimages,
            allow_sudo=ws.allow_sudo,
            inject_ssh_key=ws.inject_ssh_key,
            pixelflux_wayland=ws.pixelflux_wayland,
            clear_browser_lock=ws.clear_browser_lock,
            gpu_accel=ws.gpu_accel,
            use_docker=ws.use_docker,
            shared_profile=ws.shared_profile,
            stream_url=stream_url,
            created_at=ws.created_at,
            started_at=ws.started_at,
            stopped_at=ws.stopped_at,
            error_message=ws.error_message,
            preview_at=ws.preview_at,
            favicon_at=ws.favicon_at,
            connectable=is_connectable(ws),
            browser=BrowserFeatures(**browser_features(ws.image.url_env if ws.image else None)),
            pull_progress=_pull_progress_for(ws),
            runtime_extended_at=getattr(ws, "runtime_extended_at", None),
        )


# ── Admin ─────────────────────────────────────────────────────────────────────

class AdminUserCreate(BaseModel):
    username: str
    password: str
    is_admin: bool = False
    docker_allowed: bool = False
    zones_restricted: bool = False
    allowed_zone_ids: list[int] = []


class AdminUserUpdate(BaseModel):
    username: Optional[str] = None
    is_admin: Optional[bool] = None
    password: Optional[str] = None
    docker_allowed: Optional[bool] = None
    # Both optional: send either or both to change the user's zone limit.
    zones_restricted: Optional[bool] = None
    allowed_zone_ids: Optional[list[int]] = None


# ── App settings ──────────────────────────────────────────────────────────────

class AppSettingsOut(BaseModel):
    tailscale_image: str
    gluetun_image: str
    workspace_lan_access: bool
    workspace_lan_subnets: str
    workspace_no_new_privileges: bool
    workspace_max_runtime_hours: int
    workspace_cpu_limit: float
    workspace_memory_limit_mb: int
    workspace_pids_limit: int = 8192
    workspace_gpu_accel: bool
    workspace_gpu_render_node: str
    workspace_gpu_render_gid: int
    workspace_docker: bool
    dind_image: str
    trash_retention_days: int


class EnvEntry(BaseModel):
    name: str
    value: str


class EnvSummaryOut(BaseModel):
    entries: list[EnvEntry]


# ── Storage / disk management ───────────────────────────────────────────────

class StorageCategory(BaseModel):
    key: str
    label: str
    total: int
    active: int
    size: int
    reclaimable: int


class HostDisk(BaseModel):
    total: int
    used: int
    free: int


class ZoneStorageOut(BaseModel):
    zone_id: int
    zone_name: str
    online: bool
    error: Optional[str] = None
    host: Optional[HostDisk] = None
    categories: list[StorageCategory] = []


class StorageOut(BaseModel):
    zones: list[ZoneStorageOut]


class PruneRequest(BaseModel):
    zone_id: int = 0
    deep: bool = False


class PruneResultOut(BaseModel):
    zone_id: int
    deep: bool
    space_reclaimed: int


class AppSettingsUpdate(BaseModel):
    tailscale_image: Optional[str] = None
    gluetun_image: Optional[str] = None
    workspace_lan_access: Optional[bool] = None
    workspace_lan_subnets: Optional[str] = None
    workspace_no_new_privileges: Optional[bool] = None
    workspace_max_runtime_hours: Optional[int] = None
    # allow_inf_nan=False: "Infinity" would otherwise be stored and break every
    # launch (int(inf * 1e9) raises) until an admin notices.
    workspace_cpu_limit: Optional[float] = Field(default=None, allow_inf_nan=False, ge=0, le=4096)
    workspace_memory_limit_mb: Optional[int] = Field(default=None, ge=0, le=64 * 1024 * 1024)
    workspace_pids_limit: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    workspace_gpu_accel: Optional[bool] = None
    workspace_gpu_render_node: Optional[str] = None
    workspace_gpu_render_gid: Optional[int] = None
    workspace_docker: Optional[bool] = None
    dind_image: Optional[str] = None
    trash_retention_days: Optional[int] = None


# ── Tailscale ─────────────────────────────────────────────────────────────────

class TailscaleProfileOut(BaseModel):
    # Never returns the auth key, only whether one is stored.
    id: int
    name: str
    has_auth_key: bool
    login_server: Optional[str]
    # Why it can't be used yet (None when ready), and which workspaces use it.
    problem: Optional[str]
    used_by: list[str]


class TailscaleProfileIn(BaseModel):
    # auth_key uses a sentinel default: an omitted field leaves the stored key
    # unchanged; an explicit "" (or null) clears it; a non-empty string replaces it.
    name: Optional[str] = None
    auth_key: Optional[str] = _UNSET
    login_server: Optional[str] = _UNSET


# ── Gluetun (per-user VPN profiles) ─────────────────────────────────────────────

class VpnProviderOut(BaseModel):
    value: str
    label: str
    types: list[str]
    wg_addresses: bool
    wg_preshared_key: bool


class VpnProfileOut(BaseModel):
    # Never returns the config file or secrets — only presence flags. Server
    # filters and WireGuard addresses aren't secret and come back as-is.
    id: int
    name: str
    mode: str
    provider: Optional[str]
    provider_label: Optional[str]
    vpn_type: str
    has_config: bool
    config_filename: Optional[str]
    has_wireguard_private_key: bool
    has_wireguard_preshared_key: bool
    wireguard_addresses: Optional[str]
    has_openvpn_user: bool
    has_openvpn_password: bool
    server_countries: Optional[str]
    server_cities: Optional[str]
    server_hostnames: Optional[str]
    problem: Optional[str]
    used_by: list[str]
    # Name of the running/starting workspace holding this profile's connection.
    active_in: Optional[str]


class VpnProfileIn(BaseModel):
    # Each secret/file uses the sentinel default: omitted -> unchanged;
    # "" or null -> clear; a value -> replace. All stored encrypted at rest.
    name: Optional[str] = None
    mode: Optional[str] = None
    provider: Optional[str] = _UNSET
    vpn_type: Optional[str] = None
    config_file: Optional[str] = _UNSET
    config_filename: Optional[str] = _UNSET
    wireguard_private_key: Optional[str] = _UNSET
    wireguard_preshared_key: Optional[str] = _UNSET
    wireguard_addresses: Optional[str] = _UNSET
    openvpn_user: Optional[str] = _UNSET
    openvpn_password: Optional[str] = _UNSET
    server_countries: Optional[str] = _UNSET
    server_cities: Optional[str] = _UNSET
    server_hostnames: Optional[str] = _UNSET


# ── SSH key (per-user) ──────────────────────────────────────────────────────────

class SshKeyOut(BaseModel):
    # Never returns the private key — only the public key + metadata.
    has_key: bool
    public_key: Optional[str]
    key_type: Optional[str]
    fingerprint: Optional[str]


class SshKeyUpdate(BaseModel):
    # Upload an existing private key. "" or null clears the stored key.
    private_key: Optional[str] = None


# ── Diagnostics ───────────────────────────────────────────────────────────────

class TailscaleStatusOut(BaseModel):
    # Output of `tailscale status` from the workspace's Tailscale sidecar.
    available: bool  # False when the sidecar is missing / not running
    output: str


# ── System info (stream page) ───────────────────────────────────────────────────

class SysContainerOut(BaseModel):
    id: str
    name: str
    state: Optional[str]
    started_at: Optional[datetime]
    restart_count: int
    image_id: str
    cpu_limit: Optional[float]  # cores
    mem_limit: Optional[int]  # bytes
    pids_limit: Optional[int]
    shm_size: Optional[int]
    privileged: bool
    no_new_privileges: bool


class SysResourcesOut(BaseModel):
    cpu_pct: Optional[float] = None
    mem_used: Optional[int] = None
    mem_limit: Optional[int] = None
    mem_pct: Optional[float] = None
    net_rx: Optional[int] = None
    net_tx: Optional[int] = None
    blk_read: Optional[int] = None
    blk_write: Optional[int] = None
    pids: Optional[int] = None
    pids_limit: Optional[int] = None
    online_cpus: Optional[int] = None


class SysSidecarOut(BaseModel):
    name: str
    state: Optional[str]
    health: Optional[str]  # gluetun: starting / healthy / unhealthy; tailscale: None
    started_at: Optional[datetime]
    restart_count: int


class SysNetworkOut(BaseModel):
    mode: str  # direct | tailscale | gluetun
    profile_name: Optional[str] = None
    # VPN profile summary (never secrets).
    vpn_provider: Optional[str] = None
    vpn_type: Optional[str] = None
    vpn_servers: Optional[str] = None
    # Tailscale.
    tailscale_ip: Optional[str] = None
    tailscale_hostname: Optional[str] = None
    login_server: Optional[str] = None
    exit_node: Optional[str] = None
    accept_routes: Optional[bool] = None
    accept_dns: Optional[bool] = None
    # Direct.
    bridge_ip: Optional[str] = None
    custom_dns: Optional[str] = None
    lan_access: bool = False
    lan_subnets: list[str] = []
    sidecar: Optional[SysSidecarOut] = None


class SysStorageOut(BaseModel):
    kind: str  # persistent | ephemeral | shared
    measurable: bool  # whether /disk-usage can size it (local zone, persistent)


class SystemInfoOut(BaseModel):
    status: str
    zone_name: Optional[str]
    image_name: str
    image_ref: Optional[str]
    started_at: Optional[datetime]
    auto_stop_at: Optional[datetime]
    container: Optional[SysContainerOut]
    resources: Optional[SysResourcesOut]
    network: SysNetworkOut
    storage: SysStorageOut
    # Per-workspace options that change how it runs.
    features: dict[str, bool]


class DiskUsageOut(BaseModel):
    bytes: int
    files: int
    partial: bool  # stopped counting at the time budget: the real total is larger


class PublicIpOut(BaseModel):
    ip: str
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    org: Optional[str] = None
    timezone: Optional[str] = None
    source: str = "ipinfo.io"


class ContainerLogsOut(BaseModel):
    source: str  # 'desktop' | 'tailscale' | 'gluetun'
    available: bool  # False when the backing container is missing
    output: str


# ── proot-apps ────────────────────────────────────────────────────────────────

class ProotAppOut(BaseModel):
    # Default-repository app name; None for an app installed from another image.
    name: Optional[str]
    full_name: Optional[str] = None  # upstream display name
    icon_url: Optional[str] = None
    folder: str
    installed: bool
    downloading: bool
    # In the workspace's saved proot_apps list (reinstalled on boot/migration).
    in_config: bool
    installed_digest: Optional[str]
    latest_digest: Optional[str]
    # None when it couldn't be determined (not checked, registry unreachable, …).
    update_available: Optional[bool]


class ProotAppsOut(BaseModel):
    available: bool  # proot-apps exists in this image
    arch: Optional[str]
    checked: bool  # whether registry update checks ran
    check_failed: bool = False  # some catalog app's latest build couldn't be resolved
    apps: list[ProotAppOut]


class ProotTaskCreate(BaseModel):
    op: str = Field(pattern="^(install|update|remove)$")
    apps: list[str] = Field(min_length=1, max_length=50)


class ProotTaskOut(BaseModel):
    id: str
    kind: str = "proot"  # proot | appimage
    op: str
    state: str  # queued | running | done | failed | interrupted
    exit_code: Optional[int]
    apps: list[str]
    failed_apps: list[str]
    current_app: Optional[str]
    done_count: int
    created_at: Optional[int]  # epoch seconds, from the workspace clock
    started_at: Optional[int]
    finished_at: Optional[int]
    # Queued by the workspace coming up (the saved apps it was missing), not by
    # a user action.
    boot: bool = False


class WorkspaceProotTasksOut(BaseModel):
    workspace_id: int
    workspace_name: str
    tasks: list[ProotTaskOut]


class ProotTaskLogOut(BaseModel):
    output: str


# ── AppImages ─────────────────────────────────────────────────────────────────

class ExtensionOut(BaseModel):
    """The browser extension this deployment ships (Preferences → Browser extension)."""

    available: bool
    version: Optional[str]
    size_bytes: int
    filename: str


class AppImageOut(BaseModel):
    slug: str  # install directory, derived from the URL it came from
    name: str
    # Where it was downloaded from. None for one installed before Cove recorded
    # provenance — updating that one needs a URL from the user.
    url: Optional[str]
    size_kb: int
    installed_at: Optional[int]  # epoch seconds, from the workspace clock
    # False for one in the saved list that isn't on disk (a migration doesn't
    # copy app trees, or its first-boot install failed).
    installed: bool = True


class AppImagesOut(BaseModel):
    apps: list[AppImageOut]


class AppImageTaskCreate(BaseModel):
    op: str = Field(pattern="^(install|update|remove)$")
    # install: the URLs to download. update: `slug` plus the `url` that replaces
    # it. remove: the slugs to delete.
    urls: list[str] = Field(default_factory=list, max_length=10)
    slug: Optional[str] = None
    url: Optional[str] = None
    slugs: list[str] = Field(default_factory=list, max_length=20)


# ── Files ─────────────────────────────────────────────────────────────────────

class FileEntry(BaseModel):
    name: str
    type: str  # 'dir' | 'file'
    size: int
    modified: datetime


class FileListing(BaseModel):
    path: str
    entries: list[FileEntry]


class FileOp(BaseModel):
    # src: path (relative to the user's storage root) of the item to copy/move.
    # dst_dir: destination directory (its own root = ""). Name is preserved and
    # suffixed on collision — copy/move never overwrite.
    src: str
    dst_dir: str = ""


class FilePathIn(BaseModel):
    path: str


class TrashEntryOut(BaseModel):
    id: int
    zone_id: int
    original_path: str
    name: str
    is_dir: bool
    size: int
    deleted_at: datetime
    expires_at: datetime

    model_config = {"from_attributes": True}


# ── Zones ─────────────────────────────────────────────────────────────────────

class ZoneCreate(BaseModel):
    name: str
    # Manual registration: provide the agent's single mTLS port directly. The
    # token enrollment flow sets it from the agent's report instead.
    endpoint_host: Optional[str] = None
    endpoint_port: int = 8443


class ZoneUpdate(BaseModel):
    name: Optional[str] = None
    endpoint_host: Optional[str] = None
    endpoint_port: Optional[int] = None
    # Per-zone GPU overrides. Sending null clears an override, so the zone
    # inherits the global value from Administration → Settings again.
    gpu_accel: Optional[bool] = None
    gpu_render_node: Optional[str] = None
    gpu_render_gid: Optional[int] = Field(default=None, ge=0, le=65535)
    # Allow Docker-in-Docker on this zone (also needs the global master toggle
    # and the agent host's own opt-in, which re-running the installer sets).
    docker_enabled: Optional[bool] = None


class ZoneEnrollTokenOut(BaseModel):
    # Returned once when an admin mints an enrollment token. The plaintext token
    # is never stored (only its hash) — this is the only time it is shown.
    token: str
    expires_at: datetime
    install_command: str


class ZoneEnrollRequest(BaseModel):
    # Sent by the install script. The agent generates its server keypair + CSR
    # locally (private key never leaves the host) and reports the endpoint the
    # control plane should dial.
    csr_pem: str
    endpoint_host: str
    endpoint_port: int = 8443


class ZoneEnrollResponse(BaseModel):
    # The CA (so the agent trusts the control plane's client cert) and the agent's
    # signed server certificate.
    ca_cert_pem: str
    server_cert_pem: str
    # Provisioned so the agent's Traefik can ForwardAuth-validate stream tokens
    # locally (the stream-signing key is NOT the app secret) and resolve a
    # workspace public_id from its subdomain host.
    stream_signing_key: str
    workspace_domain: Optional[str] = None
    # The only client-cert CN the agent should accept (this zone's control-plane
    # cert) — pins the zone to its control plane.
    expected_client_cn: str
    # CN of the relay (edge) cert the central Traefik presents on stream routes.
    # Informational for the agent: its API/Docker paths accept only expected_client_cn.
    edge_client_cn: Optional[str] = None


class ZoneListItem(BaseModel):
    # Minimal, non-sensitive zone info for the launch/migrate pickers (any user).
    id: int
    name: str


class ZoneOut(BaseModel):
    id: int
    public_id: str
    name: str
    status: str
    endpoint_host: Optional[str]
    endpoint_port: int
    enrolled_at: Optional[datetime]
    last_seen_at: Optional[datetime]
    created_at: datetime
    # Number of workspaces pinned to this zone (drives the delete guard + UI).
    workspace_count: int = 0
    # GPU overrides (null = inherit the global setting) and Docker-in-Docker.
    gpu_accel: Optional[bool] = None
    gpu_render_node: Optional[str] = None
    gpu_render_gid: Optional[int] = None
    docker_enabled: bool = False
    # The agent's own DinD opt-in, as last reported (null = unknown).
    agent_allows_docker: Optional[bool] = None

    model_config = {"from_attributes": True}


# ── Audit ─────────────────────────────────────────────────────────────────────

class AuditOut(BaseModel):
    id: int
    ts: datetime
    user_id: Optional[int]
    username: Optional[str]
    action: str
    detail: Optional[str]
    ip: Optional[str]
    # The workspace a workspace.* / admin.session.* entry refers to, resolved
    # from the public_id at the start of ``detail`` (None once it's deleted, or
    # for entries about something else).
    workspace_id: Optional[int] = None
    workspace_name: Optional[str] = None

    model_config = {"from_attributes": True}


class AuditFacetsOut(BaseModel):
    """Distinct values for the audit log filter dropdowns."""
    users: list[str]
    actions: list[str]


class RuntimePolicyOut(BaseModel):
    # Admin max runtime in hours; 0 = unlimited (no auto-halt).
    max_hours: int


class ImageDetailOut(BaseModel):
    # 'present' | 'absent' | 'pulling'
    status: str
    size: Optional[int] = None
    updated_at: Optional[str] = None
    # How many workspaces (any user, any zone) are built on this image.
    workspace_count: int = 0
    pull_progress: Optional[PullProgressOut] = None


class ImageUpdateCheckOut(BaseModel):
    # True = the local copy matches the registry, False = a newer one exists,
    # None = could not tell (not pulled, registry unreachable, unknown format).
    up_to_date: Optional[bool]
    local_digest: Optional[str] = None
    remote_digest: Optional[str] = None
    detail: Optional[str] = None


class ZoneHealthOut(BaseModel):
    ok: bool
    latency_ms: Optional[int] = None
    docker_version: Optional[str] = None
    containers_running: Optional[int] = None
    error: Optional[str] = None
    # Whether the agent allows the Docker-in-Docker sidecar (null for the local
    # zone, or an agent too old to say).
    agent_dind: Optional[bool] = None
    # Whether the agent runs the control plane's own agent image, i.e. needs no
    # Update (null for the local zone, or when it can't be told).
    agent_current: Optional[bool] = None


# ── Thin clients ──────────────────────────────────────────────────────────────

# A bare hostname (optionally a leading "*." wildcard). No scheme, port, path or
# whitespace: these end up in the device's Chromium URL allowlist.
_TC_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_TC_HOST_RE = _re.compile(rf"^(\*\.)?(?=.{{1,253}}$)({_TC_LABEL}\.)*{_TC_LABEL}$")
_TC_HHMM_RE = _re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ThinClientPolicy(BaseModel):
    """What a thin client should do. Every field has a safe default, so a policy
    saved by an older Cove (missing newer fields) still validates."""

    # Display
    screen_off_minutes: int = Field(15, ge=0, le=1440)  # 0 = never
    brightness: Optional[int] = Field(None, ge=0, le=100)  # None = leave alone
    rotation: Literal["normal", "90", "180", "270"] = "normal"
    scale: Optional[float] = Field(None, ge=0.5, le=3.0)
    # Outside these local-time hours the screen goes dark after one idle minute.
    # Both empty = always "on hours".
    on_hours_start: Optional[str] = None
    on_hours_end: Optional[str] = None
    # Audio playing on the device (a film, music) counts as activity.
    media_keeps_awake: bool = True
    # Percent; above 100 PipeWire amplifies (small monitor speakers need it).
    volume: Optional[int] = Field(None, ge=0, le=150)

    # Session
    signout_minutes: int = Field(30, ge=0, le=1440)  # idle sign-out, 0 = never
    session_max_hours: int = Field(12, ge=0, le=720)  # absolute cap, 0 = Cove default
    auto_open: Literal["off", "running", "pinned"] = "running"
    pinned_workspace_id: Optional[str] = Field(None, max_length=64)

    # Browser
    extra_allowed_hosts: list[str] = Field(default_factory=list, max_length=32)
    # The colour scheme pages are told the user prefers (prefers-color-scheme),
    # e.g. so an OIDC sign-in page matches Cove. "system" leaves Chromium's own
    # default, which is light on a kiosk with no desktop settings.
    color_scheme: Literal["dark", "light", "system"] = "dark"

    # Updates
    auto_update: bool = True  # nightly apt full-upgrade in the maintenance hour
    maintenance_hour: int = Field(4, ge=0, le=23)
    auto_reboot: bool = False  # reboot in the maintenance hour if an update needs it

    @field_validator("on_hours_start", "on_hours_end")
    @classmethod
    def _hhmm(cls, v):
        if v in (None, ""):
            return None
        if not _TC_HHMM_RE.match(v):
            raise ValueError("must be HH:MM (24-hour)")
        return v

    @field_validator("extra_allowed_hosts")
    @classmethod
    def _hosts(cls, v):
        out: list[str] = []
        for h in v:
            h = (h or "").strip().lower().rstrip(".")
            if not h:
                continue
            if not _TC_HOST_RE.match(h):
                raise ValueError(f"'{h}' is not a hostname (no scheme, port or path)")
            if h not in out:
                out.append(h)
        return out


class ThinClientCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    location: Optional[str] = Field(None, max_length=256)


class ThinClientUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    location: Optional[str] = Field(None, max_length=256)
    policy: Optional[ThinClientPolicy] = None


class ThinClientCommandOut(BaseModel):
    id: int
    kind: str
    args: dict[str, Any] = {}
    status: str
    result: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    sent_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class ThinClientOut(BaseModel):
    id: int
    public_id: str
    name: str
    location: Optional[str]
    status: str
    online: bool
    enrolled_at: Optional[datetime]
    last_seen_at: Optional[datetime]
    created_at: datetime
    policy: ThinClientPolicy
    policy_version: int
    # The policy version the agent last confirmed it applied.
    applied_policy_version: Optional[int] = None
    state: dict[str, Any] = {}
    session_username: Optional[str] = None
    session_started_at: Optional[datetime] = None
    keep_awake_until: Optional[datetime] = None
    # When a browser on the device last bound to it (each fresh browser does).
    bound_at: Optional[datetime] = None
    screenshot_at: Optional[datetime] = None
    pending_commands: int = 0
    # Agent version Cove would hand out now; differs from state.agent_version
    # while an update is rolling out.
    latest_agent_version: Optional[str] = None


class ThinClientEnrollTokenIn(BaseModel):
    ttl_hours: int = Field(1, ge=1, le=168)


class ThinClientEnrollTokenOut(BaseModel):
    code: str
    expires_at: datetime
    install_command: str


class ThinClientCommandIn(BaseModel):
    kind: str = Field(..., max_length=32)
    args: dict[str, Any] = Field(default_factory=dict)


class ThinClientKeepAwakeIn(BaseModel):
    minutes: int = Field(..., ge=0, le=24 * 60)  # 0 clears it


class ThinClientWorkspaceOption(BaseModel):
    public_id: str
    name: str
    username: str
    status: str


# Device-side protocol (agent <-> Cove).

class ThinClientEnrollIn(BaseModel):
    hostname: str = Field("", max_length=255)


class ThinClientEnrollOut(BaseModel):
    device_token: str
    public_id: str
    name: str
    cp_url: str


class ThinClientCheckinIn(BaseModel):
    # Free-form device report, size-capped by the router. Display-only.
    state: dict[str, Any] = Field(default_factory=dict)
    applied_policy_version: Optional[int] = None
    # Seconds to hold the request open waiting for work (long-poll). 0 = reply now.
    wait: int = Field(0, ge=0, le=30)


class ThinClientCommandForDevice(BaseModel):
    id: int
    kind: str
    args: dict[str, Any] = {}


class ThinClientCheckinOut(BaseModel):
    name: str
    policy: ThinClientPolicy
    policy_version: int
    commands: list[ThinClientCommandForDevice] = []
    allowed_hosts: list[str] = []
    keep_awake_until: Optional[datetime] = None
    agent_version: Optional[str] = None
    agent_sha256: Optional[str] = None
    bound_at: Optional[datetime] = None
    # Who is signed in on the device, as far as Cove knows (None = nobody). The
    # agent only runs its idle sign-out while someone is.
    session_username: Optional[str] = None
    server_time: datetime


class ThinClientResultIn(BaseModel):
    ok: bool
    output: str = Field("", max_length=200_000)


class ThinClientTicketOut(BaseModel):
    url: str
    expires_in: int
    # Server time; the agent compares the device's bound_at against it.
    issued_at: datetime


class ThinClientSelfOut(BaseModel):
    """What the SPA running ON a thin client may know about it."""

    public_id: str
    name: str
    auto_open: str
    pinned_workspace_id: Optional[str]
    signout_minutes: int
    screen_off_minutes: int


# ── Integrations (Home Assistant) ────────────────────────────────────────────

class IntegrationTokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    scope: Literal["read", "control"] = "read"


class IntegrationTokenOut(BaseModel):
    id: int
    name: str
    prefix: str
    scope: str
    created_by: Optional[str] = None
    created_at: datetime
    last_used_at: Optional[datetime] = None
    last_used_ip: Optional[str] = None

    model_config = {"from_attributes": True}


class IntegrationTokenCreated(IntegrationTokenOut):
    # The plaintext token, returned once at creation and never again.
    token: str


class IntegrationCommandIn(BaseModel):
    kind: str = Field(..., max_length=32)
    args: dict[str, Any] = Field(default_factory=dict)
