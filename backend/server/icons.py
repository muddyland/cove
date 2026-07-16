"""Bake per-image PWA icons: the project logo with a small Cove watermark.

Done once when the catalog syncs (not per request) so an installed workspace PWA
carries a Cove mark that identifies it at a glance on a crowded home screen. The
composited PNG is cached on ``WorkspaceImage.icon_png`` and embedded straight into
the per-workspace manifest — a real raster icon, so it renders everywhere (an
earlier SVG-overlay approach didn't install in some browsers, e.g. Brave).

Pillow only: the LinuxServer project logos are PNG/JPEG raster. The rare logo
Pillow can't decode (a handful are SVG) is left un-watermarked — the manifest
falls back to the raw logo.
"""

import io
import logging
from functools import lru_cache
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_ICON_SIZE = 512
# Badge diameter as a fraction of the icon, and its gap from the bottom-right
# corner. Small enough to read as a watermark, big enough to recognize.
_BADGE_FRACTION = 0.34
_BADGE_MARGIN = 14
_LOGO_MAX_BYTES = 512 * 1024
_BADGE_PATH = Path(__file__).parent / "assets" / "cove_badge.png"


@lru_cache(maxsize=1)
def _badge():
    """The Cove watermark badge (a dark, cyan-ringed disc with the Cove mark),
    loaded once. It's the app's favicon glyph pre-rendered to a 256x256 PNG so
    this module needs only Pillow (no SVG rasterizer) at runtime."""
    from PIL import Image

    return Image.open(_BADGE_PATH).convert("RGBA")


def bake_watermarked_icon(logo_bytes: bytes) -> "bytes | None":
    """Composite the Cove badge onto a project logo -> a 512x512 PNG.

    Returns None if the logo can't be decoded (e.g. an SVG), so the caller leaves
    the image un-watermarked rather than dropping its icon entirely.
    """
    from PIL import Image, UnidentifiedImageError

    try:
        logo = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError):
        return None

    canvas = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    # Fit the logo into the icon, aspect-preserved and centered.
    fitted = logo.copy()
    fitted.thumbnail((_ICON_SIZE, _ICON_SIZE), Image.LANCZOS)
    canvas.alpha_composite(
        fitted,
        ((_ICON_SIZE - fitted.width) // 2, (_ICON_SIZE - fitted.height) // 2),
    )
    # Cove badge in the bottom-right corner.
    d = round(_ICON_SIZE * _BADGE_FRACTION)
    badge = _badge().resize((d, d), Image.LANCZOS)
    offset = _ICON_SIZE - d - _BADGE_MARGIN
    canvas.alpha_composite(badge, (offset, offset))

    out = io.BytesIO()
    canvas.save(out, "PNG", optimize=True)
    return out.getvalue()


async def _fetch_logo_bytes(client: httpx.AsyncClient, url: str) -> "bytes | None":
    """Fetch a logo image, returning its bytes (or None on any error/oversize)."""
    try:
        r = await client.get(url)
    except httpx.HTTPError:
        return None
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip()
    if (
        r.status_code == 200
        and r.content
        and ctype.startswith("image/")
        and len(r.content) <= _LOGO_MAX_BYTES
    ):
        return r.content
    return None


async def refresh_image_icons(db, *, only_missing: bool = True) -> int:
    """(Re)bake ``WorkspaceImage.icon_png`` for catalog images that have a logo.

    ``only_missing`` (the default, and the sync path) bakes just rows without an
    icon yet — new images, and ones whose ``icon_png`` was cleared because their
    logo changed. Best-effort per image; the caller need not pre-check. Commits
    and returns the number of icons baked.
    """
    from sqlalchemy import select

    from server.models import WorkspaceImage

    rows = [r for r in db.scalars(select(WorkspaceImage)).all() if r.logo_url]
    if only_missing:
        rows = [r for r in rows if not r.icon_png]
    if not rows:
        return 0

    baked = 0
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        for row in rows:
            data = await _fetch_logo_bytes(client, row.logo_url)
            if not data:
                continue
            icon = bake_watermarked_icon(data)
            if icon:
                row.icon_png = icon
                baked += 1
    if baked:
        db.commit()
    return baked
