"""Follow-ups from the post-1.3.1 security review that don't fit one feature's
test file: integration token scope and lifetime, the device upload and bind
endpoints, stale stream leases, the stats cache, the install script and the
policy audit trail."""

import pytest
from fastapi.testclient import TestClient

from server import integration, stream_leases
from server.db import SessionLocal
from server.models import AuditLog, IntegrationToken, Workspace
from server.tests.helpers import auth_header, create_user_via_admin, login, setup_admin
from server.tests.test_integration_api import _mint, _status, _workspace
from server.tests.test_thin_clients import _bind, _dev, _enroll
from server.thin_client_install import render_install_script


@pytest.fixture(autouse=True)
def _fresh():
    stream_leases.clear()
    integration.reset_stats_cache()
    yield
    stream_leases.clear()


def _actions():
    with SessionLocal() as db:
        return [(a.action, a.detail) for a in db.query(AuditLog).order_by(AuditLog.id)]


def test_screenshot_command_is_audited(client):
    admin, _ = setup_admin(client)
    dev, _ = _enroll(client, admin)
    token = _mint(client, admin)["token"]
    r = client.post(f"/api/integration/v1/thin-clients/{dev['public_id']}/commands",
                    json={"kind": "screenshot"}, headers=auth_header(token))
    assert r.status_code == 202
    assert ("integration.thin_client.command", "Workbench: screenshot (Home Assistant)") in _actions()


def test_tokens_go_with_their_creators_admin_rights(client):
    admin, _ = setup_admin(client)
    carol = create_user_via_admin(client, admin, "carol")
    client.patch(f"/api/admin/users/{carol['id']}", json={"is_admin": True}, headers=auth_header(admin))
    c_token = login(client, "carol", "password123").json()["access_token"]
    t1 = _mint(client, c_token, name="Carol HA")["token"]
    t2 = _mint(client, c_token, name="Carol Grafana", scope="read")["token"]
    mine = _mint(client, admin, name="Admin HA")["token"]

    # A rename keeps them hers...
    client.patch(f"/api/admin/users/{carol['id']}", json={"username": "caroline"}, headers=auth_header(admin))
    with SessionLocal() as db:
        assert {r.created_by for r in db.query(IntegrationToken)} == {"caroline", "admin"}
    _status(client, t1)

    # ...demoting her revokes them, and leaves everyone else's alone.
    client.patch(f"/api/admin/users/{carol['id']}", json={"is_admin": False}, headers=auth_header(admin))
    for t in (t1, t2):
        assert client.get("/api/integration/v1/status", headers=auth_header(t)).status_code == 401
    _status(client, mine)
    revoked = [d for a, d in _actions() if a == "admin.integration_token.revoke"]
    assert revoked == ["Carol HA (caroline no longer an admin)", "Carol Grafana (caroline no longer an admin)"]


def test_deleting_the_creator_revokes_their_tokens(client):
    admin, _ = setup_admin(client)
    dave = create_user_via_admin(client, admin, "dave")
    client.patch(f"/api/admin/users/{dave['id']}", json={"is_admin": True}, headers=auth_header(admin))
    d_token = login(client, "dave", "password123").json()["access_token"]
    t = _mint(client, d_token, name="Dave HA")["token"]
    assert client.delete(f"/api/admin/users/{dave['id']}", headers=auth_header(admin)).status_code == 204
    assert client.get("/api/integration/v1/status", headers=auth_header(t)).status_code == 401
    assert ("admin.integration_token.revoke", "Dave HA (dave deleted)") in _actions()


def test_screenshot_upload_checks_the_device_before_the_body(client):
    admin, _ = setup_admin(client)
    _, dtoken = _enroll(client, admin)
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (9 * 1024 * 1024)
    # Anonymous: refused as unauthenticated, not as "too large" after buffering.
    assert client.post("/api/thin-client/screenshot", content=big).status_code == 401
    assert client.post("/api/thin-client/screenshot", content=big,
                       headers={"Authorization": "Bearer ctc_nope"}).status_code == 401
    assert client.post("/api/thin-client/screenshot", content=big, headers=_dev(dtoken)).status_code == 413
    ok = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    assert client.post("/api/thin-client/screenshot", content=ok, headers=_dev(dtoken)).status_code == 204


def test_junk_binds_cannot_lock_out_a_real_kiosk(client):
    admin, _ = setup_admin(client)
    _, dtoken = _enroll(client, admin)
    for _ in range(30):
        client.get("/thin-client/bind?ticket=junk", follow_redirects=False)
    # Same source IP, real ticket: still binds.
    kiosk = TestClient(client.app)
    _bind(kiosk, dtoken)
    # Junk itself is still limited.
    assert client.get("/thin-client/bind?ticket=junk", follow_redirects=False).status_code == 429


def test_status_ignores_leases_on_stopped_workspaces(client):
    admin, _ = setup_admin(client)
    dev, dtoken = _enroll(client, admin)
    client.post("/api/thin-client/checkin", json={"state": {}}, headers=_dev(dtoken))
    ws_id = _workspace(client, admin)
    with SessionLocal() as db:
        db.get(Workspace, ws_id).status = "running"
        db.commit()
    stream_leases.claim(ws_id, "viewer-1234", "Workbench (thin client)", dev["public_id"])
    token = _mint(client, admin, scope="read")["token"]
    assert _status(client, token)["thin_clients"][0]["workspace_name"] == "desk"
    with SessionLocal() as db:
        db.get(Workspace, ws_id).status = "stopped"
        db.commit()
    assert _status(client, token)["thin_clients"][0]["workspace_name"] is None


def test_stopping_or_removing_forgets_the_lease(monkeypatch):
    from server.docker_manager import DockerManager

    dm = DockerManager.__new__(DockerManager)
    monkeypatch.setattr(dm, "_get_db", lambda: (_ for _ in ()).throw(RuntimeError("stop here")), raising=False)
    for method in ("stop_workspace", "remove_workspace"):
        stream_leases.claim(7, "viewer-1234", "Firefox on Linux")
        with pytest.raises(RuntimeError):
            getattr(dm, method)(7)
        assert stream_leases.by_device() == {} and stream_leases.check(7, "other-viewer", "x")[0] is True


def test_stats_sample_is_reused_when_the_running_set_changes(client, fake_docker_manager):
    admin, _ = setup_admin(client)
    ids = [_workspace(client, admin, n) for n in ("a", "b")]
    with SessionLocal() as db:
        db.get(Workspace, ids[0]).status, db.get(Workspace, ids[0]).container_id = "running", "c0"
        db.commit()
    calls = []
    fake_docker_manager.get_stats.side_effect = lambda cid: calls.append(cid) or {"cpu_pct": 1.0, "mem_used": 1}
    token = _mint(client, admin, scope="read")["token"]
    _status(client, token)
    with SessionLocal() as db:
        db.get(Workspace, ids[1]).status, db.get(Workspace, ids[1]).container_id = "running", "c1"
        db.commit()
    _status(client, token)
    assert calls == ["c0"]  # no fresh sample of every container inside the window


def test_install_script_name_cannot_splice_in_other_values():
    out = render_install_script(cp_url="https://cove.example", code="ABCD", name="__CA_PEM__ __CODE__",
                                ca_pem="-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----")
    first = out.splitlines()[1]
    assert first == '# Cove thin client installer for "__CA_PEM__ __CODE__".'
    assert out.count("BEGIN CERTIFICATE") == 1


def test_policy_change_audit_names_the_values(client):
    admin, _ = setup_admin(client)
    dev, _ = _enroll(client, admin)
    client.patch(f"/api/admin/thin-clients/{dev['id']}",
                 json={"policy": {"extra_allowed_hosts": ["docs.example.com"], "signout_minutes": 10}},
                 headers=auth_header(admin))
    detail = [d for a, d in _actions() if a == "admin.thin_client.update"][-1]
    assert "extra_allowed_hosts=['docs.example.com']" in detail and "signout_minutes=10" in detail
