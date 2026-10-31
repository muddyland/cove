"""Regression tests for the findings of the 1.4.0 pre-release security review."""

import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from server import integration, mfa, net
from server import oidc as oidc_module
from server.config import get_settings
from server.db import SessionLocal
from server.models import IntegrationToken, User, Workspace
from server.routers import auth as auth_router
from server.routers import mfa as mfa_router
from server.security import sign_state
from server.tests.helpers import add_image, auth_header, create_user_via_admin, login, setup_admin
from server.tests.softkey import SoftKey


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(get_settings(), "app_origin", "http://testserver")
    auth_router._rate_buckets.clear()
    mfa._used_jti.clear()
    mfa._attempts.clear()
    mfa_router._failures.clear()
    net.set_trusted_proxies("")
    yield


def _login(client, name="bob", password="password123"):
    client.cookies.clear()
    return client.post("/api/auth/login", json={"username": name, "password": password})


def _session(client, name="bob"):
    r = _login(client, name)
    assert r.status_code == 200 and "access_token" in r.json(), r.text
    return r.json()["access_token"]


def _opts(client, name):
    auth_router._rate_buckets.clear()
    r = client.post("/api/auth/login/passkey/options", json={"username": name})
    assert r.status_code == 200, r.text
    return r.json()["options"]["allowCredentials"]


def _register(client, token, key):
    h = auth_header(token)
    opts = client.post("/api/auth/2fa/passkeys/options", json={}, headers=h).json()
    cred = key.register(opts["options"])
    r = client.post(
        "/api/auth/2fa/passkeys",
        json={"password": "password123", "state": opts["state"], "credential": cred, "name": "k"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r


# ── Two-factor sign-in ───────────────────────────────────────────────────────


def test_passkey_options_dont_tell_case_variants_apart(client):
    """A name and its case variant always get different credential lists, so
    comparing them no longer reveals which accounts have passkeys."""
    admin, _ = setup_admin(client)
    create_user_via_admin(client, admin, "bob")
    create_user_via_admin(client, admin, "carol")
    _register(client, _session(client, "bob"), SoftKey())

    assert _opts(client, "bob") != _opts(client, "BOB")
    assert _opts(client, "carol") != _opts(client, "CAROL")
    assert _opts(client, "nobody") != _opts(client, "NOBODY")
    # Still stable per name.
    assert _opts(client, "nobody") == _opts(client, "nobody")


def test_made_up_credentials_sometimes_carry_no_transports(client):
    """Real keys stored without transports are sent without them; made-up ones
    must be able to look the same."""
    setup_admin(client)
    seen = [all("transports" in c for c in _opts(client, f"ghost{i}")) for i in range(40)]
    assert not all(seen)
    assert any(seen)


def test_non_ascii_digits_are_a_wrong_code_not_a_500(client):
    admin, _ = setup_admin(client)
    create_user_via_admin(client, admin, "bob")
    token = _session(client)
    setup = client.post("/api/auth/2fa/totp/setup", json={}, headers=auth_header(token)).json()
    r = client.post(
        "/api/auth/2fa/totp/enable",
        json={"password": "password123", "setup_token": setup["setup_token"], "code": mfa.totp_now(setup["secret"])},
        headers=auth_header(token),
    )
    assert r.status_code == 200
    tok = _login(client).json()["mfa_token"]
    c2 = TestClient(client.app, raise_server_exceptions=False)
    r = c2.post("/api/auth/login/2fa", json={"mfa_token": tok, "code": "\u0661\u0662\u0663\u0664\u0665\u0666"})
    assert r.status_code == 401, r.text
    assert mfa.match_totp(setup["secret"], "\uff11\uff12\uff13\uff14\uff15\uff16") is None


def test_racing_factor_removals_cant_strand_a_required_account(client, monkeypatch):
    admin, _ = setup_admin(client)
    u = create_user_via_admin(client, admin, "bob")
    token = _register(client, _session(client), SoftKey()).json()["access_token"]
    _register(client, token, SoftKey())
    client.patch(f"/api/admin/users/{u['id']}", json={"require_2fa": True}, headers=auth_header(admin))
    status = client.get("/api/auth/2fa", headers=auth_header(token)).json()
    ids = [p["id"] for p in status["passkeys"]]
    assert len(ids) == 2 and status["required"]

    # Hold each request between its check and its commit, as slow scheduling
    # would. With the account lock the second request waits for the first, so
    # the barrier times out and the requests run one after the other.
    orig = mfa_router._would_strand
    barrier = threading.Barrier(2, timeout=3)

    def slow(*a, **kw):
        out = orig(*a, **kw)
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return out

    monkeypatch.setattr(mfa_router, "_would_strand", slow)
    results = []

    def delete(pid):
        c = TestClient(client.app, raise_server_exceptions=False)
        r = c.post(f"/api/auth/2fa/passkeys/{pid}/delete", json={"password": "password123"},
                   headers=auth_header(token))
        results.append(r.status_code)

    ts = [threading.Thread(target=delete, args=(i,)) for i in ids]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == [200, 400], results
    auth_router._rate_buckets.clear()
    r = _login(client)
    assert r.json().get("mfa_required"), r.json()


# ── Integration tokens ───────────────────────────────────────────────────────


def _oidc(monkeypatch, groups):
    s = get_settings()
    for k, v in (("oidc_issuer", "https://idp.example"), ("oidc_client_id", "c"), ("oidc_client_secret", "s"),
                 ("oidc_admin_group", "cove-admins"), ("oidc_use_userinfo", False)):
        monkeypatch.setattr(s, k, v, raising=False)

    async def exchange_code(**kw):
        return {"id_token": "x"}

    async def verify_id_token(tok, nonce=None):
        return {"sub": "carol-sub", "preferred_username": "carol", "groups": groups}

    monkeypatch.setattr(oidc_module, "exchange_code", exchange_code)
    monkeypatch.setattr(oidc_module, "verify_id_token", verify_id_token)


def _callback(client):
    st = sign_state("abc")
    client.cookies.set("oidc_state", st)
    client.cookies.set("oidc_nonce", "n")
    r = client.get("/api/auth/oidc/callback", params={"code": "c", "state": st}, follow_redirects=False)
    client.cookies.clear()
    return r


def _mint(db, creator: str) -> str:
    tok = integration.new_token()
    db.add(IntegrationToken(name="HA", token_hash=integration.hash_token(tok), prefix=tok[:8],
                            scope="control", created_by=creator))
    db.commit()
    return tok


def test_sso_demotion_revokes_integration_tokens(client, monkeypatch):
    setup_admin(client)
    _oidc(monkeypatch, ["cove-admins"])
    assert _callback(client).status_code in (302, 303, 307)
    with SessionLocal() as db:
        carol = db.query(User).filter_by(oidc_sub="carol-sub").one()
        assert carol.is_admin
        tok = _mint(db, carol.username)
    assert client.get("/api/integration/v1/status", headers=auth_header(tok)).status_code == 200

    _oidc(monkeypatch, [])  # the IdP took carol out of the admin group
    assert _callback(client).status_code in (302, 303, 307)
    with SessionLocal() as db:
        assert db.query(User).filter_by(oidc_sub="carol-sub").one().is_admin is False
        assert db.query(IntegrationToken).count() == 0
    assert client.get("/api/integration/v1/status", headers=auth_header(tok)).status_code == 401


def test_token_stops_working_when_creator_loses_admin_any_other_way(client):
    setup_admin(client)
    with SessionLocal() as db:
        db.add(User(username="dave", auth_provider="local", is_admin=True))
        db.commit()
        tok = _mint(db, "dave")
    assert client.get("/api/integration/v1/status", headers=auth_header(tok)).status_code == 200
    with SessionLocal() as db:
        db.query(User).filter_by(username="dave").one().is_admin = False  # not through the admin API
        db.commit()
    assert client.get("/api/integration/v1/status", headers=auth_header(tok)).status_code == 401
    with SessionLocal() as db:
        orphan = _mint(db, "nobody-by-that-name")
    assert client.get("/api/integration/v1/status", headers=auth_header(orphan)).status_code == 401


# ── Workspace limits ─────────────────────────────────────────────────────────


def _limited_bob(client):
    admin, _ = setup_admin(client)
    u = create_user_via_admin(client, admin, "bob")
    client.patch(f"/api/admin/users/{u['id']}", json={"max_running": 1}, headers=auth_header(admin))
    return login(client, "bob", "password123").json()["access_token"]


def _launch(client, bob, name):
    return client.post("/api/workspaces", json={"name": name, "image_id": add_image(name=f"i{name}")},
                       headers=auth_header(bob))


def _set(ws_id, **fields):
    with SessionLocal() as db:
        ws = db.get(Workspace, ws_id)
        for k, v in fields.items():
            setattr(ws, k, v)
        db.commit()


def _monitor_pass(monkeypatch):
    from unittest.mock import MagicMock

    from server import docker_manager as dm

    mgr = dm.DockerManager.__new__(dm.DockerManager)
    mgr.zone_id = 0
    mgr._client = MagicMock()
    mgr._client.containers.get.return_value = MagicMock(status="running")
    mgr._sidecar_failure = lambda ws: None
    mgr._wait_for_http_ready = lambda *a, **k: True
    mgr.queue_boot_apps = lambda ws: None
    monkeypatch.setattr(dm, "capture_preview_frame", MagicMock(return_value=b"jpg"))
    mgr.sync_workspace_statuses()


def test_recovering_error_workspace_cant_exceed_the_running_limit(client, monkeypatch):
    bob = _limited_bob(client)
    a = _launch(client, bob, "a").json()["id"]
    assert _launch(client, bob, "b").status_code == 409  # cap holds while A is creating
    # The monitor flipped A to error (its VPN sidecar is unhealthy); its
    # container keeps running, so A no longer holds a slot and B can start.
    old = datetime.now(timezone.utc) - timedelta(seconds=600)
    _set(a, status="error", container_id=f"cove-ws-{a}", status_changed_at=old)
    r = _launch(client, bob, "b2")
    assert r.status_code == 201, r.text
    _set(r.json()["id"], status="running")
    # A recovers: the monitor must not bring it back past the limit.
    _monitor_pass(monkeypatch)
    lim = client.get("/api/workspaces/limits", headers=auth_header(bob)).json()
    assert lim["running"] == 1, lim
    with SessionLocal() as db:
        ws = db.get(Workspace, a)
        assert ws.status == "error" and "running limit" in ws.error_message


def test_recovering_error_workspace_comes_back_when_there_is_room(client, monkeypatch):
    bob = _limited_bob(client)
    a = _launch(client, bob, "a").json()["id"]
    old = datetime.now(timezone.utc) - timedelta(seconds=600)
    _set(a, status="error", container_id=f"cove-ws-{a}", status_changed_at=old)
    _monitor_pass(monkeypatch)
    with SessionLocal() as db:
        assert db.get(Workspace, a).status == "running"


def test_crashed_workspace_doesnt_hold_a_running_slot(client):
    """A workspace in error with a dead container (its id is never cleared)
    must not stop the account starting another one, or restarting it."""
    bob = _limited_bob(client)
    a = _launch(client, bob, "a").json()["id"]
    _set(a, status="error", container_id="deadbeef")
    assert client.get("/api/workspaces/limits", headers=auth_header(bob)).json()["running"] == 0
    assert _launch(client, bob, "b").status_code == 201


# ── Thin clients ─────────────────────────────────────────────────────────────


def _kiosk(client):
    from server.tests.test_thin_client_sessions import _kiosk_session

    return _kiosk_session(client, hours=12)


def test_thin_client_setup_needs_https(client, monkeypatch):
    admin, _ = setup_admin(client)
    s = get_settings()
    monkeypatch.setattr(s, "thin_client_allow_http", False)
    from server.tests.test_thin_clients import _create

    dev = _create(client, admin, "desk")
    r = client.post(f"/api/admin/thin-clients/{dev['id']}/enroll-token", json={}, headers=auth_header(admin))
    assert r.status_code == 400 and "https://" in r.json()["detail"]
    # Behind TLS (or with COVE_APP_ORIGIN on https) it works, and so does the script.
    monkeypatch.setattr(s, "app_origin", "https://cove.example")
    r = client.post(f"/api/admin/thin-clients/{dev['id']}/enroll-token", json={}, headers=auth_header(admin))
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    assert r.json()["install_command"].startswith('curl -fsSL "https://cove.example/')
    assert client.get(f"/thin-client/install.sh?code={code}").status_code == 200
    monkeypatch.setattr(s, "app_origin", "http://cove.example")
    assert client.get(f"/thin-client/install.sh?code={code}").status_code == 400


def test_install_script_never_follows_redirects():
    from server.thin_client_install import render_install_script

    out = render_install_script(cp_url="https://cove.example", code="ABCD", name="desk")
    assert "urllib.request.urlopen" not in out
    assert "build_opener(NoRedirect)" in out


@pytest.mark.parametrize("action", ["revoke", "reset_2fa", "password", "delete", "logout"])
def test_revoking_a_user_wipes_the_kiosks_they_are_signed_in_on(client, action):
    from server.models import ThinClient
    from server.tests.test_thin_clients import _checkin

    admin, dev, dtoken, kiosk, k_token = _kiosk(client)
    assert _checkin(client, dtoken).json()["session_username"] == "alice"
    with SessionLocal() as db:
        alice_id = db.query(User).filter_by(username="alice").one().id
    h = auth_header(admin)
    if action == "revoke":
        r = client.post(f"/api/admin/users/{alice_id}/revoke-sessions", headers=h)
    elif action == "reset_2fa":
        r = client.post(f"/api/admin/users/{alice_id}/reset-2fa", headers=h)
    elif action == "password":
        r = client.patch(f"/api/admin/users/{alice_id}", json={"password": "another-pass-123"}, headers=h)
    elif action == "delete":
        r = client.delete(f"/api/admin/users/{alice_id}", headers=h)
    else:  # alice signs out on her laptop: that ends every session of hers
        laptop = TestClient(client.app)
        tok = login(laptop, "alice", "password123").json()["access_token"]
        r = laptop.post("/api/auth/logout", headers=auth_header(tok))
    assert r.status_code < 300, r.text
    out = _checkin(client, dtoken).json()
    assert out["session_username"] is None
    assert [(c["kind"], c["args"]) for c in out["commands"]] == [("restart_browser", {"reason": "user_signout"})]
    with SessionLocal() as db:
        assert db.get(ThinClient, dev["id"]).session_user_id is None
