"""Per-user zone limits: an admin can restrict a user to some zones."""

from server.db import SessionLocal
from server.models import User, UserZone, Workspace, Zone
from server.tests.helpers import (
    add_image,
    auth_header,
    create_user_via_admin,
    login,
    set_workspace_status,
    setup_admin,
)


def _zone(name="LAN"):
    with SessionLocal() as db:
        z = Zone(name=name, status="enrolled", endpoint_host=f"{name.lower()}.example")
        db.add(z)
        db.commit()
        return z.id


def _bob(client, admin):
    bob = create_user_via_admin(client, admin, "bob")
    return bob, login(client, "bob", "password123").json()["access_token"]


def _limit(client, admin, user_id, zone_ids, restricted=True):
    r = client.patch(f"/api/admin/users/{user_id}",
                     json={"zones_restricted": restricted, "allowed_zone_ids": zone_ids},
                     headers=auth_header(admin))
    assert r.status_code == 200, r.text
    return r.json()


def _launch(client, token, zone_id, name="ws"):
    return client.post("/api/workspaces", json={"name": name, "image_id": add_image(name=f"img-{name}"),
                                                "zone_id": zone_id}, headers=auth_header(token))


def test_unrestricted_by_default(client):
    admin, _ = setup_admin(client)
    lan = _zone()
    bob, token = _bob(client, admin)
    assert bob["zones_restricted"] is False
    ids = [z["id"] for z in client.get("/api/zones", headers=auth_header(token)).json()]
    assert ids == [0, lan]
    assert _launch(client, token, lan).status_code == 201


def test_restricted_user_sees_and_launches_only_allowed_zones(client):
    admin, _ = setup_admin(client)
    lan = _zone()
    bob, token = _bob(client, admin)
    out = _limit(client, admin, bob["id"], [lan])
    assert out["zones_restricted"] is True and out["allowed_zone_ids"] == [lan]

    assert [z["id"] for z in client.get("/api/zones", headers=auth_header(token)).json()] == [lan]
    refused = _launch(client, token, 0, "local")
    assert refused.status_code == 403 and "Local" in refused.json()["detail"]
    assert _launch(client, token, lan, "lan").status_code == 201

    # Restricted to nothing: no zone at all.
    _limit(client, admin, bob["id"], [])
    assert client.get("/api/zones", headers=auth_header(token)).json() == []
    assert _launch(client, token, lan, "lan2").status_code == 403

    # Lifting the limit restores everything.
    _limit(client, admin, bob["id"], None, restricted=False)
    assert len(client.get("/api/zones", headers=auth_header(token)).json()) == 2


def test_limit_applies_to_start_clone_and_migrate(client):
    admin, _ = setup_admin(client)
    lan = _zone("LAN")
    dmz = _zone("DMZ")
    bob, token = _bob(client, admin)
    ws = _launch(client, token, lan, "kvm").json()
    set_workspace_status(ws["id"], "stopped")

    _limit(client, admin, bob["id"], [dmz])
    start = client.post(f"/api/workspaces/{ws['id']}/start", headers=auth_header(token))
    assert start.status_code == 403 and "Migrate it" in start.json()["detail"]
    clone = client.post(f"/api/workspaces/{ws['id']}/clone", json={"name": "kvm2"}, headers=auth_header(token))
    assert clone.status_code == 403
    # Migrating it somewhere allowed is the way out...
    with SessionLocal() as db:
        db.get(Workspace, ws["id"]).ephemeral = False
        db.commit()
    bad = client.post(f"/api/workspaces/{ws['id']}/migrate", json={"target_zone_id": 0}, headers=auth_header(token))
    assert bad.status_code == 403
    ok = client.post(f"/api/workspaces/{ws['id']}/migrate", json={"target_zone_id": dmz}, headers=auth_header(token))
    assert ok.status_code != 403, ok.text

    # An admin acting on bob's workspace is held to bob's limit too.
    set_workspace_status(ws["id"], "stopped")
    bad = client.post(f"/api/workspaces/{ws['id']}/migrate", json={"target_zone_id": 0}, headers=auth_header(admin))
    assert bad.status_code == 403 and "bob" in bad.json()["detail"]


def test_admins_are_never_restricted(client):
    admin, _ = setup_admin(client)
    lan = _zone()
    with SessionLocal() as db:
        me = db.query(User).filter_by(username="admin").one()
        me.zones_restricted = True  # even if set directly
        db.commit()
    assert [z["id"] for z in client.get("/api/zones", headers=auth_header(admin)).json()] == [0, lan]
    assert _launch(client, admin, 0).status_code == 201


def test_unknown_zone_ids_are_refused_and_deleted_zones_drop_out(client):
    admin, _ = setup_admin(client)
    lan = _zone()
    bob, _ = _bob(client, admin)
    r = client.patch(f"/api/admin/users/{bob['id']}", json={"zones_restricted": True, "allowed_zone_ids": [999]},
                     headers=auth_header(admin))
    assert r.status_code == 400
    _limit(client, admin, bob["id"], [0, lan])
    assert client.delete(f"/api/admin/zones/{lan}", headers=auth_header(admin)).status_code == 204
    with SessionLocal() as db:
        assert [z.zone_id for z in db.query(UserZone).filter_by(user_id=bob["id"])] == [0]


def test_create_user_with_a_limit(client):
    admin, _ = setup_admin(client)
    lan = _zone()
    r = client.post("/api/admin/users", json={"username": "carol", "password": "password123",
                                              "zones_restricted": True, "allowed_zone_ids": [lan]},
                    headers=auth_header(admin))
    assert r.status_code == 201, r.text
    assert r.json()["allowed_zone_ids"] == [lan]
    me = client.get("/api/auth/me", headers=auth_header(login(client, "carol", "password123").json()["access_token"]))
    assert me.json()["zones_restricted"] is True
