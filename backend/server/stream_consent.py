"""An admin's explicit consent to open another user's workspace in subpath mode.

In subpath mode a workspace's desktop is served on the app's own origin, so
whatever runs in it can script against Cove with the viewer's session. For an
admin opening someone else's workspace that is the admin's session, so
ForwardAuth only lets a non-owner through after they confirmed in the SPA
(``POST /api/workspaces/{id}/stream-auth?accept_foreign=true``), which records a
grant here. A link alone (a top-level navigation, or an iframe the SPA never
set up) gets nothing.

The grant is bound to the workspace's current container, so it ends when the
workspace is stopped or relaunched, and it lapses after a quiet period. It is
process-local: Cove runs one uvicorn process, and a restart just asks again.
"""

import threading
import time

# Kept alive by every stream request (ForwardAuth runs on each one, WebSockets
# included); lapses this long after the last.
IDLE_SECONDS = 2 * 3600
_MAX_GRANTS = 4096

_lock = threading.Lock()
# (user id, workspace public id) -> (container id, expiry on the monotonic clock)
_grants: dict[tuple[int, str], tuple[str | None, float]] = {}


def grant(user_id: int, public_id: str, container_id: str | None) -> None:
    now = time.monotonic()
    with _lock:
        if len(_grants) >= _MAX_GRANTS:
            for key in [k for k, (_, exp) in _grants.items() if exp <= now]:
                del _grants[key]
        _grants[(user_id, public_id)] = (container_id, now + IDLE_SECONDS)


def allowed(user_id: int, public_id: str, container_id: str | None) -> bool:
    """True (and the grant is extended) if this user consented for this container."""
    now = time.monotonic()
    key = (user_id, public_id)
    with _lock:
        entry = _grants.get(key)
        if entry is None:
            return False
        granted_for, expires = entry
        if expires <= now or granted_for != container_id:
            del _grants[key]
            return False
        _grants[key] = (granted_for, now + IDLE_SECONDS)
        return True


def clear() -> None:
    with _lock:
        _grants.clear()
