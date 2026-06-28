"""API tests for zones: CRUD, the seeded local zone, and zone-pinned workspaces."""

from server.tests.helpers import add_image, auth_header, setup_admin


def test_local_zone_seeded(client):
    setup_admin(client)
    resp = client.get("/api/admin/zones")
    assert resp.status_code == 200, resp.text
    zones = resp.json()
    assert len(zones) == 1
    local = zones[0]
    assert local["id"] == 0
    assert local["public_id"] == "local"
    assert local["status"] == "enrolled"
    assert local["workspace_count"] == 0


def test_zones_admin_only(client):
    token, _ = setup_admin(client)
    # Non-admin user
    client.post(
        "/api/admin/users",
        json={"username": "bob", "password": "password123", "is_admin": False},
        headers=auth_header(token),
    )
    bob = client.post(
        "/api/auth/login", json={"username": "bob", "password": "password123"}
    ).json()["access_token"]
    resp = client.get("/api/admin/zones", headers=auth_header(bob))
    assert resp.status_code == 403, resp.text


def test_create_and_delete_zone(client):
    setup_admin(client)
    resp = client.post(
        "/api/admin/zones",
        json={"name": "LAN", "endpoint_host": "10.0.0.5"},
    )
    assert resp.status_code == 201, resp.text
    zone = resp.json()
    assert zone["name"] == "LAN"
    assert zone["endpoint_host"] == "10.0.0.5"
    # A manually-registered endpoint is immediately usable.
    assert zone["status"] == "enrolled"
    zid = zone["id"]

    # Delete it.
    resp = client.delete(f"/api/admin/zones/{zid}")
    assert resp.status_code == 204, resp.text
    assert client.get(f"/api/admin/zones/{zid}").status_code == 404


def test_create_zone_without_endpoint_is_pending(client):
    setup_admin(client)
    resp = client.post("/api/admin/zones", json={"name": "Pending"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "pending"


def test_cannot_delete_local_zone(client):
    setup_admin(client)
    resp = client.delete("/api/admin/zones/0")
    assert resp.status_code == 400, resp.text


def test_create_workspace_defaults_to_local_zone(client):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    resp = client.post("/api/workspaces", json={"name": "ws", "image_id": image_id})
    assert resp.status_code == 201, resp.text
    assert resp.json()["zone_id"] == 0


def test_create_workspace_on_unknown_zone_rejected(client):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    resp = client.post(
        "/api/workspaces", json={"name": "ws", "image_id": image_id, "zone_id": 999}
    )
    assert resp.status_code == 404, resp.text


def test_create_workspace_on_pending_zone_rejected(client):
    setup_admin(client)
    zid = client.post("/api/admin/zones", json={"name": "Pending"}).json()["id"]
    image_id = add_image(name="Desktop", image_type="desktop")
    resp = client.post(
        "/api/workspaces", json={"name": "ws", "image_id": image_id, "zone_id": zid}
    )
    assert resp.status_code == 409, resp.text


def test_delete_zone_with_pinned_workspace_blocked(client):
    setup_admin(client)
    zid = client.post(
        "/api/admin/zones",
        json={"name": "LAN", "endpoint_host": "10.0.0.5"},
    ).json()["id"]
    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post(
        "/api/workspaces", json={"name": "ws", "image_id": image_id, "zone_id": zid}
    )
    assert ws.status_code == 201, ws.text
    # The zone now has a pinned workspace, so deletion is refused.
    resp = client.delete(f"/api/admin/zones/{zid}")
    assert resp.status_code == 409, resp.text
    # The count is reflected in the listing.
    zone = client.get(f"/api/admin/zones/{zid}").json()
    assert zone["workspace_count"] == 1


def test_user_zones_lists_enrolled_only(client):
    setup_admin(client)
    client.post("/api/admin/zones", json={"name": "LAN", "endpoint_host": "10.0.0.5"})  # enrolled
    client.post("/api/admin/zones", json={"name": "Pending"})  # pending (no endpoint)
    resp = client.get("/api/zones")
    assert resp.status_code == 200, resp.text
    names = [z["name"] for z in resp.json()]
    assert "Local" in names and "LAN" in names
    assert "Pending" not in names
    # Minimal, non-sensitive shape.
    assert set(resp.json()[0].keys()) == {"id", "name"}


def test_user_zones_requires_auth(client):
    resp = client.get("/api/zones")
    assert resp.status_code == 401


def _give_mtls(zone_id: int) -> None:
    """Populate a zone's cert columns so ``_zone_has_mtls`` passes (enough for the
    update-agent precondition; the values aren't dialed in these tests)."""
    from server.db import SessionLocal
    from server.models import Zone

    db = SessionLocal()
    try:
        z = db.get(Zone, zone_id)
        z.ca_cert_pem = "ca"
        z.client_cert_pem = "crt"
        z.client_key_enc = "enc:v1:x"
        db.commit()
    finally:
        db.close()


def test_update_agent_rejects_local_zone(client):
    setup_admin(client)
    assert client.post("/api/admin/zones/0/update-agent").status_code == 400


def test_update_agent_rejects_unknown_zone(client):
    setup_admin(client)
    assert client.post("/api/admin/zones/999/update-agent").status_code == 404


def test_update_agent_requires_mtls(client):
    setup_admin(client)
    # Registered by endpoint (status "enrolled") but with no cert material yet.
    zid = client.post(
        "/api/admin/zones", json={"name": "LAN", "endpoint_host": "10.0.0.5"}
    ).json()["id"]
    assert client.post(f"/api/admin/zones/{zid}/update-agent").status_code == 409


def test_update_agent_unreachable_is_502(client, monkeypatch):
    setup_admin(client)
    zid = client.post(
        "/api/admin/zones", json={"name": "LAN", "endpoint_host": "10.0.0.5"}
    ).json()["id"]
    _give_mtls(zid)
    from server import agent_update

    def _boom(zone_id):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(agent_update, "updater_present", _boom)
    resp = client.post(f"/api/admin/zones/{zid}/update-agent")
    assert resp.status_code == 502, resp.text
    assert "unreachable" in resp.json()["detail"]


def test_update_agent_without_sidecar_is_409(client, monkeypatch):
    setup_admin(client)
    zid = client.post(
        "/api/admin/zones", json={"name": "LAN", "endpoint_host": "10.0.0.5"}
    ).json()["id"]
    _give_mtls(zid)
    from server import agent_update

    monkeypatch.setattr(agent_update, "updater_present", lambda zone_id: False)
    resp = client.post(f"/api/admin/zones/{zid}/update-agent")
    assert resp.status_code == 409, resp.text
    assert "predates" in resp.json()["detail"]


def test_update_agent_schedules(client, monkeypatch):
    setup_admin(client)
    zid = client.post(
        "/api/admin/zones", json={"name": "LAN", "endpoint_host": "10.0.0.5"}
    ).json()["id"]
    _give_mtls(zid)
    from server import agent_update

    calls = {}
    monkeypatch.setattr(agent_update, "updater_present", lambda zone_id: True)
    monkeypatch.setattr(
        agent_update, "run_agent_update", lambda zone_id: calls.setdefault("ran", zone_id)
    )
    resp = client.post(f"/api/admin/zones/{zid}/update-agent")
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "updating"
    assert calls.get("ran") == zid  # background task dispatched


def test_workspace_out_includes_zone_name(client):
    setup_admin(client)
    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post("/api/workspaces", json={"name": "ws", "image_id": image_id}).json()
    assert ws["zone_id"] == 0
    assert ws["zone_name"] == "Local"
