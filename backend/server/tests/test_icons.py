"""Tests for the watermarked-icon baking (server.icons)."""

import io

from PIL import Image

from server.db import SessionLocal
from server.icons import bake_watermarked_icon, refresh_image_icons
from server.models import WorkspaceImage
from server.tests.helpers import add_image


def _png_bytes(size=(120, 120), color=(255, 120, 30, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_bake_watermarked_icon_produces_512_png():
    out = bake_watermarked_icon(_png_bytes())
    assert out is not None
    img = Image.open(io.BytesIO(out))
    assert img.format == "PNG"
    assert img.size == (512, 512)
    # The Cove badge is composited into the bottom-right corner, so a pixel there
    # is no longer the plain logo colour (it's the dark disc / cyan ring / mark).
    corner = img.convert("RGBA").getpixel((470, 470))
    assert corner != (255, 120, 30, 255)


def test_bake_returns_none_for_undecodable_bytes():
    # An SVG (or any non-raster) can't be opened by Pillow -> leave un-watermarked.
    assert bake_watermarked_icon(b"<svg xmlns='http://www.w3.org/2000/svg'/>") is None
    assert bake_watermarked_icon(b"not an image at all") is None


async def test_refresh_image_icons_bakes_missing_and_skips_baked(monkeypatch):
    logo_png = _png_bytes()

    async def _fake_fetch(_client, url):
        assert url == "https://logo.example/x.png"
        return logo_png

    monkeypatch.setattr("server.icons._fetch_logo_bytes", _fake_fetch)

    # One image needing an icon, one already baked (must be left untouched), one
    # with no logo (skipped entirely).
    need_id = add_image(name="Need", logo_url="https://logo.example/x.png")
    done_id = add_image(
        name="Done", logo_url="https://logo.example/x.png", icon_png=b"already-baked"
    )
    add_image(name="NoLogo")

    db = SessionLocal()
    try:
        baked = await refresh_image_icons(db, only_missing=True)
        assert baked == 1
        need = db.get(WorkspaceImage, need_id)
        done = db.get(WorkspaceImage, done_id)
        assert need.icon_png and Image.open(io.BytesIO(need.icon_png)).size == (512, 512)
        assert done.icon_png == b"already-baked"  # untouched
    finally:
        db.close()


async def test_refresh_image_icons_never_raises_on_bake_failure(monkeypatch):
    """A bake blowing up (e.g. Pillow missing) must be swallowed, not surface as a
    500 from the admin sync that calls this."""
    async def _fake_fetch(_client, _url):
        return b"logo-bytes"

    def _boom(_logo):
        raise RuntimeError("pillow exploded")

    monkeypatch.setattr("server.icons._fetch_logo_bytes", _fake_fetch)
    monkeypatch.setattr("server.icons.bake_watermarked_icon", _boom)
    add_image(name="X", logo_url="https://logo.example/x.png")

    db = SessionLocal()
    try:
        baked = await refresh_image_icons(db, only_missing=True)  # must not raise
        assert baked == 0
    finally:
        db.close()
