"""Tests for data migrations (0014 Tailscale auth_key encryption, 0049 profiles)."""

from sqlalchemy import text

from server import security
from server.db import SessionLocal, engine
from server.migrations import _encrypt_tailscale_auth_keys, _move_routing_config_to_profiles
from server.models import (
    TailscaleProfile,
    User,
    UserGluetun,
    UserTailscale,
    VpnProfile,
    Workspace,
    WorkspaceImage,
)


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


def test_single_configs_become_default_profiles():
    """0049: each user's one Gluetun / Tailscale config turns into a "Default"
    profile (ciphertext copied as-is), their VPN / Tailscale workspaces point at
    it, and the old rows are gone."""
    db = SessionLocal()
    try:
        user = User(username="pre", auth_provider="local", password_hash="x")
        empty = User(username="empty", auth_provider="local", password_hash="x")
        db.add_all([user, empty])
        db.commit()
        img = WorkspaceImage(name="D", docker_image="x/y:latest", image_type="desktop")
        db.add(img)
        db.commit()
        db.add(UserGluetun(
            user_id=user.id, enabled=True, vpn_type="wireguard",
            config_file=security.encrypt_secret("[Interface]"), config_filename="wg0.conf",
            wireguard_private_key=security.encrypt_secret("KEY"),
        ))
        db.add(UserTailscale(user_id=user.id, auth_key=security.encrypt_secret("tskey-1"),
                             login_server="https://hs.example.com"))
        db.add(UserGluetun(user_id=empty.id))  # nothing configured -> no profile
        vpn_ws = Workspace(user_id=user.id, name="v", image_id=img.id, use_gluetun=True)
        ts_ws = Workspace(user_id=user.id, name="t", image_id=img.id, use_tailscale=True)
        plain = Workspace(user_id=user.id, name="p", image_id=img.id)
        db.add_all([vpn_ws, ts_ws, plain])
        db.commit()
        ids = (user.id, empty.id, vpn_ws.id, ts_ws.id, plain.id)
    finally:
        db.close()

    with engine.begin() as conn:
        _move_routing_config_to_profiles(conn)

    uid, empty_id, vpn_id, ts_id, plain_id = ids
    db = SessionLocal()
    try:
        vp = db.query(VpnProfile).filter_by(user_id=uid).one()
        assert vp.name == "Default" and vp.mode == "custom" and vp.vpn_type == "wireguard"
        assert vp.config_filename == "wg0.conf"
        assert security.decrypt_secret(vp.config_file) == "[Interface]"
        assert security.decrypt_secret(vp.wireguard_private_key) == "KEY"
        tp = db.query(TailscaleProfile).filter_by(user_id=uid).one()
        assert security.decrypt_secret(tp.auth_key) == "tskey-1"
        assert tp.login_server == "https://hs.example.com"
        assert db.query(VpnProfile).filter_by(user_id=empty_id).count() == 0
        assert db.get(Workspace, vpn_id).gluetun_profile_id == vp.id
        assert db.get(Workspace, ts_id).tailscale_profile_id == tp.id
        assert db.get(Workspace, plain_id).gluetun_profile_id is None
        assert db.query(UserGluetun).count() == 0
        assert db.query(UserTailscale).count() == 0
    finally:
        db.close()
