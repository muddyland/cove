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


# Python data migrations (run after the SQL migrations): (name, callable).
_DATA_MIGRATIONS = [
    ("0014_encrypt_tailscale_auth_keys", _encrypt_tailscale_auth_keys),
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
