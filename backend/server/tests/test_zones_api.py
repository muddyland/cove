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
        json={"name": "LAN", "endpoint_host": "10.0.0.5", "endpoint_port": 2376},
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
