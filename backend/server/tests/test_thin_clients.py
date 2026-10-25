"""Thin clients: enrolment, the device protocol, browser binding and the
device-scoped sessions that let a kiosk sign out without signing its user out
everywhere else."""

import asyncio
import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from server import thin_clients as tc
from server.config import get_settings
from server.db import SessionLocal
from server.models import ThinClient, ThinClientCommand
from server.tests.helpers import auth_header, create_user_via_admin, login, setup_admin

# ── helpers ─────────────────────────────────────────────────────────────────


def _create(client, admin_token, name="Workbench"):
    r = client.post("/api/admin/thin-clients", json={"name": name, "location": "downstairs"},
                    headers=auth_header(admin_token))
    assert r.status_code == 201, r.text
    return r.json()


def _enroll(client, admin_token, name="Workbench"):
    """Create + enrol a device; returns (device json, device token)."""
    dev = _create(client, admin_token, name)
    r = client.post(f"/api/admin/thin-clients/{dev['id']}/enroll-token", json={},
                    headers=auth_header(admin_token))
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    r = client.post(f"/api/thin-client/enroll?code={code}", json={"hostname": "tc-1"})
    assert r.status_code == 200, r.text
    return dev, r.json()["device_token"]


def _dev(token):
    return {"Authorization": f"Bearer {token}"}


def _checkin(client, token, **body):
    body.setdefault("state", {"agent_version": "test"})
    return client.post("/api/thin-client/checkin", json=body, headers=_dev(token))


def _bind(client, token):
    """Mint a ticket and open the bind URL in this TestClient (the 'kiosk')."""
    r = client.post("/api/thin-client/ticket", headers=_dev(token))
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    path = url.split("://", 1)[1].split("/", 1)[1]
    r = client.get("/" + path, follow_redirects=False)
    assert r.status_code == 303, r.text
    assert tc.BIND_COOKIE in r.cookies
    return r


def _claims(token):
    return jwt.decode(token, options={"verify_signature": False})


# ── admin ───────────────────────────────────────────────────────────────────


def test_admin_crud_and_policy(client):
    token, _ = setup_admin(client)
    dev = _create(client, token)
    assert dev["status"] == "pending" and dev["online"] is False
    assert dev["policy"]["screen_off_minutes"] == 15

    r = client.patch(
        f"/api/admin/thin-clients/{dev['id']}",
        json={"name": "Rack", "policy": {"signout_minutes": 10, "rotation": "90",
                                         "extra_allowed_hosts": ["KVM.lan", "*.example.com"],
                                         "on_hours_start": "07:00", "on_hours_end": "23:30"}},
        headers=auth_header(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Rack"
    assert body["policy"]["signout_minutes"] == 10
    assert body["policy"]["extra_allowed_hosts"] == ["kvm.lan", "*.example.com"]
    assert body["policy_version"] == dev["policy_version"] + 1

    assert len(client.get("/api/admin/thin-clients", headers=auth_header(token)).json()) == 1
    assert client.delete(f"/api/admin/thin-clients/{dev['id']}", headers=auth_header(token)).status_code == 204
    assert client.get("/api/admin/thin-clients", headers=auth_header(token)).json() == []


@pytest.mark.parametrize("policy", [
    {"extra_allowed_hosts": ["https://kvm.lan"]},
    {"extra_allowed_hosts": ["kvm.lan:8443"]},
    {"extra_allowed_hosts": ["kvm lan"]},
    {"on_hours_start": "25:00"},
    {"rotation": "45"},
    {"screen_off_minutes": -1},
])
def test_policy_validation(client, policy):
    token, _ = setup_admin(client)
    dev = _create(client, token)
    r = client.patch(f"/api/admin/thin-clients/{dev['id']}", json={"policy": policy}, headers=auth_header(token))
    assert r.status_code == 422, r.text


def test_non_admin_cannot_manage(client):
    admin, _ = setup_admin(client)
    create_user_via_admin(client, admin, "bob")
    bob = login(client, "bob", "password123").json()["access_token"]
    assert client.get("/api/admin/thin-clients", headers=auth_header(bob)).status_code == 403
    assert client.post("/api/admin/thin-clients", json={"name": "x"}, headers=auth_header(bob)).status_code == 403


# ── enrolment ───────────────────────────────────────────────────────────────


def test_enroll_code_flow(client):
    token, _ = setup_admin(client)
    dev = _create(client, token)
    r = client.post(f"/api/admin/thin-clients/{dev['id']}/enroll-token", json={"ttl_hours": 24},
                    headers=auth_header(token))
    out = r.json()
    code = out["code"]
    assert len(code) == 19 and code.count("-") == 3
    assert f"/thin-client/install.sh?code={code}" in out["install_command"]

    # The install script renders for a valid code (and tolerates sloppy typing).
    sloppy = code.lower().replace("-", " ")
    r = client.get("/thin-client/install.sh", params={"code": sloppy})
    assert r.status_code == 200
    assert "agent.py\" install" in r.text and tc.normalize_code(code) in r.text
    assert client.get("/thin-client/install.sh", params={"code": "AAAA-BBBB-CCCC-DDDD"}).status_code == 404

    r = client.post(f"/api/thin-client/enroll?code={code}", json={"hostname": "bench"})
    assert r.status_code == 200, r.text
    assert r.json()["device_token"].startswith("ctc_")
    # Single use.
    assert client.post(f"/api/thin-client/enroll?code={code}", json={}).status_code == 403
    assert client.get("/thin-client/install.sh", params={"code": code}).status_code == 404

    listed = client.get("/api/admin/thin-clients", headers=auth_header(token)).json()[0]
    assert listed["status"] == "enrolled" and listed["state"]["hostname"] == "bench"


def test_expired_code_refused(client):
    token, _ = setup_admin(client)
    dev = _create(client, token)
    code = client.post(f"/api/admin/thin-clients/{dev['id']}/enroll-token", json={},
                       headers=auth_header(token)).json()["code"]
    with SessionLocal() as db:
        row = db.get(ThinClient, dev["id"])
        row.enroll_token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    assert client.post(f"/api/thin-client/enroll?code={code}", json={}).status_code == 403


def test_reenrol_replaces_device_token(client):
    admin, _ = setup_admin(client)
    dev, old = _enroll(client, admin)
    code = client.post(f"/api/admin/thin-clients/{dev['id']}/enroll-token", json={},
                       headers=auth_header(admin)).json()["code"]
    # Minting a code alone doesn't cut the running device off...
    assert _checkin(client, old).status_code == 200
    new = client.post(f"/api/thin-client/enroll?code={code}", json={}).json()["device_token"]
    # ...using it does.
    assert _checkin(client, old).status_code == 401
    assert _checkin(client, new).status_code == 200


# ── device protocol ─────────────────────────────────────────────────────────


def test_checkin_records_state_and_returns_policy(client):
    admin, _ = setup_admin(client)
    dev, token = _enroll(client, admin)
    assert _checkin(client, "ctc_nope").status_code == 401

    r = _checkin(client, token, state={"agent_version": "1.0.0", "display_on": True}, applied_policy_version=None)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Workbench"
    assert body["policy"]["signout_minutes"] == 30
    assert ".testserver" in body["allowed_hosts"]
    bundle = tc.agent_bundle()
    assert bundle is not None and body["agent_sha256"] == bundle[1]

    listed = client.get(f"/api/admin/thin-clients/{dev['id']}", headers=auth_header(admin)).json()
    assert listed["online"] is True
    assert listed["state"]["display_on"] is True
    assert listed["applied_policy_version"] is None
    assert listed["latest_agent_version"] == bundle[2]


def test_allowed_hosts_cover_workspace_domain_and_extras(client, monkeypatch):
    admin, _ = setup_admin(client)
    dev, token = _enroll(client, admin)
    monkeypatch.setattr(get_settings(), "workspace_domain", "ws.example.com")
    client.patch(f"/api/admin/thin-clients/{dev['id']}",
                 json={"policy": {"extra_allowed_hosts": ["kvm.lan", "*.docs.example.org"]}},
                 headers=auth_header(admin))
    hosts = _checkin(client, token).json()["allowed_hosts"]
    assert hosts == [".testserver", "ws.example.com", ".kvm.lan", "docs.example.org"]


def test_command_round_trip(client):
    admin, _ = setup_admin(client)
    dev, token = _enroll(client, admin)
    r = client.post(f"/api/admin/thin-clients/{dev['id']}/commands",
                    json={"kind": "logs", "args": {"lines": 99999, "unit": "agent", "evil": "x"}},
                    headers=auth_header(admin))
    assert r.status_code == 201, r.text
    assert r.json()["args"] == {"lines": 2000, "unit": "agent"}  # clamped, unknown args dropped

    cmds = _checkin(client, token).json()["commands"]
    assert [c["kind"] for c in cmds] == ["logs"]
    assert _checkin(client, token).json()["commands"] == []  # delivered once

    r = client.post(f"/api/thin-client/commands/{cmds[0]['id']}/result", json={"ok": True, "output": "hello"},
                    headers=_dev(token))
    assert r.status_code == 204
    hist = client.get(f"/api/admin/thin-clients/{dev['id']}/commands", headers=auth_header(admin)).json()
    assert hist[0]["status"] == "done" and hist[0]["result"] == "hello"


def test_unknown_command_and_unenrolled_device(client):
    admin, _ = setup_admin(client)
    pending = _create(client, admin, "Pending")
    r = client.post(f"/api/admin/thin-clients/{pending['id']}/commands", json={"kind": "reboot"},
                    headers=auth_header(admin))
    assert r.status_code == 409
    dev, _ = _enroll(client, admin)
    r = client.post(f"/api/admin/thin-clients/{dev['id']}/commands", json={"kind": "rm -rf /"},
                    headers=auth_header(admin))
    assert r.status_code == 400


def test_device_cannot_answer_another_devices_command(client):
    admin, _ = setup_admin(client)
    a, a_tok = _enroll(client, admin, "A")
    _, b_tok = _enroll(client, admin, "B")
    cmd = client.post(f"/api/admin/thin-clients/{a['id']}/commands", json={"kind": "screen_on"},
                      headers=auth_header(admin)).json()
    r = client.post(f"/api/thin-client/commands/{cmd['id']}/result", json={"ok": True}, headers=_dev(b_tok))
    assert r.status_code == 404


def test_screenshot_upload(client):
    admin, _ = setup_admin(client)
    dev, token = _enroll(client, admin)
    bad = client.post("/api/thin-client/screenshot", content=b"GIF89a", headers=_dev(token))
    assert bad.status_code == 400
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    assert client.post("/api/thin-client/screenshot", content=png, headers=_dev(token)).status_code == 204
    r = client.get(f"/api/admin/thin-clients/{dev['id']}/screenshot.png", headers=auth_header(admin))
    assert r.status_code == 200 and r.content == png


def test_agent_download_matches_checksum(client):
    admin, _ = setup_admin(client)
    _, token = _enroll(client, admin)
    r = client.get("/api/thin-client/agent", headers=_dev(token))
    assert r.status_code == 200
    assert hashlib.sha256(r.content).hexdigest() == r.headers["X-Agent-Sha256"]
    assert b"AGENT_VERSION" in r.content
    assert client.get("/api/thin-client/agent").status_code == 401


def test_deleted_device_is_cut_off(client):
    admin, _ = setup_admin(client)
    dev, token = _enroll(client, admin)
    client.delete(f"/api/admin/thin-clients/{dev['id']}", headers=auth_header(admin))
    assert _checkin(client, token).status_code == 401
    assert client.post("/api/thin-client/ticket", headers=_dev(token)).status_code == 401


async def test_long_poll_wakes_on_new_command():
    """A waiting check-in returns as soon as an admin queues a command."""
    import httpx

    from server.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        r = await ac.post("/api/auth/setup", json={"username": "admin", "password": "password123"})
        admin = r.json()["access_token"]
        h = {"Authorization": f"Bearer {admin}"}
        dev = (await ac.post("/api/admin/thin-clients", json={"name": "LP"}, headers=h)).json()
        code = (await ac.post(f"/api/admin/thin-clients/{dev['id']}/enroll-token", json={}, headers=h)).json()["code"]
        token = (await ac.post(f"/api/thin-client/enroll?code={code}", json={})).json()["device_token"]
        dh = {"Authorization": f"Bearer {token}"}
        first = (await ac.post("/api/thin-client/checkin", json={"state": {}}, headers=dh)).json()
        version = first["policy_version"]

        # Nothing to do: the poll is held open.
        t0 = time.monotonic()
        quiet = await ac.post("/api/thin-client/checkin",
                              json={"state": {}, "applied_policy_version": version, "wait": 1}, headers=dh)
        assert quiet.json()["commands"] == [] and time.monotonic() - t0 >= 0.9

        async def queue_later():
            await asyncio.sleep(0.3)
            await ac.post(f"/api/admin/thin-clients/{dev['id']}/commands", json={"kind": "screen_off"}, headers=h)

        t0 = time.monotonic()
        poll, _ = await asyncio.gather(
            ac.post("/api/thin-client/checkin",
                    json={"state": {}, "applied_policy_version": version, "wait": 20}, headers=dh),
            queue_later(),
        )
        assert [c["kind"] for c in poll.json()["commands"]] == ["screen_off"]
        assert time.monotonic() - t0 < 5


def test_notify_is_thread_safe_without_waiters():
    tc.notify(424242)  # nothing waiting: a no-op, never raises
    t = threading.Thread(target=tc.notify, args=(424242,))
    t.start()
    t.join()


# ── binding and device-scoped sessions ──────────────────────────────────────


def test_bind_ticket_is_single_use_and_fails_closed(client):
    admin, _ = setup_admin(client)
    _, token = _enroll(client, admin)
    url = client.post("/api/thin-client/ticket", headers=_dev(token)).json()["url"]
    path = "/" + url.split("://", 1)[1].split("/", 1)[1]
    first = client.get(path, follow_redirects=False)
    assert first.status_code == 303 and first.headers["location"] == "/app"
    again = client.get(path, follow_redirects=False)
    assert again.status_code == 403 and "BINDING FAILED" in again.text
    assert _checkin(client, token).json()["bound_at"] is not None


def test_self_endpoint(client):
    admin, _ = setup_admin(client)
    _, token = _enroll(client, admin)
    assert client.get("/api/thin-client/self").status_code == 404
    _bind(client, token)
    body = client.get("/api/thin-client/self").json()
    assert body["name"] == "Workbench" and body["auto_open"] == "running"


def test_login_on_kiosk_is_device_scoped(client):
    from fastapi.testclient import TestClient

    admin, _ = setup_admin(client)
    create_user_via_admin(client, admin, "alice")
    dev, dtoken = _enroll(client, admin)

    # Alice is signed in upstairs (a normal browser)...
    upstairs = TestClient(client.app)
    up_token = login(upstairs, "alice", "password123").json()["access_token"]
    assert "tc" not in _claims(up_token)

    # ...and on the kiosk downstairs.
    kiosk = TestClient(client.app)
    _bind(kiosk, dtoken)
    k = login(kiosk, "alice", "password123")
    k_token = k.json()["access_token"]
    claims = _claims(k_token)
    assert claims["tc"] == dev["public_id"] and "sst" in claims
    assert _claims(kiosk.cookies.get(get_settings().cookie_refresh_name))["tc"] == dev["public_id"]
    assert kiosk.get("/api/auth/me", headers=auth_header(k_token)).status_code == 200
    assert _checkin(client, dtoken).json()["session_username"] == "alice"

    # Refreshing keeps the device and the original session start.
    r = kiosk.post("/api/auth/refresh")
    assert r.status_code == 200
    assert _claims(r.json()["access_token"])["sst"] == claims["sst"]

    # The kiosk times out (the agent reports it): only the kiosk is signed out.
    time.sleep(0.01)
    assert client.post("/api/thin-client/signed-out", headers=_dev(dtoken)).status_code == 204
    assert kiosk.get("/api/auth/me", headers=auth_header(k_token)).status_code == 401
    assert kiosk.post("/api/auth/refresh").status_code == 401
    assert upstairs.get("/api/auth/me", headers=auth_header(up_token)).status_code == 200
    assert _checkin(client, dtoken).json()["session_username"] is None


def test_kiosk_logout_only_signs_out_the_kiosk(client):
    from fastapi.testclient import TestClient

    admin, _ = setup_admin(client)
    create_user_via_admin(client, admin, "alice")
    dev, dtoken = _enroll(client, admin)
    upstairs = TestClient(client.app)
    up_token = login(upstairs, "alice", "password123").json()["access_token"]
    kiosk = TestClient(client.app)
    _bind(kiosk, dtoken)
    k_token = login(kiosk, "alice", "password123").json()["access_token"]

    time.sleep(0.01)
    assert kiosk.post("/api/auth/logout", headers=auth_header(k_token)).status_code == 200
    assert kiosk.get("/api/auth/me", headers=auth_header(k_token)).status_code == 401
    assert upstairs.get("/api/auth/me", headers=auth_header(up_token)).status_code == 200
    # ...and the device is told to reset its browser.
    kinds = [c["kind"] for c in _checkin(client, dtoken).json()["commands"]]
    assert kinds == ["restart_browser"]


def test_admin_signout_and_ordinary_logout(client):
    from fastapi.testclient import TestClient

    admin, _ = setup_admin(client)
    create_user_via_admin(client, admin, "alice")
    dev, dtoken = _enroll(client, admin)
    kiosk = TestClient(client.app)
    _bind(kiosk, dtoken)
    k_token = login(kiosk, "alice", "password123").json()["access_token"]
    time.sleep(0.01)
    r = client.post(f"/api/admin/thin-clients/{dev['id']}/signout", headers=auth_header(admin))
    assert r.status_code == 200 and r.json()["session_username"] is None
    assert kiosk.get("/api/auth/me", headers=auth_header(k_token)).status_code == 401
    # The old browser binding is revoked too: a reload can't start a new device
    # session on it (the agent rebinds a fresh browser).
    assert kiosk.get("/api/thin-client/self").status_code == 404

    # An ordinary (non-kiosk) logout still signs the user out everywhere,
    # kiosks included.
    _bind(kiosk, dtoken)
    k_token = login(kiosk, "alice", "password123").json()["access_token"]
    other = TestClient(client.app)
    o_token = login(other, "alice", "password123").json()["access_token"]
    time.sleep(0.01)
    other.post("/api/auth/logout", headers=auth_header(o_token))
    assert kiosk.get("/api/auth/me", headers=auth_header(k_token)).status_code == 401


def test_session_cap_ends_kiosk_sessions(client):
    from fastapi.testclient import TestClient

    admin, _ = setup_admin(client)
    create_user_via_admin(client, admin, "alice")
    dev, dtoken = _enroll(client, admin)
    client.patch(f"/api/admin/thin-clients/{dev['id']}", json={"policy": {"session_max_hours": 1}},
                 headers=auth_header(admin))
    kiosk = TestClient(client.app)
    _bind(kiosk, dtoken)
    k_token = login(kiosk, "alice", "password123").json()["access_token"]
    claims = _claims(k_token)
    assert claims["exp"] <= claims["sst"] + 3600 + 1

    # A device session that started two hours ago is past its one-hour cap.
    settings = get_settings()
    now = datetime.now(timezone.utc)
    stale = jwt.encode(
        {"sub": claims["sub"], "type": "refresh", "iat": now.timestamp(), "exp": now + timedelta(days=1),
         "tc": dev["public_id"], "sst": (now - timedelta(hours=2)).timestamp()},
        settings.get_secret_key(), algorithm=settings.jwt_algorithm,
    )
    kiosk.cookies.set(settings.cookie_refresh_name, stale, path="/api/auth")
    assert kiosk.post("/api/auth/refresh").status_code == 401


def test_change_password_on_kiosk_stays_device_scoped(client):
    from fastapi.testclient import TestClient

    admin, _ = setup_admin(client)
    create_user_via_admin(client, admin, "alice")
    dev, dtoken = _enroll(client, admin)
    kiosk = TestClient(client.app)
    _bind(kiosk, dtoken)
    k_token = login(kiosk, "alice", "password123").json()["access_token"]
    sst = _claims(k_token)["sst"]
    r = kiosk.post("/api/auth/change-password", json={"current_password": "password123",
                                                      "new_password": "password456"},
                   headers=auth_header(k_token))
    assert r.status_code == 200, r.text
    new = _claims(r.json()["access_token"])
    assert new["tc"] == dev["public_id"] and new["sst"] == sst


def test_deleting_device_ends_its_sessions(client):
    from fastapi.testclient import TestClient

    admin, _ = setup_admin(client)
    create_user_via_admin(client, admin, "alice")
    dev, dtoken = _enroll(client, admin)
    kiosk = TestClient(client.app)
    _bind(kiosk, dtoken)
    k_token = login(kiosk, "alice", "password123").json()["access_token"]
    client.delete(f"/api/admin/thin-clients/{dev['id']}", headers=auth_header(admin))
    assert kiosk.get("/api/auth/me", headers=auth_header(k_token)).status_code == 401


def test_keep_awake_bumps_policy_version(client):
    admin, _ = setup_admin(client)
    dev, token = _enroll(client, admin)
    v = _checkin(client, token).json()["policy_version"]
    r = client.post(f"/api/admin/thin-clients/{dev['id']}/keep-awake", json={"minutes": 120},
                    headers=auth_header(admin))
    assert r.status_code == 200 and r.json()["keep_awake_until"]
    body = _checkin(client, token, applied_policy_version=v).json()
    assert body["policy_version"] == v + 1 and body["keep_awake_until"]


def test_workspace_options_lists_owners(client):
    from server.tests.helpers import add_image

    admin, _ = setup_admin(client)
    image_id = add_image()
    r = client.post("/api/workspaces", json={"name": "kvm", "image_id": image_id}, headers=auth_header(admin))
    assert r.status_code in (200, 201), r.text
    opts = client.get("/api/admin/thin-clients/workspace-options", headers=auth_header(admin)).json()
    assert [(o["name"], o["username"]) for o in opts] == [("kvm", "admin")]


def test_code_normalisation():
    code = tc.new_enroll_code()
    assert tc.hash_code(code) == tc.hash_code(code.lower().replace("-", ""))
    assert tc.normalize_code("oil-") == "011"


def test_stored_state_is_size_capped(client):
    admin, _ = setup_admin(client)
    dev, token = _enroll(client, admin)
    _checkin(client, token, state={"hostname": "small"})
    _checkin(client, token, state={"blob": "x" * (tc.STATE_MAX_BYTES + 10)})
    with SessionLocal() as db:
        assert json.loads(db.get(ThinClient, dev["id"]).state_json)["hostname"] == "small"
        assert db.query(ThinClientCommand).count() == 0


def _self_signed(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cove.lan")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(now).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path = tmp_path / "cove.crt"
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    import base64

    return path, "sha256//" + base64.b64encode(hashlib.sha256(spki).digest()).decode()


def test_private_certificate_is_pinned_and_shipped(client, monkeypatch, tmp_path):
    import subprocess

    admin, _ = setup_admin(client)
    dev = _create(client, admin)

    # Publicly trusted Cove: a plain one-liner and no certificate in the script.
    out = client.post(f"/api/admin/thin-clients/{dev['id']}/enroll-token", json={},
                      headers=auth_header(admin)).json()
    assert out["install_command"].startswith('curl -fsSL "') and "pinnedpubkey" not in out["install_command"]
    script = client.get("/thin-client/install.sh", params={"code": out["code"]}).text
    assert "CA_PEM=''" in script

    # Self-signed Cove: the one-liner pins the key and the script trusts the cert.
    path, pin = _self_signed(tmp_path)
    monkeypatch.setattr(get_settings(), "thin_client_ca_file", path)
    out = client.post(f"/api/admin/thin-clients/{dev['id']}/enroll-token", json={},
                      headers=auth_header(admin)).json()
    assert f'--pinnedpubkey "{pin}"' in out["install_command"]
    script = client.get("/thin-client/install.sh", params={"code": out["code"]}).text
    assert "-----BEGIN CERTIFICATE-----" in script and "update-ca-certificates" in script
    assert subprocess.run(["bash", "-n"], input=script, text=True).returncode == 0

    # A missing or empty file degrades to "publicly trusted", never a crash.
    monkeypatch.setattr(get_settings(), "thin_client_ca_file", tmp_path / "nope.crt")
    assert tc.trust_bundle() is None
