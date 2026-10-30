"""Home Assistant integration: integration tokens, the status snapshot and thin
client control. The token must work on /api/integration/v1 and nowhere else."""

import pytest
from fastapi.testclient import TestClient

from server import integration, stream_leases
from server.db import SessionLocal
from server.models import AuditLog, ThinClient, Workspace
from server.tests.helpers import add_image, auth_header, create_user_via_admin, login, set_workspace_status, setup_admin
from server.tests.test_thin_clients import _bind, _checkin, _create, _enroll


@pytest.fixture(autouse=True)
def _fresh():
    stream_leases.clear()
    integration.reset_stats_cache()
    yield
    stream_leases.clear()


def _mint(client, admin_token, scope="control", name="Home Assistant"):
    r = client.post("/api/admin/integration-tokens", json={"name": name, "scope": scope},
                    headers=auth_header(admin_token))
    assert r.status_code == 201, r.text
    return r.json()


def _status(client, token):
    r = client.get("/api/integration/v1/status", headers=auth_header(token))
    assert r.status_code == 200, r.text
    return r.json()


def _workspace(client, token, name="desk"):
    r = client.post("/api/workspaces", json={"name": name, "image_id": add_image(name=f"img-{name}")},
                    headers=auth_header(token))
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


# ── tokens ──────────────────────────────────────────────────────────────────


def test_token_lifecycle(client):
    admin, _ = setup_admin(client)
    created = _mint(client, admin, scope="read")
    token = created["token"]
    assert token.startswith("cit_") and created["prefix"] == token[:8] and created["scope"] == "read"

    listed = client.get("/api/admin/integration-tokens", headers=auth_header(admin)).json()
    assert [t["name"] for t in listed] == ["Home Assistant"]
    assert "token" not in listed[0] and listed[0]["last_used_at"] is None

    _status(client, token)
    listed = client.get("/api/admin/integration-tokens", headers=auth_header(admin)).json()
    assert listed[0]["last_used_at"] is not None

    # Only its hash is stored.
    with SessionLocal() as db:
        from server.models import IntegrationToken

        row = db.get(IntegrationToken, created["id"])
        assert token not in (row.token_hash, row.prefix)

    r = client.delete(f"/api/admin/integration-tokens/{created['id']}", headers=auth_header(admin))
    assert r.status_code == 204
    r = client.get("/api/integration/v1/status", headers=auth_header(token))
    assert r.status_code == 401

    with SessionLocal() as db:
        actions = [a.action for a in db.query(AuditLog).all()]
    assert "admin.integration_token.create" in actions and "admin.integration_token.revoke" in actions


def test_only_admins_manage_tokens(client):
    admin, _ = setup_admin(client)
    create_user_via_admin(client, admin, "bob")
    bob = login(client, "bob", "password123").json()["access_token"]
    assert client.get("/api/admin/integration-tokens", headers=auth_header(bob)).status_code == 403
    r = client.post("/api/admin/integration-tokens", json={"name": "x"}, headers=auth_header(bob))
    assert r.status_code == 403
    r = client.post("/api/admin/integration-tokens", json={"name": "x", "scope": "admin"}, headers=auth_header(admin))
    assert r.status_code == 422


def test_token_is_not_a_session_and_a_session_is_not_a_token(client):
    admin, _ = setup_admin(client)
    token = _mint(client, admin)["token"]
    for path in ("/api/auth/me", "/api/admin/users", "/api/workspaces", "/api/admin/thin-clients"):
        assert client.get(path, headers=auth_header(token)).status_code == 401, path
    # An admin's own session can't stand in for a token either.
    assert client.get("/api/integration/v1/status", headers=auth_header(admin)).status_code == 401
    assert client.get("/api/integration/v1/status").status_code == 401


# ── snapshot ────────────────────────────────────────────────────────────────


def test_status_counts_users_workspaces_and_zones(client):
    admin, _ = setup_admin(client)
    create_user_via_admin(client, admin, "bob")
    bob = login(client, "bob", "password123").json()["access_token"]
    running = _workspace(client, bob, "one")
    _workspace(client, bob, "two")
    broken = _workspace(client, admin, "three")
    set_workspace_status(running, "running")
    set_workspace_status(broken, "error")
    token = _mint(client, admin, scope="read")["token"]

    s = _status(client, token)
    assert s["users"] == {"total": 2, "admins": 1, "active": 1, "on_thin_clients": 0}
    assert s["workspaces"]["total"] == 3
    assert s["workspaces"]["running"] == 1 and s["workspaces"]["error"] == 1 and s["workspaces"]["starting"] == 1
    assert s["token"] == {"name": "Home Assistant", "scope": "read"}
    # The instance id is stable across reads.
    assert len(s["instance_id"]) == 32 and _status(client, token)["instance_id"] == s["instance_id"]

    [local] = s["zones"]
    assert local["local"] is True and local["online"] is True
    assert local["workspaces_total"] == 3 and local["workspaces_running"] == 1 and local["users_active"] == 1
    # The running one has no container in these tests, so there is no sample.
    assert local["cpu_percent"] is None


def test_zone_stats_sum_running_workspaces(client, monkeypatch, fake_docker_manager):
    admin, _ = setup_admin(client)
    ids = [_workspace(client, admin, n) for n in ("a", "b")]
    with SessionLocal() as db:
        for i, ws_id in enumerate(ids):
            ws = db.get(Workspace, ws_id)
            ws.status, ws.container_id = "running", f"c{i}"
        db.commit()
    samples = {"c0": {"cpu_pct": 12.5, "mem_used": 1000}, "c1": {"cpu_pct": 30.0, "mem_used": 500}}
    calls = []
    fake_docker_manager.get_stats.side_effect = lambda cid: calls.append(cid) or samples[cid]
    token = _mint(client, admin, scope="read")["token"]

    zone = _status(client, token)["zones"][0]
    assert zone["cpu_percent"] == 42.5 and zone["memory_used_bytes"] == 1500
    # A second poll inside the cache window reuses the sample.
    _status(client, token)
    assert sorted(calls) == ["c0", "c1"]


def test_status_reports_thin_clients(client):
    admin, _ = setup_admin(client)
    _create(client, admin, "Never enrolled")
    dev, dtoken = _enroll(client, admin, "Desk")
    _checkin(client, dtoken, state={
        "display_on": True, "volume": 80, "muted": False, "idle_s": 12, "uptime_s": 3600,
        "upgradable": 3, "reboot_required": False, "agent_version": "1.1.0", "hostname": "desk",
        "ips": ["10.0.0.5", 7], "media_playing": "yes",  # junk from the device is dropped
    })
    token = _mint(client, admin, scope="read")["token"]

    s = _status(client, token)
    assert [c["name"] for c in s["thin_clients"]] == ["Desk"]
    c = s["thin_clients"][0]
    assert c["id"] == dev["public_id"] and c["online"] is True
    assert c["display_on"] is True and c["volume"] == 80 and c["muted"] is False and c["idle_seconds"] == 12
    assert c["updates_available"] == 3 and c["ips"] == ["10.0.0.5"] and c["media_playing"] is None
    assert c["booted_at"] and c["session_username"] is None and c["workspace_name"] is None
    assert s["thin_clients_summary"] == {"total": 1, "online": 1, "in_use": 0}

    # Offline: live readings are withheld rather than shown stale.
    with SessionLocal() as db:
        row = db.get(ThinClient, dev["id"])
        row.last_seen_at = row.last_seen_at.replace(year=2020)
        db.commit()
    c = _status(client, token)["thin_clients"][0]
    assert c["online"] is False and c["display_on"] is None and c["volume"] is None and c["updates_available"] == 3


def test_status_names_the_workspace_on_screen(client):
    admin, _ = setup_admin(client)
    ws = _workspace(client, admin, "lab")
    set_workspace_status(ws, "running")  # only a running workspace streams
    _, dtoken = _enroll(client, admin, "Desk")
    _checkin(client, dtoken)
    kiosk = TestClient(client.app)
    _bind(kiosk, dtoken)
    kiosk.post(f"/api/workspaces/{ws}/stream-lease", json={"viewer_id": "viewer-kiosk-0001"},
               headers=auth_header(admin))
    token = _mint(client, admin, scope="read")["token"]

    c = _status(client, token)["thin_clients"][0]
    assert c["workspace_name"] == "lab" and c["workspace_owner"] == "admin" and c["workspace_since"]

    # Another browser takes the stream: the kiosk no longer shows it.
    client.post(f"/api/workspaces/{ws}/stream-lease", json={"viewer_id": "viewer-other-0002"},
                headers=auth_header(admin))
    assert _status(client, token)["thin_clients"][0]["workspace_name"] is None


# ── control ─────────────────────────────────────────────────────────────────


def test_read_token_cannot_control(client):
    admin, _ = setup_admin(client)
    dev, _ = _enroll(client, admin)
    token = _mint(client, admin, scope="read")["token"]
    base = f"/api/integration/v1/thin-clients/{dev['public_id']}"
    assert client.post(f"{base}/commands", json={"kind": "screen_on"}, headers=auth_header(token)).status_code == 403
    assert client.post(f"{base}/signout", headers=auth_header(token)).status_code == 403
    r = client.post(f"{base}/keep-awake", json={"minutes": 5}, headers=auth_header(token))
    assert r.status_code == 403


def test_control_token_queues_commands_for_the_device(client):
    admin, _ = setup_admin(client)
    dev, dtoken = _enroll(client, admin)
    token = _mint(client, admin, name="HA")["token"]
    base = f"/api/integration/v1/thin-clients/{dev['public_id']}"

    r = client.post(f"{base}/commands", json={"kind": "set_volume", "args": {"level": 900}}, headers=auth_header(token))
    assert r.status_code == 202 and r.json()["args"] == {"level": 150}
    r = client.post(f"{base}/commands", json={"kind": "reboot"}, headers=auth_header(token))
    assert r.status_code == 202

    got = _checkin(client, dtoken).json()["commands"]
    assert [(c["kind"], c["args"]) for c in got] == [("set_volume", {"level": 150}), ("reboot", {})]
    cmds = client.get(f"/api/admin/thin-clients/{dev['id']}/commands", headers=auth_header(admin)).json()
    assert {c["created_by"] for c in cmds} == {"integration:HA"}

    # Only disruptive commands reach the audit log.
    with SessionLocal() as db:
        details = [a.detail for a in db.query(AuditLog).filter(AuditLog.action == "integration.thin_client.command")]
    assert details == ["Workbench: reboot (HA)"]


@pytest.mark.parametrize("body,code", [
    ({"kind": "logs"}, 400),           # admins only
    ({"kind": "apt_upgrade"}, 400),    # the device's own maintenance window
    ({"kind": "nope"}, 400),
    ({"kind": "set_mute", "args": {"muted": "yes"}}, 400),
    ({"kind": "set_volume", "args": {}}, 400),
])
def test_bad_commands_are_refused(client, body, code):
    admin, _ = setup_admin(client)
    dev, _ = _enroll(client, admin)
    token = _mint(client, admin)["token"]
    r = client.post(f"/api/integration/v1/thin-clients/{dev['public_id']}/commands", json=body,
                    headers=auth_header(token))
    assert r.status_code == code, r.text


def test_unknown_or_pending_devices_are_404(client):
    admin, _ = setup_admin(client)
    pending = _create(client, admin, "Pending")
    token = _mint(client, admin)["token"]
    for pid in (pending["public_id"], "nope"):
        r = client.post(f"/api/integration/v1/thin-clients/{pid}/commands", json={"kind": "screen_on"},
                        headers=auth_header(token))
        assert r.status_code == 404


def test_signout_and_keep_awake(client):
    admin, _ = setup_admin(client)
    dev, dtoken = _enroll(client, admin)
    token = _mint(client, admin)["token"]
    base = f"/api/integration/v1/thin-clients/{dev['public_id']}"
    with SessionLocal() as db:
        row = db.get(ThinClient, dev["id"])
        row.session_username, row.session_user_id = "admin", 1
        db.commit()

    assert client.post(f"{base}/keep-awake", json={"minutes": 60}, headers=auth_header(token)).status_code == 204
    s = _status(client, token)["thin_clients"][0]
    assert s["keep_awake_until"] and s["session_username"] == "admin"
    assert _checkin(client, dtoken).json()["keep_awake_until"]

    assert client.post(f"{base}/keep-awake", json={"minutes": 0}, headers=auth_header(token)).status_code == 204
    assert client.post(f"{base}/signout", headers=auth_header(token)).status_code == 204
    s = _status(client, token)["thin_clients"][0]
    assert s["keep_awake_until"] is None and s["session_username"] is None
    # Signing out resets the device's browser, as it does for an admin.
    assert [c["kind"] for c in _checkin(client, dtoken).json()["commands"]] == ["restart_browser"]


def test_screenshot(client):
    admin, _ = setup_admin(client)
    dev, dtoken = _enroll(client, admin)
    reader = _mint(client, admin, scope="read", name="Grafana")["token"]
    token = _mint(client, admin, scope="control")["token"]
    url = f"/api/integration/v1/thin-clients/{dev['public_id']}/screenshot.png"
    assert client.get(url, headers=auth_header(token)).status_code == 404
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    assert client.post("/api/thin-client/screenshot", content=png,
                       headers={"Authorization": f"Bearer {dtoken}"}).status_code == 204
    r = client.get(url, headers=auth_header(token))
    assert r.status_code == 200 and r.content == png
    assert _status(client, token)["thin_clients"][0]["screenshot_at"]
    # A screenshot shows what someone has on screen: control tokens only.
    assert client.get(url, headers=auth_header(reader)).status_code == 403


def test_home_assistant_download(client):
    import io
    import zipfile

    admin, _ = setup_admin(client)
    create_user_via_admin(client, admin, "bob")
    bob = login(client, "bob", "password123").json()["access_token"]
    url = "/api/admin/integration-tokens/home-assistant.zip"
    assert client.get(url, headers=auth_header(bob)).status_code == 403

    r = client.get(url, headers=auth_header(admin))
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"].startswith('attachment; filename="cove-home-assistant-')
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "custom_components/cove/manifest.json" in names and "custom_components/cove/__init__.py" in names
    assert "custom_components/cove/brand/icon.png" in names
    assert not any("__pycache__" in n for n in names)
    # Same files, same bytes.
    assert client.get(url, headers=auth_header(admin)).content == r.content
