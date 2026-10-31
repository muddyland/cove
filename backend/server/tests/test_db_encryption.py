"""COVE_DB_ENCRYPTION_KEY really opens an encrypted database.

SQLite can't bind parameters in a PRAGMA, so ``PRAGMA key = ?`` failed on the
first connection ("near ?: syntax error") and Cove couldn't start with a key
set. These tests drive the real engine factory against SQLCipher.
"""

import pytest
from sqlalchemy import text

from server import db
from server.config import get_settings

sqlcipher3 = pytest.importorskip("sqlcipher3.dbapi2")


def _engine(monkeypatch, tmp_path, key):
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "db_encryption_key", key)
    return db._create_engine()


@pytest.mark.parametrize("key", ["plain-passphrase", "it's \"quoted\"; DROP TABLE t; --"])
def test_encrypted_database_round_trips(monkeypatch, tmp_path, key):
    engine = _engine(monkeypatch, tmp_path, key)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (x TEXT)"))
        conn.execute(text("INSERT INTO t VALUES ('secret row')"))
    engine.dispose()

    raw = (tmp_path / "cove.db").read_bytes()
    assert not raw.startswith(b"SQLite format 3")
    assert b"secret row" not in raw

    engine = _engine(monkeypatch, tmp_path, key)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT x FROM t")).scalar() == "secret row"
    engine.dispose()


def test_wrong_key_cannot_read(monkeypatch, tmp_path):
    engine = _engine(monkeypatch, tmp_path, "right-key")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (x TEXT)"))
    engine.dispose()

    engine = _engine(monkeypatch, tmp_path, "wrong-key")
    with pytest.raises(Exception, match="not a database"):
        with engine.connect() as conn:
            conn.execute(text("SELECT count(*) FROM t"))
    engine.dispose()


def test_key_matches_the_sqlcipher_passphrase_form(tmp_path):
    # A database keyed the usual way (as in the sqlcipher shell) opens.
    path = str(tmp_path / "old.db")
    conn = sqlcipher3.connect(path)
    conn.execute("PRAGMA key = 'legacy-key'")
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.execute("INSERT INTO t VALUES ('kept')")
    conn.commit()
    conn.close()

    conn = sqlcipher3.connect(path)
    conn.execute(db.pragma_key_sql("legacy-key"))
    assert conn.execute("SELECT x FROM t").fetchone() == ("kept",)
    conn.close()


def test_nul_in_key_is_refused():
    with pytest.raises(RuntimeError, match="NUL"):
        db.pragma_key_sql("abc\x00def")
