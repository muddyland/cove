"""Stream leases: which open page holds a workspace's stream, so a page that
another screen took the stream from can say where it went."""

import pytest

from server import stream_leases
from server.tests.helpers import add_image, auth_header, create_user_via_admin, login, setup_admin

A = "viewer-aaaa-1111"
B = "viewer-bbbb-2222"
FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"


@pytest.fixture(autouse=True)
def _fresh():
    stream_leases.clear()
    yield
    stream_leases.clear()


def _workspace(client, token):
    image_id = add_image()
    r = client.post("/api/workspaces", json={"name": "desk", "image_id": image_id}, headers=auth_header(token))
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_second_viewer_takes_over_and_the_first_learns_where(client):
    token, _ = setup_admin(client)
    ws = _workspace(client, token)
    h = {**auth_header(token), "User-Agent": FIREFOX}

    r = client.post(f"/api/workspaces/{ws}/stream-lease", json={"viewer_id": A}, headers=h)
    assert r.status_code == 200 and r.json()["holder"] is True
    assert client.get(f"/api/workspaces/{ws}/stream-lease", params={"viewer_id": A}, headers=h).json()["holder"]

    # Another screen opens the same workspace.
    client.post(f"/api/workspaces/{ws}/stream-lease", json={"viewer_id": B}, headers=auth_header(token))
    gone = client.get(f"/api/workspaces/{ws}/stream-lease", params={"viewer_id": A}, headers=h).json()
    assert gone["holder"] is False and gone["since"]
    # ...and the new holder keeps it.
    assert client.get(f"/api/workspaces/{ws}/stream-lease", params={"viewer_id": B},
                      headers=auth_header(token)).json()["holder"] is True

    # Taking it back works the same way.
    client.post(f"/api/workspaces/{ws}/stream-lease", json={"viewer_id": A}, headers=h)
    back = client.get(f"/api/workspaces/{ws}/stream-lease", params={"viewer_id": B}, headers=auth_header(token))
    assert back.json() == {**back.json(), "holder": False, "label": "Firefox on Linux"}


def test_thin_client_is_named(client):
    from server.tests.test_thin_clients import _bind, _enroll

    token, _ = setup_admin(client)
    ws = _workspace(client, token)
    _, dtoken = _enroll(client, token, "Desk")
    from fastapi.testclient import TestClient

    kiosk = TestClient(client.app)
    _bind(kiosk, dtoken)
    k = login(kiosk, "admin", "password123").json()["access_token"]
    client.post(f"/api/workspaces/{ws}/stream-lease", json={"viewer_id": A}, headers=auth_header(token))
    kiosk.post(f"/api/workspaces/{ws}/stream-lease", json={"viewer_id": B}, headers=auth_header(k))
    gone = client.get(f"/api/workspaces/{ws}/stream-lease", params={"viewer_id": A}, headers=auth_header(token))
    assert gone.json()["holder"] is False and gone.json()["label"] == "Desk (thin client)"


def test_lost_or_expired_lease_is_reclaimed(client, monkeypatch):
    token, _ = setup_admin(client)
    ws = _workspace(client, token)
    # Nothing recorded (e.g. Cove restarted): the asking page simply holds it.
    assert client.get(f"/api/workspaces/{ws}/stream-lease", params={"viewer_id": A},
                      headers=auth_header(token)).json()["holder"] is True
    # A holder that stopped checking in long ago no longer blocks anyone.
    now = stream_leases._now()
    monkeypatch.setattr(stream_leases, "_now", lambda: now + stream_leases.LEASE_TTL + 1)
    assert client.get(f"/api/workspaces/{ws}/stream-lease", params={"viewer_id": B},
                      headers=auth_header(token)).json()["holder"] is True


def test_release_and_stop_forget_the_holder(client):
    token, _ = setup_admin(client)
    ws = _workspace(client, token)
    client.post(f"/api/workspaces/{ws}/stream-lease", json={"viewer_id": A}, headers=auth_header(token))
    # A non-holder can't release someone else's lease.
    client.delete(f"/api/workspaces/{ws}/stream-lease", params={"viewer_id": B}, headers=auth_header(token))
    assert client.get(f"/api/workspaces/{ws}/stream-lease", params={"viewer_id": B},
                      headers=auth_header(token)).json()["holder"] is False
    assert client.delete(f"/api/workspaces/{ws}/stream-lease", params={"viewer_id": A},
                         headers=auth_header(token)).status_code == 204
    assert client.get(f"/api/workspaces/{ws}/stream-lease", params={"viewer_id": B},
                      headers=auth_header(token)).json()["holder"] is True


def test_peek_says_who_holds_it_without_claiming(client):
    token, _ = setup_admin(client)
    ws = _workspace(client, token)
    h = {**auth_header(token), "User-Agent": FIREFOX}
    peek = lambda viewer: client.get(  # noqa: E731
        f"/api/workspaces/{ws}/stream-lease", params={"viewer_id": viewer, "peek": "true"}, headers=h
    ).json()

    # Nobody has it: fine to open, and peeking didn't take it.
    assert peek(B) == {"holder": True, "label": None, "since": None}
    assert stream_leases.current(ws) is None

    client.post(f"/api/workspaces/{ws}/stream-lease", json={"viewer_id": A}, headers=h)
    seen = peek(B)
    assert seen["holder"] is False and seen["label"] == "Firefox on Linux" and seen["since"]
    # Still A's: a page that only asked (and was then sent back) took nothing.
    assert stream_leases.current(ws).viewer_id == A
    # The holder itself isn't warned about itself (reloading its own stream).
    assert peek(A)["holder"] is True


def _listed(client, token, ws):
    return next(w for w in client.get("/api/workspaces", headers=auth_header(token)).json() if w["id"] == ws)


def test_workspace_list_says_where_a_running_stream_is_open(client, monkeypatch):
    from server.tests.helpers import set_workspace_status

    token, _ = setup_admin(client)
    ws = _workspace(client, token)
    set_workspace_status(ws, "running")
    assert _listed(client, token, ws)["in_use_by"] is None

    h = {**auth_header(token), "User-Agent": FIREFOX}
    client.post(f"/api/workspaces/{ws}/stream-lease", json={"viewer_id": A}, headers=h)
    row = _listed(client, token, ws)
    assert row["in_use_by"] == "Firefox on Linux" and row["in_use_since"]

    # A page that stopped checking in (closed without releasing) stops counting.
    now = stream_leases._now()
    monkeypatch.setattr(stream_leases, "_now", lambda: now + stream_leases.LEASE_TTL + 1)
    assert _listed(client, token, ws)["in_use_by"] is None
    monkeypatch.setattr(stream_leases, "_now", lambda: now)

    # Only a running workspace is "in use".
    set_workspace_status(ws, "stopped")
    assert _listed(client, token, ws)["in_use_by"] is None


def test_other_users_and_bad_ids_are_refused(client):
    token, _ = setup_admin(client)
    ws = _workspace(client, token)
    create_user_via_admin(client, token, "bob")
    bob = login(client, "bob", "password123").json()["access_token"]
    assert client.post(f"/api/workspaces/{ws}/stream-lease", json={"viewer_id": A},
                       headers=auth_header(bob)).status_code == 403  # as every workspace endpoint
    assert client.get(f"/api/workspaces/{ws}/stream-lease", params={"viewer_id": "x;rm"},
                      headers=auth_header(token)).status_code == 422
    assert client.post(f"/api/workspaces/{ws}/stream-lease", json={"viewer_id": "short"},
                       headers=auth_header(token)).status_code == 422


@pytest.mark.parametrize("ua,label", [
    (FIREFOX, "Firefox on Linux"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36 "
     "Edg/141.0", "Edge on Windows"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
     "Safari/605.1.15", "Safari on macOS"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile Safari/604.1",
     "Safari on iOS"),
    ("Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/141.0 Mobile Safari/537.36", "Chrome on Android"),
    ("", "a browser"),
])
def test_describe_browser(ua, label):
    assert stream_leases.describe_browser(ua) == label
