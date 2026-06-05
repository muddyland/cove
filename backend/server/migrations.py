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
