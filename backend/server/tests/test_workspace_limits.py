"""Per-user workspace caps: total workspaces and running at once, each with a
deployment default and a per-account override."""

import threading
import uuid

from server.tests.helpers import (
    add_image,
    auth_header,
    create_user_via_admin,
    login,
    set_workspace_status,
    setup_admin,
)


def _user(client, admin, name="bob", **limits):
    u = create_user_via_admin(client, admin, name)
    if limits:
        r = client.patch(f"/api/admin/users/{u['id']}", json=limits, headers=auth_header(admin))
        assert r.status_code == 200, r.text
    return u, login(client, name, "password123").json()["access_token"]


def _launch(client, token, name):
    return client.post("/api/workspaces", json={"name": name, "image_id": add_image(name=f"img-{uuid.uuid4().hex[:8]}")},
                       headers=auth_header(token))


def _defaults(client, admin, **values):
    r = client.put("/api/admin/settings", json=values, headers=auth_header(admin))
    assert r.status_code == 200, r.text
    return r.json()


def test_no_limits_by_default(client):
    admin, _ = setup_admin(client)
    _, bob = _user(client, admin)
    for i in range(4):
        assert _launch(client, bob, f"ws{i}").status_code == 201
    limits = client.get("/api/workspaces/limits", headers=auth_header(bob)).json()
    assert limits == {"max_workspaces": 0, "max_running": 0, "workspaces": 4, "running": 4}


def test_running_cap_counts_starting_workspaces_and_frees_on_stop(client):
    admin, _ = setup_admin(client)
    _, bob = _user(client, admin, max_running=2)
    a = _launch(client, bob, "a").json()["id"]
    assert _launch(client, bob, "b").status_code == 201  # both still "creating"
    r = _launch(client, bob, "c")
    assert r.status_code == 409 and "at most 2 workspaces at once" in r.json()["detail"]

    # A stopped workspace can't start while two are running...
    set_workspace_status(a, "stopped")
    c = _launch(client, bob, "c").json()["id"]
    set_workspace_status(c, "stopped")
    set_workspace_status(a, "running")
    r = client.post(f"/api/workspaces/{c}/start", headers=auth_header(bob))
    assert r.status_code == 409 and "Stop one first" in r.json()["detail"]
    # ...and can once one is stopping (its slot is already given back).
    set_workspace_status(a, "stopping")
    assert client.post(f"/api/workspaces/{c}/start", headers=auth_header(bob)).status_code == 200


def test_total_cap_and_clone(client):
    admin, _ = setup_admin(client)
    _, bob = _user(client, admin, max_workspaces=2)
    a = _launch(client, bob, "a").json()["id"]
    _launch(client, bob, "b")
    r = _launch(client, bob, "c")
    assert r.status_code == 409 and "at most 2 workspaces. Delete one first" in r.json()["detail"]
    set_workspace_status(a, "stopped")
    r = client.post(f"/api/workspaces/{a}/clone", json={"name": "a2"}, headers=auth_header(bob))
    assert r.status_code == 409 and "at most 2 workspaces" in r.json()["detail"]


def test_defaults_apply_until_overridden(client):
    admin, _ = setup_admin(client)
    out = _defaults(client, admin, default_max_workspaces=1, default_max_running=1)
    assert out["default_max_workspaces"] == 1 and out["default_max_running"] == 1
    bob_user, bob = _user(client, admin)
    assert _launch(client, bob, "a").status_code == 201
    assert _launch(client, bob, "b").status_code == 409

    # 0 = no limit for this account, whatever the default.
    client.patch(f"/api/admin/users/{bob_user['id']}", json={"max_workspaces": 0, "max_running": 0},
                 headers=auth_header(admin))
    assert _launch(client, bob, "b").status_code == 201
    me = client.get("/api/auth/me", headers=auth_header(bob)).json()
    assert me["max_workspaces"] == 0 and me["max_running"] == 0

    # Leaving a field out keeps it; an explicit null goes back to the default.
    client.patch(f"/api/admin/users/{bob_user['id']}", json={"max_running": None}, headers=auth_header(admin))
    me = client.get("/api/auth/me", headers=auth_header(bob)).json()
    assert me["max_workspaces"] == 0 and me["max_running"] is None
    limits = client.get("/api/workspaces/limits", headers=auth_header(bob)).json()
    assert limits["max_workspaces"] == 0 and limits["max_running"] == 1


def test_admins_are_never_capped(client):
    admin, _ = setup_admin(client)
    _defaults(client, admin, default_max_workspaces=1, default_max_running=1)
    for name in ("a", "b", "c"):
        assert _launch(client, admin, name).status_code == 201
    assert client.get("/api/workspaces/limits", headers=auth_header(admin)).json()["max_running"] == 0


def test_admin_may_start_a_users_workspace_past_their_cap(client):
    admin, _ = setup_admin(client)
    _, bob = _user(client, admin, max_running=1)
    a = _launch(client, bob, "a").json()["id"]
    set_workspace_status(a, "stopped")
    b = _launch(client, bob, "b").json()["id"]
    set_workspace_status(b, "running")
    assert client.post(f"/api/workspaces/{a}/start", headers=auth_header(bob)).status_code == 409
    assert client.post(f"/api/workspaces/{a}/start", headers=auth_header(admin)).status_code == 200


def test_racing_starts_cannot_both_take_the_last_slot(client):
    admin, _ = setup_admin(client)
    _, bob = _user(client, admin, max_running=1)
    ids = []
    for name in ("a", "b", "c"):
        ws = _launch(client, bob, name).json()["id"] if not ids else None
        if ws is None:
            # Launch would be refused at the cap; make room, launch, then stop.
            set_workspace_status(ids[-1], "stopped")
            ws = _launch(client, bob, name).json()["id"]
        ids.append(ws)
    for ws in ids:
        set_workspace_status(ws, "stopped")

    barrier = threading.Barrier(len(ids))
    codes = []

    def start(ws_id):
        barrier.wait()
        codes.append(client.post(f"/api/workspaces/{ws_id}/start", headers=auth_header(bob)).status_code)

    threads = [threading.Thread(target=start, args=(ws,)) for ws in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [200, 409, 409]


def test_limits_are_validated(client):
    admin, _ = setup_admin(client)
    u, _ = _user(client, admin)
    assert client.patch(f"/api/admin/users/{u['id']}", json={"max_running": -1},
                        headers=auth_header(admin)).status_code == 422
    assert client.put("/api/admin/settings", json={"default_max_workspaces": -3},
                      headers=auth_header(admin)).status_code == 422
