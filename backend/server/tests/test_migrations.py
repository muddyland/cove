"""Tests for data migrations (0014 Tailscale auth_key encryption)."""

from sqlalchemy import text

from server import security
from server.db import SessionLocal, engine
from server.migrations import _encrypt_tailscale_auth_keys
from server.models import User, UserTailscale


def _seed_plaintext_key(username: str, key: str) -> None:
    db = SessionLocal()
    try:
        user = User(username=username, auth_provider="local", password_hash="x")
        db.add(user)
        db.commit()
        db.add(UserTailscale(user_id=user.id, auth_key=key))
        db.commit()
    finally:
        db.close()


def _stored_key() -> str:
    with engine.begin() as conn:
        return conn.execute(text("SELECT auth_key FROM user_tailscale")).fetchone()[0]


def test_data_migration_encrypts_legacy_plaintext_key():
    _seed_plaintext_key("legacy", "tskey-legacy-plaintext")

    with engine.begin() as conn:
        _encrypt_tailscale_auth_keys(conn)

    stored = _stored_key()
    assert stored.startswith(security._SECRET_PREFIX)
    assert "tskey-legacy-plaintext" not in stored
    assert security.decrypt_secret(stored) == "tskey-legacy-plaintext"


def test_data_migration_idempotent():
    _seed_plaintext_key("legacy2", "tskey-legacy-2")

    with engine.begin() as conn:
        _encrypt_tailscale_auth_keys(conn)
    first = _stored_key()

    # A second run must not double-encrypt (value already has the prefix).
    with engine.begin() as conn:
        _encrypt_tailscale_auth_keys(conn)

    assert _stored_key() == first
    assert security.decrypt_secret(first) == "tskey-legacy-2"
