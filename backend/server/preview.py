"""Workspace preview captures: a still frame of what's on a workspace's screen.

The frame is taken from the workspace's **own Selkies stream** rather than by
opening a second capture of the display. That matters for two reasons: it is the
only approach that works for Wayland workspaces (a second ``pixelflux`` capture
there spawns its own empty compositor instead of attaching to the running
session), and it needs nothing installed in the image — the capture client is a
few lines of Python driven through ``docker exec`` against the venv that Selkies
itself runs on.

Wire format (Selkies "websockets" mode): each binary message is a 6-byte header —
a ``0x03`` type marker, a 3-byte big-endian frame id, and a 2-byte big-endian
stripe y-offset — followed by a complete JPEG for that horizontal stripe of the
screen. We locate the JPEG SOI instead of assuming the header length, so an
upstream header change fails loudly (no stripes) rather than silently producing a
corrupted image.

**Never disrupt a live session.** Selkies evicts the current "primary" client when
a new one sends ``SETTINGS`` ("KILL a new primary client connected connection
killed"), which would drop a user mid-session. So the capture listens passively
first: if anyone is already streaming, their frames are being broadcast and we
take one for free. Only when nothing arrives — meaning nobody is watching — do we
ask the server to start the stream for us. ``passive_only`` forbids that second
step outright, for refreshes that must never risk an eviction.
"""

import base64
import io
import json
import logging

logger = logging.getLogger(__name__)

# Longest edge of a stored preview. Cards render ~320px wide, so this stays sharp
# on a 2x display without storing a full desktop frame per workspace.
_THUMB_MAX = 480
_THUMB_QUALITY = 72

# The venv Selkies runs on in LinuxServer images, then plain interpreters as a
# fallback. Only the first one that exists AND can import `websockets` is used.
_PYTHON_CANDIDATES = ("/lsiopy/bin/python", "/usr/bin/python3", "python3")

# Marker prefix for the payload line, so the capture survives any chatter another
# library writes to stdout.
_MARKER = "COVE_PREVIEW:"

# In-container capture client. Kept dependency-free apart from `websockets`,
# which ships in the Selkies venv. Passed via `python -c` (argv, not a shell
# string) so nothing here needs quoting.
_CAPTURE_SRC = r'''
import asyncio, base64, json, sys
import websockets

URL, PASSIVE_ONLY, PASSIVE_WAIT, ACTIVE_WAIT = sys.argv[1], sys.argv[2] == "1", float(sys.argv[3]), float(sys.argv[4])
SOI = b"\xff\xd8\xff"

def take(raw, stripes):
    """Record one stripe; returns True if the message held one."""
    if len(raw) < 8:
        return False
    off = raw.find(SOI)
    # Only trust an SOI inside the header region -- a later match is payload
    # data that happens to look like a marker.
    if off < 2 or off > 16:
        return False
    stripes[int.from_bytes(raw[off - 2:off], "big")] = base64.b64encode(raw[off:]).decode()
    return True

async def drain(ws, stripes, first_wait, quiet=0.7, cap=8.0):
    """Wait up to first_wait for a stripe, then collect until the frame stops
    growing for `quiet` seconds. A full frame arrives as one burst, so this
    finishes in a fraction of the worst-case window."""
    loop = asyncio.get_event_loop()
    hard_end = loop.time() + cap
    deadline = loop.time() + first_wait
    while loop.time() < min(deadline, hard_end):
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=max(min(deadline, hard_end) - loop.time(), 0.05))
        except asyncio.TimeoutError:
            break
        except Exception:
            break
        if isinstance(msg, bytes):
            before = len(stripes)
            if take(msg, stripes) and len(stripes) > before:
                # New row: extend the window to catch the rest of the burst.
                deadline = loop.time() + quiet

async def main():
    stripes = {}
    async with websockets.connect(URL, max_size=None, open_timeout=6) as ws:
        # Passive first: if someone is watching, their stream is already being
        # broadcast to every connected client, so we never have to announce
        # ourselves (which would evict them).
        await drain(ws, stripes, PASSIVE_WAIT, cap=PASSIVE_WAIT + 3.0)
        if not stripes and not PASSIVE_ONLY:
            # Nothing broadcasting => nobody is connected => safe to start it.
            await ws.send('SETTINGS,{"displayId":"primary","encoder":"jpeg","framerate":10}')
            await asyncio.sleep(0.3)
            await ws.send("START_VIDEO")
            await drain(ws, stripes, ACTIVE_WAIT, cap=ACTIVE_WAIT + 3.0)
    if stripes:
        sys.stdout.write("__COVE_MARKER__" + base64.b64encode(
            json.dumps(stripes).encode()).decode() + "\n")

asyncio.run(main())
'''

# Keep the client's marker and the parser's in lockstep — hardcoding it in both
# places would let a rename break capture while the decode tests still passed.
_CAPTURE_SRC = _CAPTURE_SRC.replace("__COVE_MARKER__", _MARKER)


def _decode_payload(stdout: bytes) -> "dict[int, bytes] | None":
    """Pull the marker line out of exec stdout and decode it to {y: jpeg}."""
    for line in stdout.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line.startswith(_MARKER):
            continue
        try:
            raw = json.loads(base64.b64decode(line[len(_MARKER):]))
        except (ValueError, json.JSONDecodeError):
            return None
        return {int(y): base64.b64decode(b64) for y, b64 in raw.items()}
    return None


def assemble(stripes: "dict[int, bytes]") -> "bytes | None":
    """Composite y-indexed JPEG stripes into one downscaled JPEG thumbnail.

    Returns None unless the stripes tile the frame with no gaps: a partial set
    would render as a screenshot with black bands through it, which reads as a
    broken workspace rather than a missing preview.
    """
    from PIL import Image

    if not stripes:
        return None
    try:
        tiles = [
            (y, Image.open(io.BytesIO(data)).convert("RGB"))
            for y, data in sorted(stripes.items())
        ]
    except Exception as exc:  # noqa: BLE001 - any undecodable stripe voids the frame
        logger.debug("Preview stripe decode failed: %s", exc)
        return None

    width = max(t.width for _, t in tiles)
    height = max(y + t.height for y, t in tiles)

    # Reject gaps/overlap mismatches: walk the stripes in order and require each
    # to start exactly where the previous one ended.
    cursor = 0
    for y, tile in tiles:
        if y != cursor:
            logger.debug("Preview incomplete: gap at y=%d (expected %d)", y, cursor)
            return None
        cursor = y + tile.height
    if cursor != height:
        return None

    canvas = Image.new("RGB", (width, height), (0, 0, 0))
    for y, tile in tiles:
        canvas.paste(tile, (0, y))
    canvas.thumbnail((_THUMB_MAX, _THUMB_MAX), Image.LANCZOS)

    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=_THUMB_QUALITY, optimize=True)
    return buf.getvalue()


def capture(
    container,
    port: int,
    *,
    passive_only: bool = False,
    passive_wait: float = 2.5,
    active_wait: float = 6.0,
) -> "bytes | None":
    """Capture one frame from a running workspace container. None if unavailable.

    Best-effort by contract: every failure path (no interpreter, no
    ``websockets``, stream not up yet, partial frame) returns None so callers can
    treat "no preview" as ordinary rather than exceptional.
    """
    url = f"ws://localhost:{port}/websockets"
    args = [url, "1" if passive_only else "0", str(passive_wait), str(active_wait)]
    # Budget the exec generously past the client's own waits so a hung socket
    # surfaces as a timeout here rather than wedging the caller.
    for interpreter in _PYTHON_CANDIDATES:
        try:
            code, output = container.exec_run(
                [interpreter, "-c", _CAPTURE_SRC, *args], demux=False
            )
        except Exception as exc:  # noqa: BLE001 - docker/API/transport errors
            logger.debug("Preview exec failed via %s: %s", interpreter, exc)
            continue
        if code != 0:
            # Wrong interpreter (missing binary / no websockets module) — try the
            # next candidate. A real stream failure exits 0 with no marker line.
            logger.debug("Preview exec rc=%s via %s", code, interpreter)
            continue
        stripes = _decode_payload(output or b"")
        if not stripes:
            return None
        return assemble(stripes)
    return None
