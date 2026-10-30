"""Local network only accounts: sign-in, sessions, the API and streams refuse
requests from outside the LAN networks, without being fooled by forwarded
headers and without letting an admin lock themselves out."""

import pytest

from server import net
from server.config import get_settings
from server.db import SessionLocal
from server.models import AuditLog, Workspace
from server.routers import auth as auth_router
from server.tests.helpers import add_image, auth_header, create_user_via_admin, setup_admin

LAN = {"X-Forwarded-For": "192.168.1.20"}
WAN = {"X-Forwarded-For": "203.0.113.9"}


@pytest.fixture(autouse=True)
def _fresh():
    net.set_trusted_proxies("")
    auth_router._rate_buckets.clear()
    yield
    net.set_trusted_proxies("")


def _lan_only_user(client, admin, name="bob"):
    u = create_user_via_admin(client, admin, name)
    r = client.patch(f"/api/admin/users/{u['id']}", json={"lan_only": True}, headers={**auth_header(admin), **LAN})
    assert r.status_code == 200 and r.json()["lan_only"] is True, r.text
    return u


def _login(client, name, headers, password="password123"):
    return client.post("/api/auth/login", json={"username": name, "password": password}, headers=headers)


def _audit(action):
    with SessionLocal() as db:
        return [a.detail for a in db.query(AuditLog).filter(AuditLog.action == action)]


def test_accounts_work_from_anywhere_by_default(client):
    admin, _ = setup_admin(client)
    create_user_via_admin(client, admin, "alice")
    assert _login(client, "alice", WAN).status_code == 200


def test_sign_in_only_from_the_lan(client):
    admin, _ = setup_admin(client)
    _lan_only_user(client, admin)
    assert _login(client, "bob", LAN).status_code == 200

    r = _login(client, "bob", WAN)
    assert r.status_code == 403
    assert r.headers["X-Cove-Error"] == "lan-only"
    assert r.json()["detail"] == "This account can only be used from the local network."
    assert _audit("login.lan_only") == ["bob"]


def test_a_wrong_password_from_outside_does_not_reveal_the_setting(client):
    admin, _ = setup_admin(client)
    _lan_only_user(client, admin)
    r = _login(client, "bob", WAN, password="wrong-password")
    assert r.status_code == 401 and "X-Cove-Error" not in r.headers


def test_a_session_from_the_lan_is_useless_outside_it(client):
    admin, _ = setup_admin(client)
    _lan_only_user(client, admin)
    token = _login(client, "bob", LAN).json()["access_token"]
    assert client.get("/api/auth/me", headers={**auth_header(token), **LAN}).status_code == 200

    r = client.get("/api/workspaces", headers={**auth_header(token), **WAN})
    assert r.status_code == 403 and r.headers["X-Cove-Error"] == "lan-only"
    # The refresh cookie from that sign-in can't mint a new session outside either.
    assert client.post("/api/auth/refresh", headers=WAN).status_code == 403
    assert client.post("/api/auth/refresh", headers=LAN).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        # A LAN address the client made up, then Traefik's real hop.
        {"X-Forwarded-For": "192.168.1.20, 203.0.113.9"},
        # The real hop is LAN, but something in front says the visitor isn't.
        {"X-Forwarded-For": "203.0.113.9, 192.168.1.20"},
        {"X-Forwarded-For": "192.168.1.20", "CF-Connecting-IP": "203.0.113.9"},
        {"X-Forwarded-For": "192.168.1.20", "True-Client-IP": "2001:db8::7"},
        {"X-Forwarded-For": "192.168.1.20", "Forwarded": 'for="[2001:db8::7]:443";proto=https'},
        # Docker's bridge gateway: outside clients can show up as it.
        {"X-Forwarded-For": "172.17.0.1"},
        {"X-Forwarded-For": "not-an-address"},
    ],
)
def test_forwarded_headers_cannot_make_an_outside_request_look_local(client, headers):
    admin, _ = setup_admin(client)
    _lan_only_user(client, admin)
    assert _login(client, "bob", headers).status_code == 403


def test_ipv6_and_mapped_addresses(client):
    admin, _ = setup_admin(client)
    _lan_only_user(client, admin)
    assert _login(client, "bob", {"X-Forwarded-For": "::ffff:192.168.1.20"}).status_code == 200
    assert _login(client, "bob", {"X-Forwarded-For": "fd00::42"}).status_code == 200
    assert _login(client, "bob", {"X-Forwarded-For": "2001:db8::42"}).status_code == 403


def test_trusted_proxies_are_skipped_to_find_the_visitor(client):
    admin, _ = setup_admin(client)
    _lan_only_user(client, admin)
    r = client.put("/api/admin/settings", json={"trusted_proxies": "10.9.0.2, junk"}, headers=auth_header(admin))
    assert r.status_code == 200 and r.json()["trusted_proxies"] == "10.9.0.2/32"

    assert _login(client, "bob", {"X-Forwarded-For": "192.168.1.20, 10.9.0.2"}).status_code == 200
    assert _login(client, "bob", {"X-Forwarded-For": "203.0.113.9, 10.9.0.2"}).status_code == 403
    # Only the proxy's own address: Cove can't see past it, so it isn't the LAN.
    assert _login(client, "bob", {"X-Forwarded-For": "10.9.0.2"}).status_code == 403


def test_lan_networks_are_configurable(client):
    admin, _ = setup_admin(client)
    _lan_only_user(client, admin)
    assert _login(client, "bob", {"X-Forwarded-For": "172.20.0.5"}).status_code == 403
    r = client.put("/api/admin/settings", json={"signin_lan_networks": "172.20.0.0/16 192.168.1.0/24 nope"},
                   headers=auth_header(admin))
    assert r.status_code == 200 and r.json()["signin_lan_networks"] == "172.20.0.0/16, 192.168.1.0/24"
    assert _login(client, "bob", {"X-Forwarded-For": "172.20.0.5"}).status_code == 200
    assert _login(client, "bob", {"X-Forwarded-For": "10.0.0.5"}).status_code == 403


def test_admins_cannot_lock_themselves_out(client):
    admin, _ = setup_admin(client)
    me = client.get("/api/auth/me", headers=auth_header(admin)).json()
    r = client.patch(f"/api/admin/users/{me['id']}", json={"lan_only": True}, headers={**auth_header(admin), **WAN})
    assert r.status_code == 400 and "203.0.113.9" in r.json()["detail"]
    r = client.patch(f"/api/admin/users/{me['id']}", json={"lan_only": True}, headers={**auth_header(admin), **LAN})
    assert r.status_code == 200

    # Now LAN-only: settings that would make this connection "outside" are refused, unchanged.
    r = client.put("/api/admin/settings", json={"signin_lan_networks": "10.0.0.0/8"},
                   headers={**auth_header(admin), **LAN})
    assert r.status_code == 400
    after = client.get("/api/admin/settings", headers={**auth_header(admin), **LAN}).json()
    assert after["signin_lan_networks"] == "10.0.0.0/8, 192.168.0.0/16, fc00::/7, fe80::/10"
    r = client.put("/api/admin/settings", json={"trusted_proxies": "192.168.1.0/24"},
                   headers={**auth_header(admin), **LAN})
    assert r.status_code == 400 and net.trusted_proxies() == ()


def test_workspace_streams_refuse_outside_requests(client):
    admin, _ = setup_admin(client)
    _lan_only_user(client, admin)
    client.cookies.clear()
    assert _login(client, "bob", LAN).status_code == 200  # session cookie in the jar
    ws = client.post("/api/workspaces", json={"name": "desk", "image_id": add_image()}, headers=LAN).json()
    with SessionLocal() as db:
        db.get(Workspace, ws["id"]).status = "running"
        db.commit()
    uri = {"X-Forwarded-Uri": f"/workspace/{ws['public_id']}/"}
    assert client.get("/api/auth/forward", headers={**uri, **LAN}).status_code == 200
    assert client.get("/api/auth/forward", headers={**uri, **WAN}).status_code == 401
    assert get_settings().cookie_session_name in client.cookies


def test_network_check_shows_what_cove_sees(client):
    admin, _ = setup_admin(client)
    r = client.get("/api/admin/network-check", headers={**auth_header(admin), **LAN})
    assert r.json() == {"client_ip": "192.168.1.20", "is_lan": True, "forwarded_for": ["192.168.1.20"],
                        "claimed": [], "warning": None}
    r = client.get("/api/admin/network-check",
                   headers={**auth_header(admin), "X-Forwarded-For": "10.9.0.2", "CF-Connecting-IP": "203.0.113.9"})
    body = r.json()
    assert body["is_lan"] is False and body["claimed"] == ["203.0.113.9"] and "Trusted proxies" in body["warning"]


def test_audit_and_user_listing(client):
    admin, _ = setup_admin(client)
    _lan_only_user(client, admin)
    users = client.get("/api/admin/users", headers=auth_header(admin)).json()
    assert {u["username"]: u["lan_only"] for u in users} == {"admin": False, "bob": True}
