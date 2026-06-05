from datetime import datetime
from typing import Optional

from pydantic import BaseModel, HttpUrl


# ── Auth ──────────────────────────────────────────────────────────────────────

class AuthConfig(BaseModel):
    oidc_enabled: bool
    oidc_provider_name: str
    needs_setup: bool


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
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Workspaces ────────────────────────────────────────────────────────────────

class WorkspaceCreate(BaseModel):
    name: str
    image_id: int
    workspace_type: str = "desktop"
    target_url: Optional[str] = None


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
    target_url: Optional[str]
    stream_url: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    stopped_at: Optional[datetime]
    error_message: Optional[str]

    model_config = {"from_attributes": True}

    @classmethod
    def from_workspace(cls, ws) -> "WorkspaceOut":
        stream_url = f"/workspace/{ws.public_id}/" if ws.status == "running" else None
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
            target_url=ws.target_url,
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
