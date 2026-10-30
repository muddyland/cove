"""Thin client session hardening (post-1.3.1 security review): the session cap
resets the kiosk, a stale binding fails closed, a new sign-in restarts the clock,
stream tokens die with the device session, and the device re-binds before its
binding cookie could ever lapse."""

import time
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient

from server import thin_clients as tc
from server.config import get_settings
from server.db import SessionLocal
from server.models import AuditLog, ThinClient, ThinClientCommand
from server.routers.auth import _resolve_stream_user
from server.security import create_stream_token, decode_stream_token
from server.tests.helpers import auth_header, create_user_via_admin, login, setup_admin
from server.tests.test_thin_clients import _bind, _checkin, _claims, _dev, _enroll


def _kiosk_session(client, hours=1):
    admin, _ = setup_admin(client)
    create_user_via_admin(client, admin, "alice")
    dev, dtoken = _enroll(client, admin)
    client.patch(f"/api/admin/thin-clients/{dev['id']}", json={"policy": {"session_max_hours": hours}},
                 headers=auth_header(admin))
    kiosk = TestClient(client.app)
    _bind(kiosk, dtoken)
    k_token = login(kiosk, "alice", "password123").json()["access_token"]
    return admin, dev, dtoken, kiosk, k_token


def _age_session(dev_id, hours):
    """Pretend the device's current sign-in started ``hours`` ago."""
    with SessionLocal() as db:
        c = db.get(ThinClient, dev_id)
        c.session_started_at = datetime.now(timezone.utc) - timedelta(hours=hours)
        db.commit()


def test_cap_expiry_resets_the_browser_and_clears_the_session(client):
    admin, dev, dtoken, kiosk, k_token = _kiosk_session(client)
    assert _checkin(client, dtoken).json()["session_username"] == "alice"
    _age_session(dev["id"], 2)

    out = _checkin(client, dtoken).json()
    assert out["session_username"] is None
    # The kiosk is told to wipe its profile, which takes the identity
    # provider's cookie (and any stream cookie) with it.
    assert [(c["kind"], c["args"]) for c in out["commands"]] == [("restart_browser", {"reason": "cap"})]
    with SessionLocal() as db:
        c = db.get(ThinClient, dev["id"])
        assert c.session_username is None and c.session_started_at is None
        assert db.query(AuditLog).filter(AuditLog.action == "thin_client.session_cap").count() == 1
    assert kiosk.get("/api/auth/me", headers=auth_header(k_token)).status_code == 401
    # Only once: the next check-in has nothing more to do.
    assert _checkin(client, dtoken).json()["commands"] == []


def test_signing_in_again_after_the_cap_restarts_the_clock(client):
    admin, dev, dtoken, kiosk, k_token = _kiosk_session(client)
    _age_session(dev["id"], 2)
    # The same user signs in again (e.g. before the agent's reset landed).
    _bind(kiosk, dtoken)
    login(kiosk, "alice", "password123")
    with SessionLocal() as db:
        started = db.get(ThinClient, dev["id"]).session_started_at.replace(tzinfo=timezone.utc)
    assert datetime.now(timezone.utc) - started < timedelta(minutes=1)
    # So Cove reports her signed in (the agent's idle sign-out stays armed).
    assert _checkin(client, dtoken).json()["session_username"] == "alice"


def test_refresh_keeps_the_session_start(client):
    admin, dev, dtoken, kiosk, k_token = _kiosk_session(client, hours=12)
    with SessionLocal() as db:
        first = db.get(ThinClient, dev["id"]).session_started_at
    time.sleep(0.01)
    assert kiosk.post("/api/auth/refresh").status_code == 200
    with SessionLocal() as db:
        assert db.get(ThinClient, dev["id"]).session_started_at == first


def test_revoked_binding_refuses_to_sign_in(client):
    """An admin signs the device out while its agent is offline: the browser
    keeps a revoked binding cookie. Signing in there must not hand out an
    ordinary, unscoped session."""
    admin, dev, dtoken, kiosk, k_token = _kiosk_session(client)
    time.sleep(0.01)
    client.post(f"/api/admin/thin-clients/{dev['id']}/signout", headers=auth_header(admin))
    kiosk.cookies.delete(get_settings().cookie_session_name)
    kiosk.cookies.delete(get_settings().cookie_refresh_name, path="/api/auth")
    r = login(kiosk, "alice", "password123")
    assert r.status_code == 403 and "binding" in r.json()["detail"]
    # A rebound browser signs in normally (device-scoped).
    _bind(kiosk, dtoken)
    r = login(kiosk, "alice", "password123")
    assert r.status_code == 200 and _claims(r.json()["access_token"])["tc"] == dev["public_id"]


def test_expired_binding_cookie_refuses_to_sign_in(client):
    admin, dev, dtoken, kiosk, _ = _kiosk_session(client)
    settings = get_settings()
    now = datetime.now(timezone.utc)
    stale = jwt.encode(
        {"type": "tc_bind", "tc": dev["public_id"], "iat": (now - timedelta(days=500)).timestamp(),
         "exp": now - timedelta(days=1)},
        settings.get_secret_key(), algorithm=settings.jwt_algorithm,
    )
    fresh = TestClient(client.app)
    fresh.cookies.set(tc.BIND_COOKIE, stale)
    assert login(fresh, "alice", "password123").status_code == 403
    # An ordinary browser (no binding cookie at all) is unaffected.
    assert login(TestClient(client.app), "alice", "password123").status_code == 200


def test_deleted_device_binding_refuses_to_sign_in(client):
    admin, dev, dtoken, kiosk, _ = _kiosk_session(client)
    client.delete(f"/api/admin/thin-clients/{dev['id']}", headers=auth_header(admin))
    kiosk.cookies.delete(get_settings().cookie_refresh_name, path="/api/auth")
    assert login(kiosk, "alice", "password123").status_code == 403


def test_unused_device_rebinds_before_its_cookie_could_lapse(client):
    admin, dev, dtoken, kiosk, _ = _kiosk_session(client)
    # Nobody signed in, and the browser bound longer ago than REBIND_AFTER.
    client.post(f"/api/admin/thin-clients/{dev['id']}/signout", headers=auth_header(admin))
    for cmd in _checkin(client, dtoken).json()["commands"]:  # the sign-out's own restart
        client.post(f"/api/thin-client/commands/{cmd['id']}/result", json={"ok": True}, headers=_dev(dtoken))
    with SessionLocal() as db:
        c = db.get(ThinClient, dev["id"])
        c.bound_at = datetime.now(timezone.utc) - tc.REBIND_AFTER - timedelta(hours=1)
        db.commit()
    out = _checkin(client, dtoken).json()
    assert [(c["kind"], c["args"]) for c in out["commands"]] == [("restart_browser", {"reason": "rebind"})]
    # Not queued again while that one is outstanding.
    assert _checkin(client, dtoken).json()["commands"] == []
    # Never while someone is signed in.
    with SessionLocal() as db:
        db.query(ThinClientCommand).delete()
        db.commit()
    _bind(kiosk, dtoken)
    login(kiosk, "alice", "password123")
    with SessionLocal() as db:
        c = db.get(ThinClient, dev["id"])
        c.bound_at = datetime.now(timezone.utc) - tc.REBIND_AFTER - timedelta(hours=1)
        db.commit()
    assert _checkin(client, dtoken).json()["commands"] == []


def test_binding_cookie_outlives_any_session():
    assert tc.BIND_COOKIE_DAYS >= 365
    assert tc.REBIND_AFTER < timedelta(days=tc.BIND_COOKIE_DAYS)


def test_zero_session_cap_means_cove_default_not_never(client):
    admin, dev, dtoken, kiosk, k_token = _kiosk_session(client, hours=0)
    claims = _claims(k_token)
    days = get_settings().refresh_token_days
    assert _claims(kiosk.cookies.get(get_settings().cookie_refresh_name))["exp"] <= claims["sst"] + days * 86400 + 1
    _age_session(dev["id"], days * 24 + 1)
    assert _checkin(client, dtoken).json()["session_username"] is None


def test_stream_tokens_from_a_kiosk_die_with_the_device_session(client, monkeypatch):
    """Subdomain mode: the stream cookie minted on a kiosk is device-scoped."""
    admin, dev, dtoken, kiosk, k_token = _kiosk_session(client)
    with SessionLocal() as db:
        claims = tc.device_claims(db, _claims(k_token))
    assert claims["tc"] == dev["public_id"]
    stream = create_stream_token(int(_claims(k_token)["sub"]), "ws1", **claims)
    payload = decode_stream_token(stream)
    assert payload["tc"] == dev["public_id"]
    assert payload["exp"] <= claims["cap"].timestamp() + 1

    with SessionLocal() as db:
        assert _resolve_stream_user(db, stream, "ws1") is not None
    time.sleep(0.01)
    client.post(f"/api/admin/thin-clients/{dev['id']}/signout", headers=auth_header(admin))
    with SessionLocal() as db:
        assert _resolve_stream_user(db, stream, "ws1") is None
    # A stream token from an ordinary browser carries no device and is unaffected.
    with SessionLocal() as db:
        plain = create_stream_token(int(_claims(k_token)["sub"]), "ws1", **tc.device_claims(db, {"sub": "2"}))
        assert "tc" not in decode_stream_token(plain)
        assert _resolve_stream_user(db, plain, "ws1") is not None


def test_signed_out_reason_names_the_audit_entry(client):
    admin, dev, dtoken, kiosk, _ = _kiosk_session(client)
    assert client.post("/api/thin-client/signed-out", json={"reason": "admin"}, headers=_dev(dtoken)).status_code == 204
    _bind(kiosk, dtoken)
    login(kiosk, "alice", "password123")
    # Agents before 1.1.1 send an empty body: still an idle sign-out.
    assert client.post("/api/thin-client/signed-out", headers=_dev(dtoken)).status_code == 204
    with SessionLocal() as db:
        actions = [a.action for a in db.query(AuditLog).order_by(AuditLog.id)]
    assert "thin_client.browser_reset" in actions and actions[-1] == "thin_client.idle_signout"
