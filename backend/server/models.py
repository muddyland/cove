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
    # Per-user SSH key, injected into containers' ~/.ssh by default. The private
    # key is encrypted at rest; the public key + type are not secret (shown so the
    # user can copy the public key elsewhere).
    ssh_private_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ssh_public_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ssh_key_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

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


class Zone(Base):
    """A node that runs workspace containers. Zone id 0 is the implicit "local"
    zone — the control plane's own Docker daemon (seeded by a data migration).
    Remote zones are enrolled agent nodes the control plane dials over mTLS."""

    __tablename__ = "zone"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False, default=lambda: uuid4().hex
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Lifecycle: pending -> enrolling -> enrolled -> offline / error.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # The agent's single mTLS port that the control plane DIALS — it fronts
    # everything (workspace streams, the agent API, and the policy-filtered
    # Docker proxy). There is no separately-exposed Docker port.
    endpoint_host: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    endpoint_port: Mapped[int] = mapped_column(
        Integer, nullable=False, default=8443, server_default=text("8443")
    )
    # Enrollment (Phase 3): sha256 of the one-time token + single-use bookkeeping.
    enroll_token_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    enroll_token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    enroll_consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # mTLS material (Phase 2/3). Public certs in the clear; the CP's per-zone
    # client private key is encrypted at rest (encrypt_secret, enc:v1: prefix).
    ca_cert_pem: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    server_cert_pem: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    client_cert_pem: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    client_key_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    agent_fingerprint: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    enrolled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


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
    # Which zone (node) this workspace runs on. 0 = the local control-plane daemon
    # (the default). A workspace is pinned to exactly one zone; moving it requires
    # an explicit migration that copies its /config to the destination zone.
    zone_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("zone.id"), nullable=False, default=0, server_default=text("0")
    )
    use_tailscale: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    # Route egress through the user's Gluetun VPN sidecar (mutually exclusive with
    # use_tailscale). VPN details (config file + secrets) are stored per-user.
    use_gluetun: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    # Ephemeral: skip the persistent /config bind mount entirely, so no data is
    # saved between sessions (the home lives only in the container's layer).
    ephemeral: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    # Opt-in for direct (raw-bridge) egress to the admin-configured LAN subnets.
    # Only takes effect when the admin master toggle + LAN subnets are also set;
    # tailnet-routed LAN access is independent of this flag.
    lan_access: Mapped[bool] = mapped_column(
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
    # Use custom (public) DNS resolvers instead of the Docker/host default. When
    # enabled, the container's resolver forwards to ``dns_servers`` (or sensible
    # public defaults if none are listed) rather than the local network's DNS.
    custom_dns: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    # Space/comma separated DNS server IPs (e.g. "1.1.1.1 9.9.9.9").
    dns_servers: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    # Per-workspace distro packages (universal-package-install Docker Mod), free
    # text — pipe/comma/space separated package names.
    install_packages: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Per-workspace proot-apps (installed via the bundled init script), free text.
    proot_apps: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Per-workspace AppImage app URLs (downloaded + extracted + given a desktop
    # launcher by the bundled init script). Free text — newline/comma/space sep.
    appimages: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Whether in-container sudo is allowed. When False, the container gets the
    # no-new-privileges flag which blocks setuid (sudo).
    allow_sudo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    # Inject the owner's account SSH key into this container's ~/.ssh. On by
    # default; can be disabled per workspace.
    inject_ssh_key: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    volume_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="workspaces")
    image: Mapped["WorkspaceImage"] = relationship("WorkspaceImage", back_populates="workspaces")
    zone: Mapped[Optional["Zone"]] = relationship("Zone")


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


class UserGluetun(Base):
    """Per-user Gluetun VPN config: a custom OpenVPN/Wireguard config file plus
    optional credential overrides. The config file and secrets are encrypted at
    rest (they carry VPN credentials)."""

    __tablename__ = "user_gluetun"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), unique=True, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # "openvpn" or "wireguard".
    vpn_type: Mapped[str] = mapped_column(String(16), nullable=False, default="openvpn")
    # The uploaded .ovpn / wg .conf file contents (encrypted at rest).
    config_file: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    config_filename: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    # Optional direct-secret overrides (encrypted). When set, these override the
    # corresponding values inside the config file.
    wireguard_private_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    openvpn_user: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    openvpn_password: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
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
