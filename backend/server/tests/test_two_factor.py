"""Two-factor sign-in for local accounts: TOTP, recovery codes, requiring 2FA,
admin reset, and passkeys / security keys."""

import time

import pytest

from server import mfa, net
from server.config import get_settings
from server.db import SessionLocal
from server.models import AuditLog, User
from server.routers import auth as auth_router
from server.routers import mfa as mfa_router
from server.security import decrypt_secret
from server.tests.helpers import auth_header, create_user_via_admin, setup_admin
from server.tests.softkey import SoftKey, unb64u


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    # Passkeys are tied to COVE_APP_ORIGIN, never to the request's Host.
    monkeypatch.setattr(get_settings(), "app_origin", "http://testserver")
    auth_router._rate_buckets.clear()
    mfa._used_jti.clear()
    mfa._attempts.clear()
    mfa_router._failures.clear()
    net.set_trusted_proxies("")
    yield


def _login(client, name="bob", password="password123", headers=None):
    client.cookies.clear()
    return client.post("/api/auth/login", json={"username": name, "password": password}, headers=headers or {})


def _session(client, name="bob"):
    r = _login(client, name)
    assert r.status_code == 200 and "access_token" in r.json(), r.text
    return r.json()["access_token"]


def _secret(name="bob"):
    with SessionLocal() as db:
        u = db.query(User).filter(User.username == name).one()
        return decrypt_secret(u.totp_secret)


def _enable_totp(client, token, mfa_token=None):
    body = {"mfa_token": mfa_token} if mfa_token else {}
    setup = client.post("/api/auth/2fa/totp/setup", json=body, headers=auth_header(token) if token else {}).json()
    assert setup["uri"].startswith("otpauth://totp/") and setup["qr"].startswith("data:image/svg+xml;base64,")
    pw = {} if mfa_token else {"password": "password123"}
    r = client.post("/api/auth/2fa/totp/enable",
                    json={**body, **pw, "setup_token": setup["setup_token"], "code": mfa.totp_now(setup["secret"])},
                    headers=auth_header(token) if token else {})
    assert r.status_code == 200, r.text
    return setup["secret"], r.json()


def _bob(client):
    admin, _ = setup_admin(client)
    u = create_user_via_admin(client, admin, "bob")
    return admin, u


def _actions(action):
    with SessionLocal() as db:
        return [a.detail for a in db.query(AuditLog).filter(AuditLog.action == action)]


# ── TOTP ─────────────────────────────────────────────────────────────────────


def test_rfc6238_reference_vector():
    # RFC 6238 appendix B, SHA-1 seed "12345678901234567890", T=59 -> 94287082 (8 digits; we use the last 6).
    import base64

    secret = base64.b32encode(b"12345678901234567890").decode()
    assert mfa.totp_now(secret, at=59) == "287082"
    assert mfa.totp_now(secret, at=1111111109) == "081804"


def test_accounts_without_2fa_sign_in_as_before(client):
    _bob(client)
    assert "access_token" in _login(client).json()


def test_totp_turns_sign_in_into_two_steps(client):
    _bob(client)
    token = _session(client)
    secret, out = _enable_totp(client, token)
    assert len(out["recovery_codes"]) == 10 and len(set(out["recovery_codes"])) == 10

    r = _login(client)
    body = r.json()
    assert r.status_code == 200 and body["mfa_required"] is True and "access_token" not in body
    assert set(body["methods"]) == {"totp", "recovery"}
    assert "set-cookie" not in r.headers  # no session yet
    # The in-between token is not a session.
    assert client.get("/api/auth/me", headers=auth_header(body["mfa_token"])).status_code == 401

    code = mfa.totp_now(secret, time.time() + 30)  # next step: the setup code's step is spent
    r = client.post("/api/auth/login/2fa", json={"mfa_token": body["mfa_token"], "code": code})
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=auth_header(r.json()["access_token"])).status_code == 200
    assert "authenticator app" in _actions("login.success")

    # The token is single use, and the code can't be replayed on a new sign-in.
    assert client.post("/api/auth/login/2fa", json={"mfa_token": body["mfa_token"], "code": code}).status_code == 401
    again = _login(client).json()["mfa_token"]
    assert client.post("/api/auth/login/2fa", json={"mfa_token": again, "code": code}).status_code == 401


def test_wrong_codes_end_the_sign_in_and_then_lock_the_account(client):
    _bob(client)
    secret, _ = _enable_totp(client, _session(client))
    good = mfa.totp_now(secret, time.time() + 30)
    tok = _login(client).json()["mfa_token"]
    for i in range(mfa.MAX_ATTEMPTS):
        wrong = "000000" if good != "000000" else "111111"
        r = client.post("/api/auth/login/2fa", json={"mfa_token": tok, "code": wrong})
        assert r.status_code == 401
    assert r.json()["detail"] == "Too many wrong codes. Sign in again."
    assert client.post("/api/auth/login/2fa", json={"mfa_token": tok, "code": good}).status_code == 401

    # Across sign-ins: past the account limit, even the right code waits.
    for _ in range(mfa_router._ACCOUNT_FAILURES):
        auth_router._rate_buckets.clear()
        t = _login(client).json()["mfa_token"]
        client.post("/api/auth/login/2fa", json={"mfa_token": t, "code": "999999" if good != "999999" else "888888"})
    auth_router._rate_buckets.clear()
    t = _login(client).json()["mfa_token"]
    assert client.post("/api/auth/login/2fa", json={"mfa_token": t, "code": good}).status_code == 429


def test_recovery_codes_work_once(client):
    _bob(client)
    _, out = _enable_totp(client, _session(client))
    code = out["recovery_codes"][3]
    r = client.post("/api/auth/login/2fa", json={"mfa_token": _login(client).json()["mfa_token"], "code": code.lower()})
    assert r.status_code == 200 and r.json()["recovery_codes_left"] == 9
    r = client.post("/api/auth/login/2fa", json={"mfa_token": _login(client).json()["mfa_token"], "code": code})
    assert r.status_code == 401
    with SessionLocal() as db:
        stored = [rc.code_hash for rc in db.query(User).filter(User.username == "bob").one().recovery_codes]
    assert code.replace("-", "") not in str(stored)  # only hashes are kept


def test_setup_needs_a_matching_code_and_the_secret_is_encrypted(client):
    _bob(client)
    token = _session(client)
    setup = client.post("/api/auth/2fa/totp/setup", json={}, headers=auth_header(token)).json()
    r = client.post("/api/auth/2fa/totp/enable", json={"password": "password123",
                                                       "setup_token": setup["setup_token"], "code": "123456"
                                                       if mfa.totp_now(setup["secret"]) != "123456" else "654321"},
                    headers=auth_header(token))
    assert r.status_code == 400
    _enable_totp(client, token)
    with SessionLocal() as db:
        raw = db.query(User).filter(User.username == "bob").one().totp_secret
    assert raw and _secret() not in raw


def test_a_stolen_session_cannot_add_its_own_second_factor(client):
    # Without the password, a session can't lock its owner out by adding the
    # thief's authenticator or key.
    _bob(client)
    token = _session(client)
    setup = client.post("/api/auth/2fa/totp/setup", json={}, headers=auth_header(token)).json()
    for pw in ({}, {"password": "wrong-one"}):
        r = client.post("/api/auth/2fa/totp/enable",
                        json={**pw, "setup_token": setup["setup_token"], "code": mfa.totp_now(setup["secret"])},
                        headers=auth_header(token))
        assert r.status_code == 403
    key = SoftKey()
    opts = client.post("/api/auth/2fa/passkeys/options", json={}, headers=auth_header(token)).json()
    body = {"state": opts["state"], "credential": key.register(opts["options"])}
    r = client.post("/api/auth/2fa/passkeys", json=body, headers=auth_header(token))
    assert r.status_code == 403
    assert client.get("/api/auth/2fa", headers=auth_header(token)).json()["passkeys"] == []


def test_a_setup_sign_in_dies_once_the_account_has_a_factor(client):
    # Someone who knows the password signed in too and holds a setup token.
    # Once the owner has set up a factor, that token can't add the attacker's.
    admin, u = _bob(client)
    client.patch(f"/api/admin/users/{u['id']}", json={"require_2fa": True}, headers=auth_header(admin))
    attacker = _login(client).json()["mfa_token"]
    owner = _login(client).json()["mfa_token"]
    _enable_totp(client, None, mfa_token=owner)
    r = client.post("/api/auth/2fa/passkeys/options", json={"mfa_token": attacker})
    assert r.status_code == 401
    r = client.post("/api/auth/2fa/totp/setup", json={"mfa_token": attacker})
    assert r.status_code == 401


def test_the_first_factor_ends_password_only_sessions(client):
    # Sessions from before 2FA came from the password alone (maybe a thief's).
    _bob(client)
    elsewhere = _session(client)
    here = _session(client)
    _, body = _enable_totp(client, here)
    assert client.get("/api/auth/me", headers=auth_header(elsewhere)).status_code == 401
    assert client.get("/api/auth/me", headers=auth_header(body["access_token"])).status_code == 200
    # Adding a second factor later doesn't do it again.
    key = SoftKey()
    r = _register(client, body["access_token"], key)
    assert r.status_code == 200 and "access_token" not in r.json()
    assert client.get("/api/auth/me", headers=auth_header(body["access_token"])).status_code == 200


def test_racing_requests_cannot_use_a_recovery_code_twice(client, monkeypatch):
    import threading

    _bob(client)
    token = _session(client)
    _, body = _enable_totp(client, token)
    code = body["recovery_codes"][0]
    tokens = [_login(client).json()["mfa_token"] for _ in range(4)]
    real = mfa.use_recovery_code

    def slow(db, user, c):  # widen the gap between the check and the commit
        ok = real(db, user, c)
        time.sleep(0.3)
        return ok

    monkeypatch.setattr(mfa, "use_recovery_code", slow)
    barrier = threading.Barrier(len(tokens))
    results = []

    def go(tok):
        barrier.wait()
        results.append(client.post("/api/auth/login/2fa", json={"mfa_token": tok, "code": code}).status_code)

    threads = [threading.Thread(target=go, args=(tok,)) for tok in tokens]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(results) == [200, 401, 401, 401]


def test_a_password_change_voids_a_pending_sign_in(client):
    _bob(client)
    secret, _ = _enable_totp(client, _session(client))
    tok = _login(client).json()["mfa_token"]
    with SessionLocal() as db:
        from datetime import datetime, timedelta, timezone

        u = db.query(User).filter(User.username == "bob").one()
        u.tokens_valid_from = datetime.now(timezone.utc) + timedelta(seconds=1)
        db.commit()
    r = client.post("/api/auth/login/2fa", json={"mfa_token": tok, "code": mfa.totp_now(secret, time.time() + 30)})
    assert r.status_code == 401


def test_removing_totp_needs_the_password(client):
    _bob(client)
    token = _session(client)
    token = _enable_totp(client, token)[1]["access_token"]  # the first factor renews the session
    r = client.post("/api/auth/2fa/totp/disable", json={"password": "nope"}, headers=auth_header(token))
    # 403, not 401: a 401 would make the SPA sign the user out over a typo.
    assert r.status_code == 403
    r = client.post("/api/auth/2fa/totp/disable", json={"password": "password123"}, headers=auth_header(token))
    assert r.status_code == 200
    status = client.get("/api/auth/2fa", headers=auth_header(token)).json()
    assert status["totp"] is False and status["recovery_codes_left"] == 0
    assert "access_token" in _login(client).json()


# ── Requiring 2FA ────────────────────────────────────────────────────────────


def test_required_2fa_is_set_up_during_sign_in(client):
    admin, u = _bob(client)
    client.patch(f"/api/admin/users/{u['id']}", json={"require_2fa": True}, headers=auth_header(admin))
    r = _login(client)
    body = r.json()
    assert body["mfa_setup_required"] is True and "access_token" not in body
    # It's only good for setting up a factor.
    r = client.post("/api/auth/login/2fa", json={"mfa_token": body["mfa_token"], "code": "123456"})
    assert r.status_code == 401
    assert client.get("/api/auth/me", headers=auth_header(body["mfa_token"])).status_code == 401

    _, out = _enable_totp(client, None, mfa_token=body["mfa_token"])
    assert out["access_token"] and len(out["recovery_codes"]) == 10  # signed in, codes shown
    assert client.get("/api/auth/me", headers=auth_header(out["access_token"])).json()["totp_enabled"] is True
    # Required: can't remove the only factor.
    r = client.post("/api/auth/2fa/totp/disable", json={"password": "password123"},
                    headers=auth_header(out["access_token"]))
    assert r.status_code == 400


def test_require_for_everyone_skips_sso_accounts(client):
    admin, _ = _bob(client)
    client.put("/api/admin/settings", json={"require_2fa_all": True}, headers=auth_header(admin))
    assert _login(client).json().get("mfa_setup_required") is True
    with SessionLocal() as db:
        sso = User(username="sso", auth_provider="oidc", oidc_sub="x")
        db.add(sso)
        db.commit()
        assert mfa.required(db, sso) is False


def test_admin_reset_removes_every_factor_and_signs_out(client):
    admin, u = _bob(client)
    token = _session(client)
    _enable_totp(client, token)
    r = client.post(f"/api/admin/users/{u['id']}/reset-2fa", headers=auth_header(admin))
    assert r.status_code == 200 and r.json()["totp_enabled"] is False
    assert client.get("/api/auth/me", headers=auth_header(token)).status_code == 401
    assert "access_token" in _login(client).json()
    assert _actions("admin.user.reset_2fa") == ["bob"]
    me_user = create_user_via_admin(client, admin, "eve")
    r = client.post(f"/api/admin/users/{me_user['id']}/reset-2fa", headers=auth_header(_session(client, "eve")))
    assert r.status_code == 403


def test_lan_only_still_applies_at_the_second_step(client):
    admin, u = _bob(client)
    secret, _ = _enable_totp(client, _session(client))
    client.patch(f"/api/admin/users/{u['id']}", json={"lan_only": True},
                 headers={**auth_header(admin), "X-Forwarded-For": "192.168.1.2"})
    tok = _login(client, headers={"X-Forwarded-For": "192.168.1.2"}).json()["mfa_token"]
    r = client.post("/api/auth/login/2fa", json={"mfa_token": tok, "code": mfa.totp_now(secret, time.time() + 30)},
                    headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.status_code == 403


# ── Passkeys ─────────────────────────────────────────────────────────────────


def _register(client, token, key, name="YubiKey", mfa_token=None):
    body = {"mfa_token": mfa_token} if mfa_token else {}
    h = auth_header(token) if token else {}
    opts = client.post("/api/auth/2fa/passkeys/options", json=body, headers=h).json()
    assert opts["options"]["authenticatorSelection"]["userVerification"] == "required"
    pw = {} if mfa_token else {"password": "password123"}
    cred = key.register(opts["options"])
    r = client.post("/api/auth/2fa/passkeys",
                    json={**body, **pw, "state": opts["state"], "credential": cred, "name": name}, headers=h)
    return r


def _passkey_login(client, key, name="bob", **kw):
    opts = client.post("/api/auth/login/passkey/options", json={"username": name}).json()
    body = {"state": opts["state"], "credential": key.assert_(opts["options"], **kw)}
    return client.post("/api/auth/login/passkey", json=body)


def test_a_passkey_signs_in_on_its_own(client):
    _bob(client)
    token = _session(client)
    token = _enable_totp(client, token)[1]["access_token"]  # TOTP too: a passkey still skips it
    key = SoftKey()
    r = _register(client, token, key)
    assert r.status_code == 200 and r.json()["passkey"]["name"] == "YubiKey"
    assert "recovery_codes" not in r.json()  # TOTP already made them

    opts = client.post("/api/auth/login/passkey/options", json={"username": "bob"}).json()["options"]
    assert [unb64u(c["id"]) for c in opts["allowCredentials"]] == [key.cred_id]
    client.cookies.clear()
    r = _passkey_login(client, key)
    assert r.status_code == 200 and "access_token" in r.json()
    status = client.get("/api/auth/2fa", headers=auth_header(r.json()["access_token"])).json()
    assert status["passkeys"][0]["last_used_at"] and status["passkeys"][0]["transports"] == ["usb"]
    # After a password, a passkey is offered as the second step too.
    assert "passkey" in _login(client).json()["methods"]


def test_passkey_options_do_not_reveal_accounts(client):
    _bob(client)
    a = client.post("/api/auth/login/passkey/options", json={"username": "nobody"}).json()["options"]
    b = client.post("/api/auth/login/passkey/options", json={"username": "nobody"}).json()["options"]
    c = client.post("/api/auth/login/passkey/options", json={"username": "bob"}).json()["options"]
    assert a["allowCredentials"] == b["allowCredentials"]  # stable per name
    assert c["allowCredentials"] != a["allowCredentials"] and a.keys() == c.keys()
    # Made-up entries look like real ones: transports, and differing counts
    # and id lengths (a fixed shape would mark the accounts with real keys).
    seen = []
    for i in range(40):
        auth_router._rate_buckets.clear()
        seen.append(client.post("/api/auth/login/passkey/options", json={"username": f"user{i}"}).json()["options"])
    entries = [e for o in seen for e in o["allowCredentials"]]
    assert all(e.get("transports") for e in entries)
    assert {len(o["allowCredentials"]) for o in seen} == {1, 2}
    assert len({len(unb64u(e["id"])) for e in entries}) > 1


def test_passkeys_need_cove_s_address(client, monkeypatch):
    # Without COVE_APP_ORIGIN the request's Host would pick the relying party:
    # a proxy under another name could get passkeys made for its own domain.
    _bob(client)
    token = _session(client)
    for origin in (None, "https://192.168.0.10"):
        monkeypatch.setattr(get_settings(), "app_origin", origin)
        assert client.get("/api/auth/config").json()["passkeys"] is False
        r = client.post("/api/auth/2fa/passkeys/options", json={}, headers=auth_header(token))
        assert r.status_code == 400 and "COVE_APP_ORIGIN" in r.json()["detail"]
        r = client.post("/api/auth/login/passkey/options", json={"username": "bob"})
        assert r.status_code == 400
        assert client.get("/api/auth/2fa", headers=auth_header(token)).json()["passkeys_enabled"] is False
    monkeypatch.setattr(get_settings(), "app_origin", "https://cove.example.com")
    assert client.get("/api/auth/config").json()["passkeys"] is True
    opts = client.post("/api/auth/2fa/passkeys/options", json={}, headers=auth_header(token)).json()["options"]
    assert opts["rp"]["id"] == "cove.example.com"
    # A Host header doesn't move it.
    h = {**auth_header(token), "Host": "evil.example"}
    opts = client.post("/api/auth/2fa/passkeys/options", json={}, headers=h)
    assert opts.json()["options"]["rp"]["id"] == "cove.example.com"


def test_passkey_sign_in_refuses_the_wrong_key_account_or_replay(client):
    admin, _ = _bob(client)
    create_user_via_admin(client, admin, "carol")
    bob_key, carol_key = SoftKey(), SoftKey()
    assert _register(client, _session(client, "bob"), bob_key).status_code == 200
    assert _register(client, _session(client, "carol"), carol_key).status_code == 200

    # Carol's key against options for bob.
    assert _passkey_login(client, carol_key, name="bob").status_code == 401
    # No user verification (no PIN / fingerprint).
    assert _passkey_login(client, bob_key, uv=False).status_code == 401
    # Another site's origin.
    assert _passkey_login(client, bob_key, origin="https://evil.example").status_code == 401
    # The same options (challenge) twice.
    opts = client.post("/api/auth/login/passkey/options", json={"username": "bob"}).json()
    ok = {"state": opts["state"], "credential": bob_key.assert_(opts["options"])}
    assert client.post("/api/auth/login/passkey", json=ok).status_code == 200
    assert client.post("/api/auth/login/passkey", json=ok).status_code == 401


def test_a_signed_challenge_works_once_even_without_a_counter(client):
    # The signature counter catches a replay only on keys that count; the
    # single-use challenge has to catch it on the rest.
    _bob(client)
    key = SoftKey(counts=False)
    assert _register(client, _session(client), key).status_code == 200
    opts = client.post("/api/auth/login/passkey/options", json={"username": "bob"}).json()
    ok = {"state": opts["state"], "credential": key.assert_(opts["options"])}
    assert client.post("/api/auth/login/passkey", json=ok).status_code == 200
    assert client.post("/api/auth/login/passkey", json=ok).status_code == 401
    assert _passkey_login(client, key).status_code == 200  # a fresh challenge still works


def test_registration_requires_user_verification_and_rejects_duplicates(client):
    _bob(client)
    token = _session(client)
    key = SoftKey()
    opts = client.post("/api/auth/2fa/passkeys/options", json={}, headers=auth_header(token)).json()
    body = {"state": opts["state"], "credential": key.register(opts["options"], uv=False), "password": "password123"}
    r = client.post("/api/auth/2fa/passkeys", json=body, headers=auth_header(token))
    assert r.status_code == 400
    r = _register(client, token, key)
    assert r.status_code == 200
    token = r.json()["access_token"]
    out = client.post("/api/auth/2fa/passkeys/options", json={}, headers=auth_header(token)).json()["options"]
    assert [unb64u(c["id"]) for c in out["excludeCredentials"]] == [key.cred_id]


def test_first_passkey_brings_recovery_codes_and_can_finish_required_setup(client):
    admin, u = _bob(client)
    client.patch(f"/api/admin/users/{u['id']}", json={"require_2fa": True}, headers=auth_header(admin))
    tok = _login(client).json()["mfa_token"]
    r = _register(client, None, SoftKey(), mfa_token=tok)
    assert r.status_code == 200 and r.json()["access_token"] and len(r.json()["recovery_codes"]) == 10


def test_removing_a_passkey_needs_the_password_and_keeps_one_when_required(client):
    admin, u = _bob(client)
    token = _session(client)
    token = _register(client, token, SoftKey()).json()["access_token"]
    pk = client.get("/api/auth/2fa", headers=auth_header(token)).json()["passkeys"][0]["id"]
    r = client.post(f"/api/auth/2fa/passkeys/{pk}/delete", json={"password": "x"}, headers=auth_header(token))
    assert r.status_code == 403
    client.patch(f"/api/admin/users/{u['id']}", json={"require_2fa": True}, headers=auth_header(admin))
    r = client.post(f"/api/auth/2fa/passkeys/{pk}/delete", json={"password": "password123"}, headers=auth_header(token))
    assert r.status_code == 400
    client.patch(f"/api/admin/users/{u['id']}", json={"require_2fa": False}, headers=auth_header(admin))
    r = client.post(f"/api/auth/2fa/passkeys/{pk}/delete", json={"password": "password123"}, headers=auth_header(token))
    assert r.status_code == 200
    status = client.get("/api/auth/2fa", headers=auth_header(token)).json()
    assert status["passkeys"] == [] and status["recovery_codes_left"] == 0


def test_thin_clients_are_steered_to_security_keys(client):
    from server import thin_clients

    _bob(client)
    plain = client.post("/api/auth/login/passkey/options", json={"username": "bob"}).json()["options"]
    assert "hints" not in plain
    client.cookies.set(thin_clients.BIND_COOKIE, "x")
    kiosk = client.post("/api/auth/login/passkey/options", json={"username": "bob"}).json()["options"]
    assert kiosk["hints"] == ["security-key"]


def test_sso_accounts_cannot_add_local_2fa(client):
    _bob(client)
    with SessionLocal() as db:
        db.add(User(username="sso", auth_provider="oidc", oidc_sub="s1"))
        db.commit()
        uid = db.query(User).filter(User.username == "sso").one().id
    from server.security import create_access_token

    r = client.post("/api/auth/2fa/totp/setup", json={}, headers=auth_header(create_access_token(uid, False)))
    assert r.status_code == 400
