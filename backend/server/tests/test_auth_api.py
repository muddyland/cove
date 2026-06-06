"""API tests for the auth router (setup/login/refresh/logout/me/rate-limit)."""

from server.config import get_settings
from server.tests.helpers import auth_header, login, setup_admin


def test_config_needs_setup_initially(client):
    resp = client.get("/api/auth/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["needs_setup"] is True
    assert body["oidc_enabled"] is False


def test_setup_creates_admin_and_sets_cookies(client):
    token, resp = setup_admin(client)
    assert token
    settings = get_settings()
    assert settings.cookie_session_name in resp.cookies
    assert settings.cookie_refresh_name in resp.cookies
    # needs_setup flips to False afterwards.
    assert client.get("/api/auth/config").json()["needs_setup"] is False


def test_second_setup_returns_410(client):
    setup_admin(client)
    resp = client.post(
        "/api/auth/setup", json={"username": "other", "password": "password123"}
    )
    assert resp.status_code == 410


def test_setup_rejects_short_password(client):
    resp = client.post("/api/auth/setup", json={"username": "a", "password": "short"})
    assert resp.status_code == 400


def test_login_good_credentials(client):
    setup_admin(client)
    # Clear session cookies so login is exercised cleanly.
    client.cookies.clear()
    resp = login(client, "admin", "password123")
    assert resp.status_code == 200
    assert resp.json()["access_token"]
    settings = get_settings()
    assert settings.cookie_session_name in resp.cookies
    assert settings.cookie_refresh_name in resp.cookies


def test_login_bad_credentials(client):
    setup_admin(client)
    client.cookies.clear()
    resp = login(client, "admin", "wrong-password")
    assert resp.status_code == 401


def test_login_unknown_user_rejected(client):
    # A non-existent account returns the same uniform 401 as a wrong password
    # (the dummy-hash verify keeps timing/behavior indistinguishable).
    setup_admin(client)
    client.cookies.clear()
    resp = login(client, "no-such-user", "whatever-password")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


def test_me_with_bearer_token(client):
    token, _ = setup_admin(client)
    client.cookies.clear()
    resp = client.get("/api/auth/me", headers=auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "admin"
    assert body["is_admin"] is True


def test_me_without_token_rejected(client):
    setup_admin(client)
    client.cookies.clear()
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_refresh_with_cookie_returns_new_token(client):
    setup_admin(client)  # sets the refresh cookie on the client jar
    resp = client.post("/api/auth/refresh")
    assert resp.status_code == 200
    assert resp.json()["access_token"]


def test_refresh_without_cookie_rejected(client):
    setup_admin(client)
    client.cookies.clear()
    resp = client.post("/api/auth/refresh")
    assert resp.status_code == 401


def test_logout_revokes_old_token(client):
    token, _ = setup_admin(client)
    # The token works before logout.
    assert client.get("/api/auth/me", headers=auth_header(token)).status_code == 200
    # Logout (uses the session cookie / bearer).
    resp = client.post("/api/auth/logout", headers=auth_header(token))
    assert resp.status_code == 200
    # The old token is now revoked (tokens_valid_from > iat).
    client.cookies.clear()
    assert client.get("/api/auth/me", headers=auth_header(token)).status_code == 401


def test_login_rate_limit_returns_429(client):
    setup_admin(client)
    client.cookies.clear()
    settings = get_settings()
    limit = settings.login_rate_limit
    # Exhaust the per-IP bucket with failed attempts.
    for _ in range(limit):
        login(client, "admin", "wrong-password")
    # The next attempt (even with correct creds) is rate-limited.
    resp = login(client, "admin", "password123")
    assert resp.status_code == 429


def test_oidc_only_disables_local_auth(client, monkeypatch):
    """When COVE_OIDC_ONLY is set (with OIDC configured), local login + setup are
    rejected and /config reports oidc_only=True."""
    monkeypatch.setenv("COVE_OIDC_ISSUER", "https://idp.example.com/")
    monkeypatch.setenv("COVE_OIDC_CLIENT_ID", "cid")
    monkeypatch.setenv("COVE_OIDC_CLIENT_SECRET", "secret")
    monkeypatch.setenv("COVE_OIDC_ONLY", "true")
    get_settings.cache_clear()
    try:
        cfg = client.get("/api/auth/config").json()
        assert cfg["oidc_only"] is True
        assert cfg["needs_setup"] is False  # no local setup in OIDC-only mode
        assert client.post(
            "/api/auth/setup", json={"username": "admin", "password": "password123"}
        ).status_code == 403
        assert client.post(
            "/api/auth/login", json={"username": "admin", "password": "password123"}
        ).status_code == 403
    finally:
        get_settings.cache_clear()
