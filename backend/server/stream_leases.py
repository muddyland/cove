"""Which open stream page currently holds each workspace.

Selkies lets one client control a stream: when a second browser (or a thin
client) connects, it closes the first one's socket, and that page is left
showing a dead stream with no explanation. The stream itself is opaque to the
SPA (cross-origin in subdomain mode), so the SPA tracks it here instead: each
stream page has a random viewer id, claims the workspace when its stream loads,
and polls to learn whether it still holds it. A page that lost it shows where
the workspace went and offers to take it back.

In memory on purpose: Cove runs one worker, and losing this on a restart only
means the next poll re-claims (see ``check``). Nothing here grants access; the
router checks workspace ownership first.
"""

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# A holder that stopped polling (tab closed, machine asleep) stops counting after
# this long. Generous: browsers throttle timers in background tabs to ~1/min.
LEASE_TTL = 150.0


@dataclass
class Lease:
    viewer_id: str
    label: str
    since: datetime
    last_seen: float
    # The thin client (public id) the page runs on, if any.
    device: Optional[str] = None


_leases: dict[int, Lease] = {}
_lock = threading.Lock()


def _now() -> float:
    return time.monotonic()


def claim(ws_id: int, viewer_id: str, label: str, device: Optional[str] = None) -> Lease:
    """This page's stream just loaded: it is the holder now (Selkies has already
    disconnected whoever had it before)."""
    with _lock:
        current = _leases.get(ws_id)
        if current is not None and current.viewer_id == viewer_id:
            current.last_seen = _now()
            current.label = label
            current.device = device
            return current
        lease = Lease(viewer_id, label, datetime.now(timezone.utc), _now(), device)
        _leases[ws_id] = lease
        return lease


def check(ws_id: int, viewer_id: str, label: str, device: Optional[str] = None) -> tuple[bool, Optional[Lease]]:
    """``(still_holder, current_lease)``. A missing or expired lease is
    re-claimed by the asking page, so a Cove restart is invisible to users."""
    with _lock:
        current = _leases.get(ws_id)
        if current is None or _now() - current.last_seen > LEASE_TTL:
            current = Lease(viewer_id, label, datetime.now(timezone.utc), _now(), device)
            _leases[ws_id] = current
            return True, current
        if current.viewer_id == viewer_id:
            current.last_seen = _now()
            return True, current
        return False, current


def release(ws_id: int, viewer_id: str) -> None:
    """The holder closed its page: forget it (only if it still holds it)."""
    with _lock:
        current = _leases.get(ws_id)
        if current is not None and current.viewer_id == viewer_id:
            del _leases[ws_id]


def forget(ws_id: int) -> None:
    """The workspace stopped or was removed."""
    with _lock:
        _leases.pop(ws_id, None)


def by_device() -> dict[str, tuple[int, datetime]]:
    """Thin client public id -> ``(workspace id, since)`` for every live lease
    held on a thin client: what each device is showing right now."""
    with _lock:
        now = _now()
        return {
            lease.device: (ws_id, lease.since)
            for ws_id, lease in _leases.items()
            if lease.device and now - lease.last_seen <= LEASE_TTL
        }


def clear() -> None:
    """Tests only."""
    with _lock:
        _leases.clear()


_BROWSERS = (("Edg/", "Edge"), ("OPR/", "Opera"), ("Vivaldi", "Vivaldi"), ("Firefox/", "Firefox"),
             ("Chrome/", "Chrome"), ("Chromium/", "Chromium"), ("Safari/", "Safari"))
_SYSTEMS = (("Android", "Android"), ("iPhone", "iOS"), ("iPad", "iPadOS"), ("CrOS", "ChromeOS"),
            ("Windows", "Windows"), ("Mac OS X", "macOS"), ("Linux", "Linux"))


def describe_browser(user_agent: str) -> str:
    """'Firefox on Linux' — enough to tell your own screens apart."""
    ua = user_agent or ""
    browser = next((name for key, name in _BROWSERS if key in ua), "a browser")
    system = next((name for key, name in _SYSTEMS if key in ua), None)
    return f"{browser} on {system}" if system else browser
