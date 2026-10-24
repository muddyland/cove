from sqlalchemy import Column, String, text
from sqlalchemy.exc import OperationalError

from server.db import Base, engine


class Migration(Base):
    __tablename__ = "_migrations"
    __table_args__ = {"extend_existing": True}

    name = Column(String, primary_key=True)
    applied_at = Column(String, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


_MIGRATIONS: list[tuple[str, str]] = [
    # (name, sql)
    # workspace.public_id: add column, backfill existing rows, then unique index.
    (
        "0001_workspace_public_id_add_column",
        "ALTER TABLE workspace ADD COLUMN public_id TEXT",
    ),
    (
        "0002_workspace_public_id_backfill",
        "UPDATE workspace SET public_id = lower(hex(randomblob(16))) "
        "WHERE public_id IS NULL",
    ),
    (
        "0003_workspace_public_id_unique_index",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_workspace_public_id "
        "ON workspace (public_id)",
    ),
    # workspace_image.internal_port for upgraders.
    (
        "0004_workspace_image_internal_port",
        "ALTER TABLE workspace_image ADD COLUMN internal_port INTEGER NOT NULL DEFAULT 3000",
    ),
    # workspace_image.url_env (browser startup-URL env var) for upgraders.
    (
        "0005_workspace_image_url_env",
        "ALTER TABLE workspace_image ADD COLUMN url_env TEXT",
    ),
    # workspace.use_tailscale: route this workspace through a Tailscale sidecar.
    (
        "0006_workspace_use_tailscale",
        "ALTER TABLE workspace ADD COLUMN use_tailscale INTEGER NOT NULL DEFAULT 0",
    ),
    # Per-workspace Tailscale routing options (moved from user_tailscale).
    (
        "0007_workspace_ts_exit_node",
        "ALTER TABLE workspace ADD COLUMN ts_exit_node TEXT",
    ),
    (
        "0008_workspace_ts_accept_routes",
        "ALTER TABLE workspace ADD COLUMN ts_accept_routes INTEGER NOT NULL DEFAULT 1",
    ),
    (
        "0009_workspace_ts_accept_dns",
        "ALTER TABLE workspace ADD COLUMN ts_accept_dns INTEGER NOT NULL DEFAULT 1",
    ),
    # Per-workspace package installation + sudo control.
    (
        "0010_workspace_install_packages",
        "ALTER TABLE workspace ADD COLUMN install_packages TEXT",
    ),
    (
        "0011_workspace_proot_apps",
        "ALTER TABLE workspace ADD COLUMN proot_apps TEXT",
    ),
    (
        "0012_workspace_allow_sudo",
        "ALTER TABLE workspace ADD COLUMN allow_sudo INTEGER NOT NULL DEFAULT 1",
    ),
    # workspace_image.logo_url (project logo from the LinuxServer API) for upgraders.
    (
        "0013_workspace_image_logo_url",
        "ALTER TABLE workspace_image ADD COLUMN logo_url TEXT",
    ),
    # workspace.kiosk: open target_url in browser kiosk/full-screen mode.
    (
        "0015_workspace_kiosk",
        "ALTER TABLE workspace ADD COLUMN kiosk INTEGER NOT NULL DEFAULT 0",
    ),
    # Kiosk extras: force dark mode, keep right-click/refresh menu.
    (
        "0016_workspace_kiosk_dark",
        "ALTER TABLE workspace ADD COLUMN kiosk_dark INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "0017_workspace_kiosk_menu",
        "ALTER TABLE workspace ADD COLUMN kiosk_menu INTEGER NOT NULL DEFAULT 0",
    ),
    # Per-workspace custom (public) DNS resolvers.
    (
        "0018_workspace_custom_dns",
        "ALTER TABLE workspace ADD COLUMN custom_dns INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "0019_workspace_dns_servers",
        "ALTER TABLE workspace ADD COLUMN dns_servers VARCHAR(256)",
    ),
    # Per-workspace AppImage app URLs (extracted + launcher via init script).
    (
        "0020_workspace_appimages",
        "ALTER TABLE workspace ADD COLUMN appimages VARCHAR",
    ),
    # Per-workspace opt-in for direct (raw-bridge) LAN egress.
    (
        "0021_workspace_lan_access",
        "ALTER TABLE workspace ADD COLUMN lan_access BOOLEAN NOT NULL DEFAULT 0",
    ),
    # Ephemeral workspaces: no persistent /config mount.
    (
        "0022_workspace_ephemeral",
        "ALTER TABLE workspace ADD COLUMN ephemeral BOOLEAN NOT NULL DEFAULT 0",
    ),
    # Route a workspace through the user's Gluetun VPN sidecar. (The user_gluetun
    # table itself is created by create_all.)
    (
        "0023_workspace_use_gluetun",
        "ALTER TABLE workspace ADD COLUMN use_gluetun BOOLEAN NOT NULL DEFAULT 0",
    ),
    # Per-user SSH key (private encrypted at rest, public + type in the clear).
    (
        "0024_user_ssh_private_key",
        "ALTER TABLE user ADD COLUMN ssh_private_key TEXT",
    ),
    (
        "0025_user_ssh_public_key",
        "ALTER TABLE user ADD COLUMN ssh_public_key TEXT",
    ),
    (
        "0026_user_ssh_key_type",
        "ALTER TABLE user ADD COLUMN ssh_key_type VARCHAR(16)",
    ),
    # Inject the owner's SSH key into a workspace's ~/.ssh (on by default).
    (
        "0027_workspace_inject_ssh_key",
        "ALTER TABLE workspace ADD COLUMN inject_ssh_key BOOLEAN NOT NULL DEFAULT 1",
    ),
    # Multi-zone: which node a workspace runs on. 0 = the local control-plane
    # daemon. (The zone table itself is created by create_all.)
    (
        "0028_workspace_zone_id",
        "ALTER TABLE workspace ADD COLUMN zone_id INTEGER NOT NULL DEFAULT 0",
    ),
    # Use Wayland for the pixelflux/Selkies stream (on by default); off forces
    # the X11 fallback via PIXELFLUX_WAYLAND=false.
    (
        "0030_workspace_pixelflux_wayland",
        "ALTER TABLE workspace ADD COLUMN pixelflux_wayland BOOLEAN NOT NULL DEFAULT 1",
    ),
    # Timestamp of the last status transition — anchors the reconciler's
    # transient-state timeouts (creating/stopping/migrating).
    (
        "0031_workspace_status_changed_at",
        "ALTER TABLE workspace ADD COLUMN status_changed_at DATETIME",
    ),
    # Opt-in: clear a stale browser single-instance lock before launch.
    (
        "0032_workspace_clear_browser_lock",
        "ALTER TABLE workspace ADD COLUMN clear_browser_lock BOOLEAN NOT NULL DEFAULT 0",
    ),
    # Opt-in: GPU (VAAPI) hardware acceleration for the Selkies stream.
    (
        "0033_workspace_gpu_accel",
        "ALTER TABLE workspace ADD COLUMN gpu_accel BOOLEAN NOT NULL DEFAULT 0",
    ),
    # Opt-in: per-workspace Docker-in-Docker sidecar (dev container support).
    (
        "0034_workspace_use_docker",
        "ALTER TABLE workspace ADD COLUMN use_docker BOOLEAN NOT NULL DEFAULT 0",
    ),
    # workspace_image.icon_png: baked PWA icon (logo + Cove watermark) for upgraders.
    (
        "0035_workspace_image_icon_png",
        "ALTER TABLE workspace_image ADD COLUMN icon_png BLOB",
    ),
    # Opt-in: share the user's persistent /config profile across workspaces.
    (
        "0036_workspace_shared_profile",
        "ALTER TABLE workspace ADD COLUMN shared_profile BOOLEAN NOT NULL DEFAULT 0",
    ),
    # workspace.preview_jpg / preview_at: still frame of the workspace screen,
    # captured from its own Selkies stream at launch (see server.preview).
    (
        "0037_workspace_preview_jpg",
        "ALTER TABLE workspace ADD COLUMN preview_jpg BLOB",
    ),
    (
        "0038_workspace_preview_at",
        "ALTER TABLE workspace ADD COLUMN preview_at DATETIME",
    ),
    # workspace.favicon_*: the icon of the site a browser workspace opens, so the
    # card shows the site rather than the browser (see server.favicons).
    (
        "0039_workspace_favicon_png",
        "ALTER TABLE workspace ADD COLUMN favicon_png BLOB",
    ),
    (
        "0040_workspace_favicon_origin",
        "ALTER TABLE workspace ADD COLUMN favicon_origin TEXT",
    ),
    (
        "0041_workspace_favicon_at",
        "ALTER TABLE workspace ADD COLUMN favicon_at DATETIME",
    ),
    # Delete the workspace record when its container stops (ephemeral only).
    (
        "0042_workspace_auto_remove",
        "ALTER TABLE workspace ADD COLUMN auto_remove BOOLEAN NOT NULL DEFAULT 0",
    ),
    # 1.1.0: a separate relay (edge) client cert per zone, so the central Traefik
    # never presents the control plane's own cert to an agent.
    (
        "0043_zone_edge_cert",
        "ALTER TABLE zone ADD COLUMN edge_cert_pem TEXT",
    ),
    (
        "0044_zone_edge_key",
        "ALTER TABLE zone ADD COLUMN edge_key_enc TEXT",
    ),
    # 1.1.0: Docker-in-Docker is granted per user (it is host root).
    (
        "0045_user_docker_allowed",
        "ALTER TABLE user ADD COLUMN docker_allowed BOOLEAN NOT NULL DEFAULT 0",
    ),
    # Lets a user push back the max-runtime auto-halt of a running workspace.
    (
        "0046_workspace_runtime_extended_at",
        "ALTER TABLE workspace ADD COLUMN runtime_extended_at DATETIME",
    ),
    # Saved VPN / Tailscale profiles: which one a workspace routes through. (The
    # vpn_profile / tailscale_profile tables themselves come from create_all.)
    (
        "0047_workspace_gluetun_profile_id",
        "ALTER TABLE workspace ADD COLUMN gluetun_profile_id INTEGER",
    ),
    (
        "0048_workspace_tailscale_profile_id",
        "ALTER TABLE workspace ADD COLUMN tailscale_profile_id INTEGER",
    ),
    # Per-zone GPU and Docker-in-Docker settings (NULL GPU fields inherit the
    # global settings).
    (
        "0050_zone_gpu_accel",
        "ALTER TABLE zone ADD COLUMN gpu_accel BOOLEAN",
    ),
    (
        "0051_zone_gpu_render_node",
        "ALTER TABLE zone ADD COLUMN gpu_render_node VARCHAR(64)",
    ),
    (
        "0052_zone_gpu_render_gid",
        "ALTER TABLE zone ADD COLUMN gpu_render_gid INTEGER",
    ),
    (
        "0053_zone_docker_enabled",
        "ALTER TABLE zone ADD COLUMN docker_enabled BOOLEAN NOT NULL DEFAULT 0",
    ),
    (
        "0054_zone_agent_allows_docker",
        "ALTER TABLE zone ADD COLUMN agent_allows_docker BOOLEAN",
    ),
]


def _encrypt_tailscale_auth_keys(conn) -> None:
    """Encrypt any Tailscale pre-auth keys still stored as plaintext.

    Idempotent: encrypt_secret-produced values carry a recognizable prefix, so
    already-encrypted rows are skipped (and the migration is recorded so it only
    runs once anyway).
    """
    from server.security import _SECRET_PREFIX, encrypt_secret

    rows = conn.execute(
        text("SELECT id, auth_key FROM user_tailscale WHERE auth_key IS NOT NULL")
    ).fetchall()
    for row in rows:
        key = row[1]
        if key and not key.startswith(_SECRET_PREFIX):
            conn.execute(
                text("UPDATE user_tailscale SET auth_key = :k WHERE id = :id"),
                {"k": encrypt_secret(key), "id": row[0]},
            )


def _seed_local_zone(conn) -> None:
    """Insert the implicit "local" zone (id 0) representing the control plane's
    own Docker daemon, so every workspace's zone_id FK always resolves and the
    per-zone client resolver has a single code path. Idempotent."""
    exists = conn.execute(text("SELECT 1 FROM zone WHERE id = 0")).fetchone()
    if exists:
        return
    conn.execute(
        text(
            "INSERT INTO zone (id, public_id, name, status) "
            "VALUES (0, 'local', 'Local', 'enrolled')"
        )
    )


def _move_routing_config_to_profiles(conn) -> None:
    """Turn each user's single Gluetun / Tailscale config into a profile named
    "Default" and point that user's VPN / Tailscale workspaces at it.

    Ciphertext is copied as-is (same key, same encrypt_secret format). The old
    rows are deleted afterwards: they are no longer read, and leaving them
    behind would keep the user row pinned by a foreign key without ON DELETE.
    """
    now = "CURRENT_TIMESTAMP"
    for row in conn.execute(
        text(
            "SELECT user_id, vpn_type, config_file, config_filename, "
            "wireguard_private_key, openvpn_user, openvpn_password FROM user_gluetun"
        )
    ).fetchall():
        user_id, vpn_type, cfg, fname, wg_key, ov_user, ov_pw = row
        if not (cfg or wg_key or ov_user or ov_pw):
            continue
        conn.execute(
            text(
                "INSERT INTO vpn_profile (user_id, name, mode, vpn_type, config_file, "
                "config_filename, wireguard_private_key, openvpn_user, openvpn_password, "
                f"created_at, updated_at) VALUES (:u, 'Default', 'custom', :t, :c, :f, :k, "
                f":ou, :op, {now}, {now})"
            ),
            {"u": user_id, "t": vpn_type or "openvpn", "c": cfg, "f": fname,
             "k": wg_key, "ou": ov_user, "op": ov_pw},
        )
        pid = conn.execute(
            text("SELECT id FROM vpn_profile WHERE user_id = :u AND name = 'Default'"),
            {"u": user_id},
        ).scalar()
        conn.execute(
            text(
                "UPDATE workspace SET gluetun_profile_id = :p "
                "WHERE user_id = :u AND use_gluetun = 1 AND gluetun_profile_id IS NULL"
            ),
            {"p": pid, "u": user_id},
        )
    conn.execute(text("DELETE FROM user_gluetun"))

    for row in conn.execute(
        text("SELECT user_id, auth_key, login_server FROM user_tailscale")
    ).fetchall():
        user_id, key, server = row
        if not (key or server):
            continue
        conn.execute(
            text(
                "INSERT INTO tailscale_profile (user_id, name, auth_key, login_server, "
                f"created_at, updated_at) VALUES (:u, 'Default', :k, :s, {now}, {now})"
            ),
            {"u": user_id, "k": key, "s": server},
        )
        pid = conn.execute(
            text("SELECT id FROM tailscale_profile WHERE user_id = :u AND name = 'Default'"),
            {"u": user_id},
        ).scalar()
        conn.execute(
            text(
                "UPDATE workspace SET tailscale_profile_id = :p "
                "WHERE user_id = :u AND use_tailscale = 1 AND tailscale_profile_id IS NULL"
            ),
            {"p": pid, "u": user_id},
        )
    conn.execute(text("DELETE FROM user_tailscale"))


# Python data migrations (run after the SQL migrations): (name, callable).
_DATA_MIGRATIONS = [
    ("0014_encrypt_tailscale_auth_keys", _encrypt_tailscale_auth_keys),
    ("0029_seed_local_zone", _seed_local_zone),
    ("0049_routing_config_to_profiles", _move_routing_config_to_profiles),
]


def run_migrations() -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for name, sql in _MIGRATIONS:
            exists = conn.execute(
                text("SELECT 1 FROM _migrations WHERE name = :name"), {"name": name}
            ).fetchone()
            if exists:
                continue
            try:
                conn.execute(text(sql))
            except OperationalError as exc:
                # On a fresh DB, create_all already produced the columns/indexes,
                # so ALTER ADD COLUMN / CREATE INDEX may be redundant. Treat
                # "duplicate column" / "already exists" as a successfully-applied
                # no-op and record the migration so it is not retried.
                msg = str(exc).lower()
                if "duplicate column" not in msg and "already exists" not in msg:
                    raise
            conn.execute(
                text("INSERT INTO _migrations (name) VALUES (:name)"), {"name": name}
            )

        for name, fn in _DATA_MIGRATIONS:
            exists = conn.execute(
                text("SELECT 1 FROM _migrations WHERE name = :name"), {"name": name}
            ).fetchone()
            if exists:
                continue
            fn(conn)
            conn.execute(
                text("INSERT INTO _migrations (name) VALUES (:name)"), {"name": name}
            )
