"""Site favicons for browser workspaces.

A browser workspace that opens one site *is* that site to the person using it —
a "GitHub" node, not "a Chromium node that happens to start on github.com". So
its icon is the site's own favicon: discovered from the page's ``<link rel=icon>``
tags (falling back to ``/favicon.ico``), fetched here, normalized to a small PNG
and cached on ``Workspace.favicon_png``.

Fetched server-side rather than pointing an ``<img>`` straight at the site: that
way it works for plain-http LAN sites (a mixed-content image on an https Cove
page is blocked outright), it finds icons that are only declared in the HTML, and
it costs one fetch per workspace instead of one per card render.

Only ever for a *single* URL. A workspace opening several sites has no one site
to stand for it, so it keeps the browser image's project logo — as does any
workspace whose favicon couldn't be fetched or decoded (an SVG-only favicon, for
instance: Pillow can't rasterize one, and nothing else here needs an SVG engine).
"""

import asyncio
import io
import ipaddress
import logging
import re
import socket
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

# Stored size. The UI draws this at 14-64px and the PWA manifest scales it up to
# 512, so a little headroom beyond the largest on-screen use is worth the bytes.
_ICON_PX = 256
_MAX_BYTES = 512 * 1024
_HTML_MAX_BYTES = 256 * 1024
_TIMEOUT = 6.0
# Try a handful of declared icons before giving up: the first is often an SVG we
# can't rasterize, and the next one down is usually a perfectly good PNG.
_MAX_CANDIDATES = 5
# Below this, a declared icon is worth trying the well-known apple-touch path
# ahead of. 120px is under the smallest conventional touch-icon size (144).
_WELL_KNOWN_MIN_PX = 120
# Says what it is rather than pretending to be a browser. The "Mozilla/5.0
# (compatible; ...)" wrapper is the long-standing convention for a well-behaved
# bot and gets past naive user-agent sniffing that rejects anything unfamiliar.
# Sites that additionally demand a contact URL (Wikimedia, notably) will refuse
# us, and that's the trade taken: Cove has no public URL to point at, and the
# private one this deployment runs on is not third parties' business. Those
# workspaces keep their browser logo.
_UA = "Mozilla/5.0 (compatible; CoveFaviconFetcher/1.0)"

_ICON_RELS = {"icon", "shortcut", "apple-touch-icon", "apple-touch-icon-precomposed"}


def site_origin(target_url: "str | None") -> "str | None":
    """The single origin a workspace represents, or None.

    None for no URL and — deliberately — for several: with more than one site
    open there is no site to speak for the workspace. Credentials in the URL are
    dropped, so the fetch below never replays a password found in the DB.
    """
    urls = (target_url or "").split()
    if len(urls) != 1:
        return None
    parsed = urlparse(urls[0])
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    origin = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        origin += f":{parsed.port}"
    return origin


async def _host_is_fetchable(host: str) -> bool:
    """Whether the server may make a request to this host.

    Everywhere else Cove fetches an image the URL comes from the admin-synced
    catalog; here it comes from whatever the workspace owner typed, which makes
    this the one place a user aims the *control plane* at an address of their
    choosing. Private LAN ranges stay allowed on purpose — self-hosted sites are
    exactly what people point these workspaces at — but loopback (Cove's own API,
    reachable without a session when it doesn't come through Traefik) and
    link-local (169.254.169.254 and friends) are not.

    A pre-flight check on the resolved addresses, not a security boundary: the
    connection resolves the name again, so a name that answers differently the
    second time can still slip past. It costs one DNS lookup and rules out the
    obvious aim, and the reply is never shown to the user — only an image that
    Pillow could decode is ever stored.
    """
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo, host, None, 0, socket.SOCK_STREAM
        )
    except (OSError, UnicodeError):
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
            or ip.is_reserved
        ):
            return False
    return True


_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.I)
_ATTR_RE = re.compile(r"""([A-Za-z_:][-\w:.]*)\s*=\s*("[^"]*"|'[^']*'|[^\s"'>]+)""")
_SIZE_RE = re.compile(r"^(\d+)x(\d+)$")


def _declared_px(attrs: dict) -> int:
    """The largest size a ``<link>`` claims to be, for ranking. 0 = unstated."""
    best = 0
    for token in attrs.get("sizes", "").lower().split():
        match = _SIZE_RE.match(token)
        if match:
            best = max(best, int(match.group(1)))
    if not best and "apple-touch" in attrs.get("rel", "").lower():
        best = 180  # the conventional apple-touch-icon size when none is declared
    return best


def icon_candidates(html: str, origin: str) -> "list[str]":
    """Icon URLs to try for a site, best first.

    Declared icons come first, largest first (we downscale, so bigger is safer
    than a 16px original blown up), then the well-known ``/favicon.ico`` — which
    plenty of sites serve without ever mentioning it in their markup.

    Regex rather than an HTML parser: this reads one element type out of a page
    we already treat as hostile input, and the answer is checked by actually
    fetching and decoding it. A parser would add a dependency to be no more sure.
    """
    ranked: "list[tuple[int, str]]" = []
    for tag in _LINK_TAG_RE.findall(html):
        attrs = {
            m.group(1).lower(): m.group(2).strip("\"'") for m in _ATTR_RE.finditer(tag)
        }
        rels = attrs.get("rel", "").lower().split()
        if not any(rel in _ICON_RELS for rel in rels):
            continue
        href = attrs.get("href", "").strip()
        # data: hrefs are already the image, but decoding them here would mean a
        # second parser for a rare case; skip and let the next candidate answer.
        if not href or href.lower().startswith("data:"):
            continue
        try:
            url = urljoin(origin + "/", href)
        except ValueError:
            continue
        if urlparse(url).scheme in ("http", "https"):
            ranked.append((_declared_px(attrs), url))

    ranked.sort(key=lambda pair: pair[0], reverse=True)

    out: "list[str]" = []
    for size, url in ranked:
        # Once the declared icons are down to postage stamps, try the well-known
        # apple-touch-icon path first: plenty of sites (GitHub among them) declare
        # only a 32px icon on a given page while serving a 180px one at the
        # conventional path, and the home-screen install is the difference between
        # a crisp mark and an upscaled blur.
        if size < _WELL_KNOWN_MIN_PX:
            _append(out, f"{origin}/apple-touch-icon.png")
        _append(out, url)
    if not ranked:
        _append(out, f"{origin}/apple-touch-icon.png")
    _append(out, f"{origin}/favicon.ico")
    return out[:_MAX_CANDIDATES]


def _append(urls: "list[str]", url: str) -> None:
    if url not in urls:
        urls.append(url)


async def _read_capped(response: httpx.Response, cap: int) -> bytes:
    """Read a streamed body up to ``cap`` bytes, then stop pulling.

    Streamed rather than ``response.content`` so a site that answers a favicon
    request with a gigabyte can't be read into memory before the size is checked.
    """
    chunks: "list[bytes]" = []
    total = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        total += len(chunk)
        if total > cap:
            break
    return b"".join(chunks)[: cap + 1]


async def _page_html(client: httpx.AsyncClient, origin: str) -> str:
    """The site's landing page, or "" if it isn't reachable/isn't HTML."""
    try:
        async with client.stream("GET", origin, headers={"Accept": "text/html"}) as resp:
            ctype = (resp.headers.get("content-type") or "").split(";")[0].strip()
            if resp.status_code != 200 or not ctype.startswith(("text/html", "application/xhtml")):
                return ""
            raw = await _read_capped(resp, _HTML_MAX_BYTES)
    except httpx.HTTPError:
        return ""
    return raw.decode("utf-8", errors="replace")


async def _fetch_icon(client: httpx.AsyncClient, url: str) -> "bytes | None":
    """Fetch one icon candidate. Content type is a hint only — plenty of servers
    label ``favicon.ico`` as octet-stream — so the real gate is whether Pillow can
    decode what came back."""
    try:
        async with client.stream("GET", url, headers={"Accept": "image/*"}) as resp:
            if resp.status_code != 200:
                return None
            data = await _read_capped(resp, _MAX_BYTES)
    except httpx.HTTPError:
        return None
    if not data or len(data) > _MAX_BYTES:
        return None
    return data


def to_png(data: bytes) -> "bytes | None":
    """Normalize icon bytes to a square-ish RGBA PNG, or None if undecodable.

    Sites serve favicons as ICO, PNG, GIF, JPEG and SVG; storing one format means
    the UI and the manifest don't each need to cope with the rest. Returns None
    (never raises) for anything Pillow can't open — SVG, mostly — leaving the
    workspace on its browser logo.
    """
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow ships in the image
        logger.warning("Pillow is not installed — site favicons are unavailable.")
        return None

    try:
        img = Image.open(io.BytesIO(data))
        # Pillow's ICO reader exposes the largest frame in the file as the opened
        # image, which is the one worth keeping — .ico files routinely carry 16px
        # through 256px versions of the same mark.
        img = img.convert("RGBA")
        if img.width < 8 or img.height < 8:
            return None  # a tracking pixel or a stub, not an icon
        if max(img.size) > _ICON_PX:
            img.thumbnail((_ICON_PX, _ICON_PX), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "PNG", optimize=True)
        return out.getvalue()
    except Exception as exc:
        logger.debug("Could not decode favicon (%d bytes): %s", len(data), exc)
        return None


async def fetch_favicon(origin: str) -> "bytes | None":
    """Best-effort: the site's favicon as a PNG, or None. Never raises."""
    host = urlparse(origin).hostname
    if not host or not await _host_is_fetchable(host):
        return None
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": _UA}
        ) as client:
            html = await _page_html(client, origin)
            for url in icon_candidates(html, origin):
                data = await _fetch_icon(client, url)
                png = to_png(data) if data else None
                if png:
                    return png
    except Exception as exc:  # a fetch must never take down its caller
        logger.debug("Favicon lookup failed for %s: %s", origin, exc)
    return None


async def refresh_workspace_favicon(ws_id: int) -> None:
    """Background task: bring a workspace's stored favicon in line with its URL.

    Runs after launch/edit rather than on the request path — the fetch talks to a
    third-party site and can be slow or hang, and nothing in the UI needs to wait
    for it (the card shows the browser logo until the icon lands). Best-effort
    throughout: a failure leaves the workspace on its browser logo and is logged,
    never raised.

    The DB session is opened around each step and closed for the fetch itself, so
    a slow site doesn't sit on a connection from the pool while it answers.
    """
    from server.db import SessionLocal
    from server.models import Workspace

    def _load() -> "tuple[str | None, str | None, bool] | None":
        db = SessionLocal()
        try:
            ws = db.get(Workspace, ws_id)
            if ws is None:
                return None
            wanted = (
                site_origin(ws.target_url)
                if ws.workspace_type in ("browser", "link")
                else None
            )
            return wanted, ws.favicon_origin, ws.favicon_png is not None
        finally:
            db.close()

    def _store(origin: "str | None", png: "bytes | None") -> None:
        db = SessionLocal()
        try:
            ws = db.get(Workspace, ws_id)
            if ws is None:
                return
            ws.favicon_png = png
            ws.favicon_origin = origin if png else None
            ws.favicon_at = datetime.now(timezone.utc) if png else None
            db.commit()
        finally:
            db.close()

    try:
        loaded = await asyncio.to_thread(_load)
        if loaded is None:
            return
        wanted, stored_origin, has_png = loaded

        if wanted is None:
            # No single site any more — several URLs, a cleared URL, or a rebuild
            # onto a non-browser image. Drop the icon so the card stops showing a
            # site the workspace no longer opens.
            if has_png or stored_origin:
                await asyncio.to_thread(_store, None, None)
            return
        if wanted == stored_origin and has_png:
            return  # already holding this site's mark

        png = await fetch_favicon(wanted)
        await asyncio.to_thread(_store, wanted, png)
        if png is None:
            logger.info("No usable favicon for %s (workspace %s)", wanted, ws_id)
    except Exception as exc:
        logger.warning("Favicon refresh failed for workspace %s: %s", ws_id, exc)
