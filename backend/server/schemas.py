from datetime import datetime
from typing import Optional

from pydantic import BaseModel

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
    description: Optional[str] = None
    enabled: Optional[bool] = None


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

    model_config = {"from_attributes": True}


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
    # Ephemeral: no persistent storage — nothing is saved between sessions.
    ephemeral: bool = False
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


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = None
    target_url: Optional[str] = None
    kiosk: Optional[bool] = None
    kiosk_dark: Optional[bool] = None
    kiosk_menu: Optional[bool] = None
    use_tailscale: Optional[bool] = None
    use_gluetun: Optional[bool] = None
    ephemeral: Optional[bool] = None
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


class LanPolicyOut(BaseModel):
    # Admin master toggle for direct (raw-bridge) LAN egress, plus the CIDRs a
    # workspace may reach when it opts in. Surfaced to non-admin users so the
    # launch/edit modals can show the per-workspace checkbox + its ranges.
    enabled: bool
    subnets: list[str]


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


class WorkspaceStats(BaseModel):
    cpu_pct: float
    mem_used: int
    mem_limit: int
    mem_pct: float
    # Present only for Tailscale-routed workspaces once the sidecar has joined
    # the tailnet; None otherwise.
    tailscale_ip: str | None = None


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
    image_id: int
    image_name: str
    image_logo: Optional[str]
    target_url: Optional[str]
    kiosk: bool
    kiosk_dark: bool
    kiosk_menu: bool
    use_tailscale: bool
    use_gluetun: bool
    ephemeral: bool
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
    stream_url: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    stopped_at: Optional[datetime]
    error_message: Optional[str]

    model_config = {"from_attributes": True}

    @classmethod
    def from_workspace(cls, ws) -> "WorkspaceOut":
        from server.config import get_settings

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
            workspace_type=ws.workspace_type,
            container_id=ws.container_id,
            container_name=ws.container_name,
            zone_id=ws.zone_id,
            image_id=ws.image_id,
            image_name=ws.image.name if ws.image else "",
            image_logo=ws.image.logo_url if ws.image else None,
            target_url=ws.target_url,
            kiosk=ws.kiosk,
            kiosk_dark=ws.kiosk_dark,
            kiosk_menu=ws.kiosk_menu,
            use_tailscale=ws.use_tailscale,
            use_gluetun=ws.use_gluetun,
            ephemeral=ws.ephemeral,
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
            stream_url=stream_url,
            created_at=ws.created_at,
            started_at=ws.started_at,
            stopped_at=ws.stopped_at,
            error_message=ws.error_message,
        )


# ── Admin ─────────────────────────────────────────────────────────────────────

class AdminUserCreate(BaseModel):
    username: str
    password: str
    is_admin: bool = False


class AdminUserUpdate(BaseModel):
    username: Optional[str] = None
    is_admin: Optional[bool] = None
    password: Optional[str] = None


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


class EnvEntry(BaseModel):
    name: str
    value: str


class EnvSummaryOut(BaseModel):
    entries: list[EnvEntry]


class AppSettingsUpdate(BaseModel):
    tailscale_image: Optional[str] = None
    gluetun_image: Optional[str] = None
    workspace_lan_access: Optional[bool] = None
    workspace_lan_subnets: Optional[str] = None
    workspace_no_new_privileges: Optional[bool] = None
    workspace_max_runtime_hours: Optional[int] = None
    workspace_cpu_limit: Optional[float] = None
    workspace_memory_limit_mb: Optional[int] = None


# ── Tailscale ─────────────────────────────────────────────────────────────────

class TailscaleConfigOut(BaseModel):
    enabled: bool
    has_auth_key: bool
    login_server: Optional[str]


class TailscaleConfigUpdate(BaseModel):
    # auth_key uses a sentinel default: an omitted field leaves the stored key
    # unchanged; an explicit "" (or null) clears it; a non-empty string replaces it.
    auth_key: Optional[str] = _UNSET
    login_server: Optional[str] = None
    enabled: Optional[bool] = None


# ── Gluetun (per-user VPN) ──────────────────────────────────────────────────────

class GluetunConfigOut(BaseModel):
    # Never returns the config file or secrets — only presence flags.
    enabled: bool
    vpn_type: str
    has_config: bool
    config_filename: Optional[str]
    has_wireguard_private_key: bool
    has_openvpn_user: bool
    has_openvpn_password: bool


class GluetunConfigUpdate(BaseModel):
    # Each secret/file uses the sentinel default: omitted -> unchanged;
    # "" or null -> clear; a value -> replace. All stored encrypted at rest.
    enabled: Optional[bool] = None
    vpn_type: Optional[str] = None
    config_file: Optional[str] = _UNSET
    config_filename: Optional[str] = _UNSET
    wireguard_private_key: Optional[str] = _UNSET
    openvpn_user: Optional[str] = _UNSET
    openvpn_password: Optional[str] = _UNSET


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


class ContainerLogsOut(BaseModel):
    source: str  # 'desktop' | 'tailscale' | 'gluetun'
    available: bool  # False when the backing container is missing
    output: str


# ── Files ─────────────────────────────────────────────────────────────────────

class FileEntry(BaseModel):
    name: str
    type: str  # 'dir' | 'file'
    size: int
    modified: datetime


class FileListing(BaseModel):
    path: str
    entries: list[FileEntry]


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
    status: Optional[str] = None


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

    model_config = {"from_attributes": True}
