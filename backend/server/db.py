from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from server.config import get_settings


class Base(DeclarativeBase):
    pass


def _create_engine():
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    key = settings.db_encryption_key

    if key:
        # Encrypted SQLite via SQLCipher. Import the dbapi lazily so the default
        # (unencrypted) path works even when sqlcipher3 is not installed.
        try:
            import sqlcipher3.dbapi2 as sqlcipher_dbapi  # noqa: F401
        except ImportError as exc:
            # sqlcipher3-binary ships x86_64 wheels only; on other arches it is
            # absent (see requirements.txt). Fail clearly instead of with a raw
            # ImportError so the operator knows the key was set but the driver
            # is unavailable on this platform.
            raise RuntimeError(
                "COVE_DB_ENCRYPTION_KEY is set but the sqlcipher3 driver is not "
                "installed. It is only available as a prebuilt wheel on x86_64. "
                "On this architecture, either unset COVE_DB_ENCRYPTION_KEY to run "
                "with an unencrypted database, or build sqlcipher3 from source."
            ) from exc

        engine = create_engine(
            settings.db_url,
            module=sqlcipher_dbapi,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(engine, "connect")
        def _set_pragmas_encrypted(conn, _):
            cur = conn.cursor()
            # PRAGMA key must run first, before any other statement. Bind the key
            # as a parameter rather than interpolating it so a key containing a
            # quote cannot corrupt the statement.
            cur.execute("PRAGMA key = ?", (key,))
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine

    # Default: plain SQLite.
    engine = create_engine(
        settings.db_url,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def set_wal_mode(conn, _):
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

    return engine


engine = _create_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
