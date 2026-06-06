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
    workspace_type: str = "desktop"
    target_url: Optional[str] = None
    use_tailscale: bool = False
    # Per-workspace Tailscale routing options (stored regardless of use_tailscale).
    ts_exit_node: Optional[str] = None
    ts_accept_routes: bool = True
    ts_accept_dns: bool = True
    # Per-workspace package installation + sudo control.
    install_packages: Optional[str] = None
    proot_apps: Optional[str] = None
    allow_sudo: bool = True


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = None
    target_url: Optional[str] = None
    use_tailscale: Optional[bool] = None
    ts_exit_node: Optional[str] = None
    ts_accept_routes: Optional[bool] = None
    ts_accept_dns: Optional[bool] = None
    install_packages: Optional[str] = None
    proot_apps: Optional[str] = None
    allow_sudo: Optional[bool] = None


class StreamAuthOut(BaseModel):
    # The URL the SPA should point the workspace iframe at. In subdomain mode it
    # carries a one-time ``?__cove_t`` token that bootstraps the per-workspace
    # stream cookie; in subpath mode it is the plain same-origin stream path.
    url: str


class WorkspaceOut(BaseModel):
    id: int
    public_id: str
    user_id: int
    name: str
    status: str
    workspace_type: str
    container_id: Optional[str]
    container_name: Optional[str]
    image_id: int
    image_name: str
    image_logo: Optional[str]
    target_url: Optional[str]
    use_tailscale: bool
    ts_exit_node: Optional[str]
    ts_accept_routes: bool
    ts_accept_dns: bool
    install_packages: Optional[str]
    proot_apps: Optional[str]
    allow_sudo: bool
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
            image_id=ws.image_id,
            image_name=ws.image.name if ws.image else "",
            image_logo=ws.image.logo_url if ws.image else None,
            target_url=ws.target_url,
            use_tailscale=ws.use_tailscale,
            ts_exit_node=ws.ts_exit_node,
            ts_accept_routes=ws.ts_accept_routes,
            ts_accept_dns=ws.ts_accept_dns,
            install_packages=ws.install_packages,
            proot_apps=ws.proot_apps,
            allow_sudo=ws.allow_sudo,
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
    workspace_lan_access: bool
    workspace_no_new_privileges: bool
    workspace_max_runtime_hours: int


class EnvEntry(BaseModel):
    name: str
    value: str


class EnvSummaryOut(BaseModel):
    entries: list[EnvEntry]


class AppSettingsUpdate(BaseModel):
    tailscale_image: Optional[str] = None
    workspace_lan_access: Optional[bool] = None
    workspace_no_new_privileges: Optional[bool] = None
    workspace_max_runtime_hours: Optional[int] = None


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


# ── Files ─────────────────────────────────────────────────────────────────────

class FileEntry(BaseModel):
    name: str
    type: str  # 'dir' | 'file'
    size: int
    modified: datetime


class FileListing(BaseModel):
    path: str
    entries: list[FileEntry]


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
