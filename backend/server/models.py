from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db import Base


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    auth_provider: Mapped[str] = mapped_column(String(16), nullable=False, default="local")
    oidc_sub: Mapped[Optional[str]] = mapped_column(String(256), unique=True, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    tokens_valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    workspaces: Mapped[list["Workspace"]] = relationship(
        "Workspace", back_populates="user", cascade="all, delete-orphan"
    )


class WorkspaceImage(Base):
    __tablename__ = "workspace_image"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    docker_image: Mapped[str] = mapped_column(String(256), nullable=False)
    image_type: Mapped[str] = mapped_column(String(16), nullable=False, default="desktop")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    internal_port: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3000, server_default=text("3000")
    )
    # For browser images: the env var used to pass a startup URL (e.g. CHROME_CLI).
    # NULL for non-browser images.
    url_env: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Project logo URL (from the LinuxServer API project_logo field), for display.
    logo_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    workspaces: Mapped[list["Workspace"]] = relationship("Workspace", back_populates="image")


class Workspace(Base):
    __tablename__ = "workspace"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False, default=lambda: uuid4().hex
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="creating")
    workspace_type: Mapped[str] = mapped_column(String(16), nullable=False, default="desktop")
    container_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    container_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    use_tailscale: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    # Per-workspace Tailscale routing options (auth_key + login_server stay per-user).
    ts_exit_node: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    ts_accept_routes: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    ts_accept_dns: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    image_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspace_image.id"), nullable=False)
    target_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    # Open target_url in the browser's full-screen kiosk mode (no browser chrome).
    kiosk: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    # Kiosk extras (browser images only): force dark mode, and keep the
    # right-click context menu / refresh (uses --start-fullscreen vs --kiosk).
    kiosk_dark: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    kiosk_menu: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    # Per-workspace distro packages (universal-package-install Docker Mod), free
    # text — pipe/comma/space separated package names.
    install_packages: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Per-workspace proot-apps (installed via the bundled init script), free text.
    proot_apps: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Whether in-container sudo is allowed. When False, the container gets the
    # no-new-privileges flag which blocks setuid (sudo).
    allow_sudo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    volume_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="workspaces")
    image: Mapped["WorkspaceImage"] = relationship("WorkspaceImage", back_populates="workspaces")


class UserTailscale(Base):
    __tablename__ = "user_tailscale"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), unique=True, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auth_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    login_server: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    # Legacy columns — Tailscale routing options moved to the Workspace level.
    # Still mapped (with defaults) so inserts satisfy the NOT NULL constraint that
    # exists in databases created before the move. Not exposed in the API.
    exit_node: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    accept_routes: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    accept_dns: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class AppSetting(Base):
    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
