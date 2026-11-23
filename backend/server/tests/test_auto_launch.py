"""The sign-in workspace: the one Cove opens right after an account signs in.

Set by the user (Preferences) or by an admin (Admin -> Users), who can lock it
and can create a workspace for an account that has none."""

import uuid

from sqlalchemy import select

from server.db import SessionLocal
from server.models import User, Workspace
from server.tests.helpers import (
    add_image,
    auth_header,
    create_user_via_admin,
    login,
    set_workspace_status,
    setup_admin,
)


def _user(client, admin, name="bob", **fields):
    u = create_user_via_admin(client, admin, name)
    if fields:
        r = client.patch(f"/api/admin/users/{u['id']}", json=fields, headers=auth_header(admin))
        assert r.status_code == 200, r.text
    return u, login(client, name, "password123").json()["access_token"]


def _launch(client, token, name, **extra):
    image_id = add_image(name=f"img-{uuid.uuid4().hex[:8]}")
    r = client.post("/api/workspaces", json={"name": name, "image_id": image_id, **extra}, headers=auth_header(token))
    assert r.status_code == 201, r.text
    return r.json()


def _me(client, token):
    return client.get("/api/auth/me", headers=auth_header(token)).json()


def _choose(client, token, workspace_id):
    return client.put("/api/users/me/auto-launch", json={"workspace_id": workspace_id}, headers=auth_header(token))


# ── The user's own choice ──────────────────────────────────────────────────────

def test_user_picks_and_clears_their_sign_in_workspace(client):
    admin, _ = setup_admin(client)
    _, bob = _user(client, admin)
    ws = _launch(client, bob, "desk")
    assert _me(client, bob)["auto_launch_workspace_id"] is None

    r = _choose(client, bob, ws["public_id"])
    assert r.status_code == 200, r.text
    assert r.json()["auto_launch_workspace_id"] == ws["public_id"]
    assert r.json()["auto_launch_locked"] is False
    assert _me(client, bob)["auto_launch_workspace_id"] == ws["public_id"]

    assert _choose(client, bob, None).json()["auto_launch_workspace_id"] is None


def test_user_cannot_pick_someone_elses_or_a_missing_workspace(client):
    admin, _ = setup_admin(client)
    _, bob = _user(client, admin, "bob")
    _, eve = _user(client, admin, "eve")
    bobs = _launch(client, bob, "desk")
    assert _choose(client, eve, bobs["public_id"]).status_code == 404
    assert _choose(client, eve, "0" * 32).status_code == 404
    assert _me(client, eve)["auto_launch_workspace_id"] is None


def test_auto_remove_workspace_cannot_be_the_sign_in_workspace(client):
    admin, _ = setup_admin(client)
    _, bob = _user(client, admin)
    ws = _launch(client, bob, "once", ephemeral=True, auto_remove=True)
    r = _choose(client, bob, ws["public_id"])
    assert r.status_code == 400 and "deletes itself" in r.json()["detail"]


def test_deleting_the_workspace_forgets_it(client):
    admin, _ = setup_admin(client)
    u, bob = _user(client, admin)
    ws = _launch(client, bob, "desk")
    r = client.put(f"/api/admin/users/{u['id']}/auto-launch",
                   json={"workspace_id": ws["public_id"], "locked": True}, headers=auth_header(admin))
    assert r.status_code == 200, r.text
    set_workspace_status(ws["id"], "stopped")
    # An admin may delete it even while it's locked; the choice and lock go with it.
    assert client.delete(f"/api/workspaces/{ws['id']}", headers=auth_header(admin)).status_code == 204
    me = _me(client, bob)
    assert me["auto_launch_workspace_id"] is None and me["auto_launch_locked"] is False


def test_any_orm_delete_forgets_it(client):
    """The manager's teardown and the auto-remove stop delete the row directly."""
    admin, _ = setup_admin(client)
    _, bob = _user(client, admin)
    ws = _launch(client, bob, "desk")
    assert _choose(client, bob, ws["public_id"]).status_code == 200
    with SessionLocal() as db:
        db.delete(db.get(Workspace, ws["id"]))
        db.commit()
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.username == "bob")).auto_launch_workspace_id is None


# ── Admin: set and lock ────────────────────────────────────────────────────────

def test_admin_lock_stops_the_user_changing_it_or_deleting_the_workspace(client):
    admin, _ = setup_admin(client)
    u, bob = _user(client, admin)
    ws = _launch(client, bob, "desk")
    other = _launch(client, bob, "other")
    r = client.put(f"/api/admin/users/{u['id']}/auto-launch",
                   json={"workspace_id": ws["public_id"], "locked": True}, headers=auth_header(admin))
    assert r.status_code == 200, r.text
    assert r.json()["auto_launch_workspace_id"] == ws["public_id"] and r.json()["auto_launch_locked"] is True

    r = _choose(client, bob, other["public_id"])
    assert r.status_code == 403  # never 401: the SPA would sign the user out
    assert _choose(client, bob, None).status_code == 403
    set_workspace_status(ws["id"], "stopped")
    r = client.delete(f"/api/workspaces/{ws['id']}", headers=auth_header(bob))
    assert r.status_code == 409 and "administrator" in r.json()["detail"]
    # Other workspaces are unaffected.
    set_workspace_status(other["id"], "stopped")
    assert client.delete(f"/api/workspaces/{other['id']}", headers=auth_header(bob)).status_code == 204

    # Unlocked again, the user is free.
    r = client.put(f"/api/admin/users/{u['id']}/auto-launch",
                   json={"workspace_id": ws["public_id"], "locked": False}, headers=auth_header(admin))
    assert r.json()["auto_launch_locked"] is False
    assert _choose(client, bob, None).status_code == 200


def test_locking_the_dashboard_is_not_a_lock(client):
    admin, _ = setup_admin(client)
    u, bob = _user(client, admin)
    r = client.put(f"/api/admin/users/{u['id']}/auto-launch",
                   json={"workspace_id": None, "locked": True}, headers=auth_header(admin))
    assert r.status_code == 200 and r.json()["auto_launch_locked"] is False


def test_admin_cannot_pick_a_workspace_of_another_account(client):
    admin, _ = setup_admin(client)
    u, _bob = _user(client, admin, "bob")
    _, eve = _user(client, admin, "eve")
    eves = _launch(client, eve, "desk")
    r = client.put(f"/api/admin/users/{u['id']}/auto-launch",
                   json={"workspace_id": eves["public_id"]}, headers=auth_header(admin))
    assert r.status_code == 404


def test_admin_endpoints_are_admin_only(client):
    admin, _ = setup_admin(client)
    u, bob = _user(client, admin)
    ws = _launch(client, bob, "desk")
    h = auth_header(bob)
    assert client.get(f"/api/admin/users/{u['id']}/workspaces", headers=h).status_code == 403
    assert client.put(f"/api/admin/users/{u['id']}/auto-launch",
                      json={"workspace_id": ws["public_id"], "locked": False}, headers=h).status_code == 403
    image_id = add_image(name="img-x")
    assert client.post(f"/api/admin/users/{u['id']}/workspaces",
                       json={"name": "x", "image_id": image_id}, headers=h).status_code == 403


def test_admin_lists_an_accounts_workspaces(client):
    admin, _ = setup_admin(client)
    u, bob = _user(client, admin)
    a = _launch(client, bob, "a")
    _launch(client, admin, "admins-own")
    r = client.get(f"/api/admin/users/{u['id']}/workspaces", headers=auth_header(admin))
    assert r.status_code == 200
    assert [w["public_id"] for w in r.json()] == [a["public_id"]]
    assert client.get("/api/admin/users/999/workspaces", headers=auth_header(admin)).status_code == 404


# ── Admin: create one for the account ──────────────────────────────────────────

def test_admin_creates_a_locked_sign_in_workspace_for_an_account(client):
    admin, _ = setup_admin(client)
    u, bob = _user(client, admin, max_workspaces=1)
    _launch(client, bob, "existing")  # at the cap: an admin's create isn't held to it
    image_id = add_image(name="img-desk")
    r = client.post(f"/api/admin/users/{u['id']}/workspaces",
                    json={"name": "Desk", "image_id": image_id, "locked": True}, headers=auth_header(admin))
    assert r.status_code == 201, r.text
    ws = r.json()
    assert ws["user_id"] == u["id"] and ws["name"] == "Desk"
    me = _me(client, bob)
    assert me["auto_launch_workspace_id"] == ws["public_id"] and me["auto_launch_locked"] is True
    # It shows on the owner's dashboard, not the admin's.
    assert ws["id"] in [w["id"] for w in client.get("/api/workspaces", headers=auth_header(bob)).json()]
    assert ws["id"] not in [w["id"] for w in client.get("/api/workspaces", headers=auth_header(admin)).json()]


def test_admin_create_without_auto_launch_leaves_the_choice_alone(client):
    admin, _ = setup_admin(client)
    u, bob = _user(client, admin)
    image_id = add_image(name="img-desk")
    r = client.post(f"/api/admin/users/{u['id']}/workspaces",
                    json={"name": "Desk", "image_id": image_id, "auto_launch": False}, headers=auth_header(admin))
    assert r.status_code == 201, r.text
    assert _me(client, bob)["auto_launch_workspace_id"] is None


def test_admin_create_is_checked_as_the_owner(client):
    """The workspace must run on a zone the account may use, and gets a name
    unique among the account's own workspaces."""
    admin, _ = setup_admin(client)
    u, bob = _user(client, admin, zones_restricted=True, allowed_zone_ids=[])
    image_id = add_image(name="img-desk")
    r = client.post(f"/api/admin/users/{u['id']}/workspaces",
                    json={"name": "Desk", "image_id": image_id}, headers=auth_header(admin))
    assert r.status_code in (400, 403), r.text
    with SessionLocal() as db:
        assert db.scalar(select(Workspace).where(Workspace.user_id == u["id"])) is None
        assert db.get(User, u["id"]).auto_launch_workspace_id is None

    u2, carol = _user(client, admin, "carol")
    _launch(client, carol, "Desk")
    r = client.post(f"/api/admin/users/{u2['id']}/workspaces",
                    json={"name": "Desk", "image_id": image_id}, headers=auth_header(admin))
    assert r.status_code in (400, 409), r.text


# ── OIDC lands as a fresh sign-in ──────────────────────────────────────────────

def test_oidc_callback_marks_a_fresh_sign_in(client, monkeypatch):
    from server.tests.test_prerelease_review import _callback, _oidc

    setup_admin(client)
    _oidc(monkeypatch, [])
    r = _callback(client)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == "/app?signed_in=1"
