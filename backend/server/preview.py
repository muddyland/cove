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

**The endpoint moved.** Selkies builds from around September 2026 serve the stream
socket at ``/api/websockets`` and 404 the old ``/websockets``; older builds do the
reverse. Both are tried in turn, newest first, so one Cove release spans images on
either side of the change.

**Never disrupt a live session.** Selkies evicts the current "primary" client when
a new one sends ``SETTINGS`` ("KILL a new primary client connected connection
killed"), which would drop a user mid-session. So the capture listens passively
first: if anyone is already streaming, their frames are being broadcast and we
take one for free. Only when nothing arrives — meaning nobody is watching — do we
ask the server to start the stream for us. ``passive_only`` forbids that second
step outright, for refreshes that must never risk an eviction.

Current builds make the passive step safer rather than unnecessary: a
``?role=viewer`` connection never becomes primary (measured against a live
session, the watcher kept streaming), but it also cannot *start* a stream, so it
only ever returns a frame someone else's session is already producing. Starting
one still means connecting as primary, with the eviction risk that implies.
"""

import base64
import io
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Longest edge of a stored preview. Cards render ~320px wide, so this stays sharp
# on a 2x display without storing a full desktop frame per workspace.
# A running workspace is held back from connecting until its stream has produced
# its first frame, since opening the stream before Selkies is rendering leaves a
# client that only a reload recovers. Past this long after going running it opens
# anyway, so an image whose stream Cove can't capture still becomes usable.
CONNECT_FALLBACK_SECONDS = 90

_THUMB_MAX = 480
_THUMB_QUALITY = 72

# The venv Selkies runs on in LinuxServer images, then plain interpreters as a
# fallback. Only the first one that exists AND can import `websockets` is used.
_PYTHON_CANDIDATES = ("/lsiopy/bin/python", "/usr/bin/python3", "python3")

# Marker prefix for the payload line, so the capture survives any chatter another
# library writes to stdout.
_MARKER = "COVE_PREVIEW:"

# Marker the client writes to stderr when it could not reach a stream at all, so
# a moved endpoint reads as one warning line instead of a silent empty preview.
_ERR_MARKER = "COVE_PREVIEW_ERR"

# Stream socket. Current Selkies builds serve ``/api/websockets`` and 404 the old
# ``/websockets``; older builds do the reverse, so both spellings are tried.
#
# ``?role=viewer`` (current builds only) joins without becoming the primary
# client: measured against a live session, the watcher kept streaming, where a
# plain connection made the server send "KILL a new primary client connected".
# A viewer only receives what is already being broadcast, though — it cannot
# start a stream — so it is a free first attempt, not a replacement for one.
_WS_VIEWER_PATH = "/api/websockets?role=viewer"
_WS_PRIMARY_PATH = "/api/websockets"
_WS_LEGACY_PATH = "/websockets"

# In-container capture client. Kept dependency-free apart from `websockets`,
# which ships in the Selkies venv. Passed via `python -c` (argv, not a shell
# string) so nothing here needs quoting.
_CAPTURE_SRC = r'''
import asyncio, base64, json, sys
import websockets

# [[url, may_start_the_stream], ...] tried in order, plus the two drain windows.
TARGETS, PASSIVE_WAIT, ACTIVE_WAIT = json.loads(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
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
    errors = []
    connected = False
    for url, may_start in TARGETS:
        try:
            async with websockets.connect(url, max_size=None, open_timeout=6) as ws:
                connected = True
                # Passive first: if someone is watching, their stream is already
                # being broadcast to every connected client, so we can take a
                # frame without announcing ourselves at all.
                await drain(ws, stripes, PASSIVE_WAIT, cap=PASSIVE_WAIT + 3.0)
                if not stripes and may_start:
                    # Nothing is being broadcast, so nobody is watching and it is
                    # safe to become the primary client and ask for a stream.
                    await ws.send('SETTINGS,{"displayId":"primary","encoder":"jpeg","framerate":10}')
                    await asyncio.sleep(0.3)
                    await ws.send("START_VIDEO")
                    await drain(ws, stripes, ACTIVE_WAIT, cap=ACTIVE_WAIT + 3.0)
        except Exception as exc:
            # Usually this endpoint does not exist on this build; the next
            # candidate is the other spelling. Reported only if none work.
            errors.append("%s -> %s: %s" % (url, type(exc).__name__, exc))
            continue
        if stripes:
            break
    if stripes:
        sys.stdout.write("__COVE_MARKER__" + base64.b64encode(
            json.dumps(stripes).encode()).decode() + "\n")
    elif errors and not connected:
        # Nothing answered anywhere: the endpoint has moved out from under us.
        # Exit 0 regardless -- no frame is an ordinary outcome for the caller --
        # but it must not be a silent one. A candidate that simply doesn't exist
        # on this build is not reported, since another one did answer.
        sys.stderr.write("__COVE_ERR_MARKER__ " + " | ".join(errors) + "\n")

asyncio.run(main())
'''

# Keep the client's marker and the parser's in lockstep — hardcoding it in both
# places would let a rename break capture while the decode tests still passed.
_CAPTURE_SRC = _CAPTURE_SRC.replace("__COVE_MARKER__", _MARKER)
_CAPTURE_SRC = _CAPTURE_SRC.replace("__COVE_ERR_MARKER__", _ERR_MARKER)


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

    if not stripes or len(stripes) > _MAX_STRIPES:
        return None
    try:
        tiles = []
        for y, data in sorted(stripes.items()):
            img = Image.open(io.BytesIO(data))
            # Check the declared size BEFORE decoding pixels: a few hundred KB
            # of JPEG can claim a 13000x13000 canvas and cost gigabytes.
            if img.width > _MAX_STRIPE_PX[0] or img.height > _MAX_STRIPE_PX[1]:
                logger.debug("Preview stripe %dx%d too large; frame dropped", img.width, img.height)
                return None
            tiles.append((y, img.convert("RGB")))
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


# Everything the capture prints comes from a process inside the USER'S container;
# never read an unbounded amount of it into the control plane's memory. A full
# 4K frame of JPEG stripes base64-encoded is well under this.
_MAX_EXEC_BYTES = 8 * 1024 * 1024
# Largest stripe the assembler will decode (a screen, not a decompression bomb).
_MAX_STRIPE_PX = (8192, 8192)
_MAX_STRIPES = 256


def _exec_capped(container, cmd: list) -> "tuple[int | None, bytes | None]":
    """``exec_run`` that stops reading once the output exceeds _MAX_EXEC_BYTES
    (returning ``output=None``), and still reports the exit code."""
    api = container.client.api
    exec_id = api.exec_create(container.id, cmd, stdout=True, stderr=True)["Id"]
    chunks: list[bytes] = []
    total = 0
    overflow = False
    for chunk in api.exec_start(exec_id, stream=True):
        total += len(chunk)
        if total > _MAX_EXEC_BYTES:
            overflow = True
            break
        chunks.append(chunk)
    code = api.exec_inspect(exec_id).get("ExitCode")
    return code, (None if overflow else b"".join(chunks))


def _failure_note(output: "bytes | None") -> str:
    """The client's own explanation of why it reached no stream, as a log-safe
    suffix, or "" when it didn't leave one.

    The text comes from a process in the user's container, so it is truncated and
    flattened to a single line before it goes anywhere near a log.
    """
    for line in (output or b"").decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line.startswith(_ERR_MARKER):
            return ": " + " ".join(line[len(_ERR_MARKER):].split())[:300]
    return ""


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
    ``websockets``, no reachable endpoint, stream not up yet, partial frame)
    returns None so callers can treat "no preview" as ordinary rather than
    exceptional. Failures that are *not* ordinary — the client running and dying,
    or no endpoint answering at all — are logged at warning, since those mean
    every preview in the deployment is silently missing.

    ``passive_only`` means "never risk evicting whoever is watching". On builds
    with the viewer role that costs nothing, because a viewer cannot supersede
    the primary client; on older ones it forbids starting a stream at all.
    """
    # Tried in order. The viewer joins without becoming primary, so it can take a
    # frame from a session someone is watching without evicting them — but it
    # only receives what is already being broadcast; it cannot start a stream, so
    # it is always the passive attempt. Starting one means connecting as primary,
    # which is what ``passive_only`` forbids.
    targets = [[f"ws://localhost:{port}{_WS_VIEWER_PATH}", False]]
    if not passive_only:
        targets.append([f"ws://localhost:{port}{_WS_PRIMARY_PATH}", True])
    targets.append([f"ws://localhost:{port}{_WS_LEGACY_PATH}", not passive_only])
    args = [json.dumps(targets), str(passive_wait), str(active_wait)]
    # Budget the exec generously past the client's own waits so a hung socket
    # surfaces as a timeout here rather than wedging the caller.
    for interpreter in _PYTHON_CANDIDATES:
        try:
            code, output = _exec_capped(container, [interpreter, "-c", _CAPTURE_SRC, *args])
        except Exception as exc:  # noqa: BLE001 - docker/API/transport errors
            logger.debug("Preview exec failed via %s: %s", interpreter, exc)
            continue
        if output is None:
            logger.warning("Preview output from %s exceeded %d bytes; ignored", container.name, _MAX_EXEC_BYTES)
            return None
        if code != 0:
            # 126/127 is the interpreter itself being missing or unusable (no
            # binary, no `websockets`), which is why there is a candidate list —
            # try the next one quietly.
            if code in (126, 127):
                logger.debug("Preview interpreter %s unusable in %s (rc=%s)", interpreter, container.name, code)
                continue
            # Anything else means the client ran and died. That used to be
            # indistinguishable from a missing interpreter, which is how a moved
            # stream endpoint went unnoticed through every log level above DEBUG.
            logger.warning(
                "Preview capture failed in %s via %s (rc=%s)%s",
                container.name, interpreter, code, _failure_note(output),
            )
            continue
        stripes = _decode_payload(output or b"")
        if not stripes:
            # Reached an interpreter but got no frame. Couldn't reach a stream at
            # all => say so; otherwise the stream simply isn't rendering yet,
            # which is ordinary during a launch.
            note = _failure_note(output)
            if note:
                logger.warning("Preview capture: no stream reachable in %s%s", container.name, note)
            return None
        return assemble(stripes)
    return None


def is_connectable(ws, now: "datetime | None" = None) -> bool:
    """Whether a workspace's stream is ready for a client: running, and either
    its first frame has been captured or CONNECT_FALLBACK_SECONDS have passed."""
    if ws.status != "running":
        return False
    if ws.preview_at is not None or ws.started_at is None:
        return True
    started = ws.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - started).total_seconds() >= CONNECT_FALLBACK_SECONDS
