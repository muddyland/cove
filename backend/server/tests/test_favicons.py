"""Tests for site favicons on browser workspaces (server.favicons + the endpoint).

The fetch itself talks to third-party sites, so what is covered here is
everything around it: which workspaces get an icon at all, how a page's declared
icons are ranked, the normalization to PNG, the addresses the server refuses to
fetch from, and the endpoint's ownership/caching behaviour.
"""

import asyncio
import io

import pytest
from PIL import Image

from server.db import SessionLocal
from server.favicons import (
    _host_is_fetchable,
    icon_candidates,
    refresh_workspace_favicon,
    site_origin,
    to_png,
)
from server.models import Workspace
from server.tests.helpers import (
    add_image,
    auth_header,
    create_user_via_admin,
    login,
    set_workspace_status,
    setup_admin,
)


def _png(size=(64, 64), colour=(200, 40, 90, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, colour).save(buf, "PNG")
    return buf.getvalue()


def _ico(sizes=((16, 16), (64, 64))) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", max(sizes), (10, 200, 180, 255)).save(buf, "ICO", sizes=sizes)
    return buf.getvalue()


# --- which workspaces get an icon -----------------------------------------


def test_site_origin_for_a_single_url():
    assert site_origin("https://github.com/anthropics") == "https://github.com"
    assert site_origin("http://nas.lan:8080/ui") == "http://nas.lan:8080"


def test_site_origin_is_none_for_several_urls():
    """A workspace opening several sites has no one site to stand for it, so it
    keeps the browser's own logo rather than picking a winner arbitrarily."""
    assert site_origin("https://github.com\nhttps://news.ycombinator.com") is None


def test_site_origin_is_none_without_a_url():
    assert site_origin(None) is None
    assert site_origin("") is None
    assert site_origin("   ") is None


def test_site_origin_drops_credentials():
    """The origin is fetched by the server, so a password that happens to be in
    the stored URL must not be replayed to the site."""
    origin = site_origin("https://user:secret@intranet.example/dash")
    assert origin == "https://intranet.example"
    assert "secret" not in origin


# --- icon discovery -------------------------------------------------------


def test_icon_candidates_prefers_the_largest_declared_icon():
    html = """
    <html><head>
      <link rel="icon" sizes="16x16" href="/small.png">
      <link rel="apple-touch-icon" sizes="180x180" href="/touch.png">
      <link rel="icon" sizes="32x32" href="/medium.png">
    </head></html>
    """
    found = icon_candidates(html, "https://site.test")
    assert found[0] == "https://site.test/touch.png"
    assert found.index("https://site.test/medium.png") < found.index(
        "https://site.test/small.png"
    )


def test_icon_candidates_try_the_well_known_path_before_a_tiny_declared_icon():
    """A page declaring only a 32px icon usually still serves a 180px one at the
    conventional path — and that's the difference between a crisp home-screen
    install and an upscaled blur."""
    found = icon_candidates(
        '<link rel="icon" sizes="32x32" href="/f.png">', "https://site.test"
    )
    assert found[0] == "https://site.test/apple-touch-icon.png"
    assert "https://site.test/f.png" in found


def test_icon_candidates_keep_a_large_declared_icon_first():
    found = icon_candidates(
        '<link rel="apple-touch-icon" sizes="180x180" href="/t.png">',
        "https://site.test",
    )
    assert found[0] == "https://site.test/t.png"


def test_icon_candidates_always_falls_back_to_favicon_ico():
    """Plenty of sites serve /favicon.ico without ever mentioning it in markup."""
    assert icon_candidates("", "https://site.test")[-1] == "https://site.test/favicon.ico"
    with_link = icon_candidates('<link rel="icon" href="/i.png">', "https://site.test")
    assert with_link[-1] == "https://site.test/favicon.ico"


def test_icon_candidates_resolves_relative_and_absolute_hrefs():
    html = (
        '<link rel="shortcut icon" href="icons/fav.png">'
        '<link rel="icon" href="https://cdn.other.test/f.png">'
    )
    found = icon_candidates(html, "https://site.test")
    assert "https://site.test/icons/fav.png" in found
    assert "https://cdn.other.test/f.png" in found


def test_icon_candidates_ignores_non_icon_links():
    html = '<link rel="stylesheet" href="/app.css"><link rel="canonical" href="/x">'
    # Only the well-known paths remain — no stylesheet ever gets fetched as an icon.
    assert icon_candidates(html, "https://site.test") == [
        "https://site.test/apple-touch-icon.png",
        "https://site.test/favicon.ico",
    ]


def test_icon_candidates_are_capped():
    html = "".join(
        f'<link rel="icon" sizes="{n}x{n}" href="/i{n}.png">' for n in range(10, 60)
    )
    assert len(icon_candidates(html, "https://site.test")) <= 5


# --- normalization --------------------------------------------------------


def test_to_png_normalizes_an_ico():
    """Sites serve favicons as ICO, PNG, GIF and JPEG; storing one format keeps
    that variety out of the UI and the manifest."""
    out = to_png(_ico())
    img = Image.open(io.BytesIO(out))
    assert img.format == "PNG"
    assert img.mode == "RGBA"


def test_to_png_downscales_an_oversized_icon():
    out = to_png(_png((900, 900)))
    img = Image.open(io.BytesIO(out))
    assert max(img.size) == 256


def test_to_png_keeps_a_small_icon_at_its_own_size():
    img = Image.open(io.BytesIO(to_png(_png((32, 32)))))
    assert img.size == (32, 32)


def test_to_png_rejects_an_svg():
    """SVG favicons are common and Pillow can't rasterize one — the workspace
    keeps its browser logo rather than the request failing."""
    assert to_png(b'<svg xmlns="http://www.w3.org/2000/svg"><rect /></svg>') is None


def test_to_png_rejects_junk_and_tracking_pixels():
    assert to_png(b"<!doctype html><html>not an image</html>") is None
    assert to_png(_png((1, 1))) is None


# --- what the server will fetch -------------------------------------------


def test_loopback_is_refused():
    """The URL is user-supplied, so this is the one place a workspace owner could
    aim the control plane itself — loopback reaches Cove's own API without going
    through Traefik."""
    assert asyncio.run(_host_is_fetchable("127.0.0.1")) is False
    assert asyncio.run(_host_is_fetchable("localhost")) is False


def test_link_local_metadata_address_is_refused():
    assert asyncio.run(_host_is_fetchable("169.254.169.254")) is False


def test_private_lan_addresses_are_allowed():
    """Self-hosted sites on the LAN are exactly what these workspaces point at,
    so RFC1918 stays fetchable on purpose."""
    assert asyncio.run(_host_is_fetchable("192.168.1.10")) is True
    assert asyncio.run(_host_is_fetchable("10.0.0.5")) is True


def test_unresolvable_host_is_refused():
    assert asyncio.run(_host_is_fetchable("no-such-host.invalid")) is False


# --- refresh --------------------------------------------------------------


def _browser_workspace(client, url="https://site.test"):
    image_id = add_image(name="Chromium", image_type="browser", url_env="CHROME_CLI")
    resp = client.post(
        "/api/workspaces",
        json={"name": "site", "image_id": image_id, "target_url": url},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _stored(ws_id):
    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        return ws.favicon_png, ws.favicon_origin, ws.favicon_at
    finally:
        db.close()


def _set_favicon(ws_id, png, origin):
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        ws.favicon_png = png
        ws.favicon_origin = origin
        ws.favicon_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


def test_refresh_stores_the_fetched_icon(client, monkeypatch):
    setup_admin(client)
    ws_id = _browser_workspace(client)
    icon = _png()

    async def fake_fetch(origin):
        assert origin == "https://site.test"
        return icon

    monkeypatch.setattr("server.favicons.fetch_favicon", fake_fetch)
    asyncio.run(refresh_workspace_favicon(ws_id))

    png, origin, at = _stored(ws_id)
    assert png == icon
    assert origin == "https://site.test"
    assert at is not None


def test_refresh_skips_a_site_it_already_has(client, monkeypatch):
    """Editing an unrelated field re-runs the refresh; it must not re-fetch the
    same site's icon every time."""
    setup_admin(client)
    ws_id = _browser_workspace(client)
    _set_favicon(ws_id, _png(), "https://site.test")

    called = False

    async def fake_fetch(origin):
        nonlocal called
        called = True
        return _png()

    monkeypatch.setattr("server.favicons.fetch_favicon", fake_fetch)
    asyncio.run(refresh_workspace_favicon(ws_id))
    assert called is False


def test_refresh_clears_the_icon_when_a_second_site_is_added(client):
    """Two sites open means no single site represents the workspace — the stored
    mark has to go, or the card keeps claiming to be a site it no longer is."""
    setup_admin(client)
    ws_id = _browser_workspace(client)
    _set_favicon(ws_id, _png(), "https://site.test")
    set_workspace_status(ws_id, "stopped")

    resp = client.patch(
        f"/api/workspaces/{ws_id}",
        json={"target_url": "https://site.test\nhttps://other.test"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["favicon_at"] is None
    assert _stored(ws_id)[0] is None


def test_refresh_keeps_no_icon_when_the_site_has_none(client, monkeypatch):
    setup_admin(client)
    ws_id = _browser_workspace(client)

    async def fake_fetch(origin):
        return None

    monkeypatch.setattr("server.favicons.fetch_favicon", fake_fetch)
    asyncio.run(refresh_workspace_favicon(ws_id))

    png, origin, at = _stored(ws_id)
    assert (png, origin, at) == (None, None, None)


def test_refresh_ignores_a_desktop_workspace(client, monkeypatch):
    setup_admin(client)
    image_id = add_image(name="Desktop")
    ws_id = client.post(
        "/api/workspaces", json={"name": "node", "image_id": image_id}
    ).json()["id"]

    async def fake_fetch(origin):  # pragma: no cover - must not run
        raise AssertionError("a desktop workspace has no site to fetch")

    monkeypatch.setattr("server.favicons.fetch_favicon", fake_fetch)
    asyncio.run(refresh_workspace_favicon(ws_id))
    assert _stored(ws_id)[0] is None


def test_refresh_survives_a_deleted_workspace(client):
    """The refresh runs after the response, so the workspace can be gone by the
    time it lands; that must not raise into the background task runner."""
    setup_admin(client)
    asyncio.run(refresh_workspace_favicon(99999))


def test_editing_the_url_drops_the_old_sites_icon(client):
    setup_admin(client)
    ws_id = _browser_workspace(client)
    _set_favicon(ws_id, _png(), "https://site.test")
    set_workspace_status(ws_id, "stopped")

    resp = client.patch(
        f"/api/workspaces/{ws_id}", json={"target_url": "https://elsewhere.test"}
    )
    assert resp.status_code == 200, resp.text
    # Cleared in the same transaction as the edit: showing the previous site's
    # mark on a workspace that now opens a different one is worse than none.
    assert resp.json()["favicon_at"] is None
    assert _stored(ws_id) == (None, None, None)


def test_editing_another_field_keeps_the_icon(client):
    setup_admin(client)
    ws_id = _browser_workspace(client)
    _set_favicon(ws_id, _png(), "https://site.test")
    set_workspace_status(ws_id, "stopped")

    resp = client.patch(f"/api/workspaces/{ws_id}", json={"name": "renamed"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["favicon_at"] is not None


# --- endpoint -------------------------------------------------------------


def test_favicon_served_with_etag_and_private_caching(client):
    token, _ = setup_admin(client)
    ws_id = _browser_workspace(client)
    icon = _png()
    _set_favicon(ws_id, icon, "https://site.test")

    resp = client.get(f"/api/workspaces/{ws_id}/favicon.png", headers=auth_header(token))
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == icon
    # Which site someone runs a workspace on is theirs to know.
    assert "private" in resp.headers["cache-control"]

    again = client.get(
        f"/api/workspaces/{ws_id}/favicon.png",
        headers={**auth_header(token), "If-None-Match": resp.headers["etag"]},
    )
    assert again.status_code == 304


def test_favicon_404_when_absent(client):
    """The ordinary case for a desktop workspace or a site with no usable icon —
    the UI falls back to the browser logo."""
    token, _ = setup_admin(client)
    ws_id = _browser_workspace(client)

    resp = client.get(f"/api/workspaces/{ws_id}/favicon.png", headers=auth_header(token))
    assert resp.status_code == 404


def test_favicon_is_not_readable_by_another_user(client):
    admin_token, _ = setup_admin(client)
    ws_id = _browser_workspace(client)
    _set_favicon(ws_id, _png(), "https://site.test")
    create_user_via_admin(client, admin_token, "someone")
    other = login(client, "someone", "password123").json()["access_token"]

    resp = client.get(f"/api/workspaces/{ws_id}/favicon.png", headers=auth_header(other))
    assert resp.status_code == 403


def test_favicon_requires_auth(client):
    setup_admin(client)
    ws_id = _browser_workspace(client)
    _set_favicon(ws_id, _png(), "https://site.test")
    # Setup leaves a session cookie on the test client, and the endpoint accepts
    # cookie auth — drop it so this exercises the unauthenticated path.
    client.cookies.clear()
    assert client.get(f"/api/workspaces/{ws_id}/favicon.png").status_code == 401


def test_workspace_list_reports_whether_an_icon_exists(client):
    token, _ = setup_admin(client)
    ws_id = _browser_workspace(client)

    def favicon_at():
        body = client.get("/api/workspaces", headers=auth_header(token)).json()
        return next(w["favicon_at"] for w in body if w["id"] == ws_id)

    assert favicon_at() is None
    _set_favicon(ws_id, _png(), "https://site.test")
    assert favicon_at() is not None


def test_manifest_uses_the_site_icon_over_the_image_logo(client):
    """An installed browser workspace that only opens one site should land on the
    home screen as that site, not as the browser."""
    token, _ = setup_admin(client)
    ws_id = _browser_workspace(client)
    _set_favicon(ws_id, _png(), "https://site.test")

    resp = client.get(
        f"/api/workspaces/{ws_id}/manifest.webmanifest", headers=auth_header(token)
    )
    assert resp.status_code == 200
    icons = resp.json()["icons"]
    assert icons[0]["src"].startswith("data:image/png;base64,")
    # Scaled to 512: a favicon at its native size is under the 144px Chrome wants
    # before it will offer to install at all.
    assert icons[0]["sizes"] == "512x512"


@pytest.mark.parametrize("url", ["https://site.test", "https://site.test/deep/path"])
def test_clone_carries_the_icon_over(client, url):
    token, _ = setup_admin(client)
    ws_id = _browser_workspace(client, url)
    icon = _png()
    _set_favicon(ws_id, icon, "https://site.test")
    set_workspace_status(ws_id, "stopped")

    resp = client.post(f"/api/workspaces/{ws_id}/clone", json={"name": "site-copy"})
    assert resp.status_code == 201, resp.text
    assert _stored(resp.json()["id"])[0] == icon
