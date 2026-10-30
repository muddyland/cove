"""Home automation integration (Home Assistant): tokens and the status snapshot.

An admin mints an *integration token* in Admin → Integrations. It is a random
bearer secret stored only as a sha256, and it works on ``/api/integration/v1``
and nowhere else: it is not a JWT, so the session dependencies reject it, and
the integration endpoints accept nothing but it. Two scopes:

* ``read`` — the snapshot below.
* ``control`` — also thin client screenshots (what's on someone's screen),
  commands, sign-out and keep-awake.

A token acts with the authority of the admin who minted it, so it is revoked
when that account is deleted or stops being an admin.

Home Assistant polls the snapshot over Cove's one HTTPS entrypoint, so nothing
new is exposed: no inbound port, no webhook into Home Assistant.
"""

import hashlib
import io
import json
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server import settings_store, stream_leases
from server import thin_clients as tc
from server.models import IntegrationToken, ThinClient, User, Workspace, Zone

TOKEN_PREFIX = "cit_"
SCOPES = ("read", "control")
# Commands Home Assistant may queue. Not ``logs`` (its output is for admins) or
# ``apt_upgrade`` (maintenance belongs to the device's own update window).
CONTROL_COMMANDS = (
    "screen_on",
    "screen_off",
    "restart_browser",
    "identify",
    "screenshot",
    "reboot",
    "poweroff",
    "set_volume",
    "set_mute",
)
# Commands that interrupt whoever is at the device, or capture their screen,
# so they're audited.
AUDITED_COMMANDS = ("restart_browser", "reboot", "poweroff", "screenshot")
# last_used_at is written at most this often, not on every poll.
_TOUCH_EVERY = timedelta(minutes=1)
# Docker stats take ~1 s per container; reuse a sample for this long so several
# pollers (or a fast scan interval) don't each pay for it.
_STATS_TTL = 20.0
_INSTANCE_KEY = "integration_instance_id"


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


def find_token(db: Session, token: str) -> Optional[IntegrationToken]:
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    return db.scalar(select(IntegrationToken).where(IntegrationToken.token_hash == hash_token(token)))


def revoke_tokens_of(db: Session, username: str) -> list[str]:
    """Delete every token minted by ``username``; returns their names."""
    rows = db.scalars(select(IntegrationToken).where(IntegrationToken.created_by == username)).all()
    names = [r.name for r in rows]
    for r in rows:
        db.delete(r)
    if rows:
        db.commit()
    return names


def rename_creator(db: Session, old: str, new: str) -> None:
    """Keep tokens tied to their creator across a username change."""
    for r in db.scalars(select(IntegrationToken).where(IntegrationToken.created_by == old)).all():
        r.created_by = new


def touch(db: Session, row: IntegrationToken, ip: Optional[str]) -> None:
    now = tc.now_utc()
    last = tc._utc(row.last_used_at)
    if last is None or now - last >= _TOUCH_EVERY or row.last_used_ip != ip:
        row.last_used_at = now
        row.last_used_ip = ip
        db.commit()


def instance_id(db: Session) -> str:
    """A stable random id for this Cove, so Home Assistant can tell two Coves
    apart and recognise the same one under a new URL."""
    value = settings_store.get_setting(db, _INSTANCE_KEY)
    if not value:
        value = uuid4().hex
        settings_store.set_setting(db, _INSTANCE_KEY, value)
    return value


# ── Home Assistant component download ────────────────────────────────────────

_HA_PATHS = (
    Path("/app/integrations/home-assistant/custom_components/cove"),
    Path(__file__).resolve().parents[2] / "integrations" / "home-assistant" / "custom_components" / "cove",
)


def home_assistant_bundle() -> Optional[tuple[bytes, str]]:
    """``(zip, version)`` of the Home Assistant custom component, laid out as
    ``custom_components/cove/…`` to unzip into Home Assistant's config folder.
    Built from files in the image; byte-for-byte stable for the same files."""
    import zipfile

    root = next((p for p in _HA_PATHS if (p / "manifest.json").is_file()), None)
    if root is None:
        return None
    version = json.loads((root / "manifest.json").read_text()).get("version", "unknown")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                name = f"custom_components/cove/{path.relative_to(root).as_posix()}"
                info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, path.read_bytes())
    return buf.getvalue(), version


# ── Workspace stats ──────────────────────────────────────────────────────────

_stats_lock = threading.Lock()
# (taken at, running workspace ids, stats by workspace id)
_stats_cache: tuple[float, frozenset[int], dict[int, dict]] = (0.0, frozenset(), {})


def _workspace_stats(running: list[Workspace]) -> dict[int, dict]:
    """Workspace id -> ``{cpu_pct, mem_used, ...}`` for running containers. The
    lock is held while sampling, so concurrent pollers share one sample."""
    global _stats_cache
    key = frozenset(ws.id for ws in running)
    with _stats_lock:
        taken, _, cached = _stats_cache
        # Reuse a fresh sample even when the set of running workspaces changed
        # since: otherwise every start/stop (or a poller timing its requests
        # around them) forces a full re-sample of every container. A workspace
        # that started since just has no stats until the next sample.
        if time.monotonic() - taken < _STATS_TTL:
            return {ws_id: data for ws_id, data in cached.items() if ws_id in key}
        from server.docker_manager import get_docker_manager

        out: dict[int, dict] = {}
        if running:
            with ThreadPoolExecutor(max_workers=min(8, len(running))) as ex:
                futs = {ws.id: ex.submit(get_docker_manager(ws.zone_id).get_stats, ws.container_id) for ws in running}
            for ws_id, fut in futs.items():
                try:
                    data = fut.result()
                except Exception:  # noqa: BLE001 - an unreachable zone just has no stats
                    data = None
                if data:
                    out[ws_id] = data
        _stats_cache = (time.monotonic(), key, out)
        return out


def reset_stats_cache() -> None:
    """Tests only."""
    global _stats_cache
    with _stats_lock:
        _stats_cache = (0.0, frozenset(), {})


# ── Snapshot ─────────────────────────────────────────────────────────────────

def _iso(dt: Optional[datetime]) -> Optional[str]:
    dt = tc._utc(dt)
    return dt.isoformat() if dt else None


def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _bool(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _str(value: Any, limit: int = 256) -> Optional[str]:
    return value[:limit] if isinstance(value, str) and value else None


def thin_client_out(db: Session, client: ThinClient, showing: dict, ws_names: dict[int, tuple[str, str]]) -> dict:
    """One device for the snapshot. The reported state is untrusted, so each
    field is type-checked rather than passed through."""
    state = tc.load_state(client)
    online = tc.is_online(client)
    keep = tc._utc(client.keep_awake_until)
    lease = showing.get(client.public_id) if online else None
    ws = ws_names.get(lease[0]) if lease else None
    uptime = _int(state.get("uptime_s"))
    seen = tc._utc(client.last_seen_at)
    booted = seen - timedelta(seconds=uptime) if (online and seen and uptime is not None) else None
    return {
        "id": client.public_id,
        "name": client.name,
        "location": client.location,
        "status": client.status,
        "online": online,
        "last_seen_at": _iso(client.last_seen_at),
        "session_username": tc.active_session_username(client),
        "session_started_at": _iso(client.session_started_at) if tc.active_session_username(client) else None,
        "workspace_name": ws[0] if ws else None,
        "workspace_owner": ws[1] if ws else None,
        "workspace_since": _iso(lease[1]) if ws else None,
        "keep_awake_until": _iso(keep) if keep and keep > tc.now_utc() else None,
        "screenshot_at": _iso(client.screenshot_at),
        "pending_commands": tc.pending_count(db, client),
        # As reported by the device (null when it never said, or is offline).
        "display_on": _bool(state.get("display_on")) if online else None,
        "idle_seconds": _int(state.get("idle_s")) if online else None,
        "media_playing": _bool(state.get("media_playing")) if online else None,
        "volume": _int(state.get("volume")) if online else None,
        "muted": _bool(state.get("muted")) if online else None,
        "audio_output": _str(state.get("audio_output")) if online else None,
        "browser_running": _bool(state.get("browser_running")) if online else None,
        "booted_at": _iso(booted),
        "updates_available": _int(state.get("upgradable")),
        "reboot_required": _bool(state.get("reboot_required")),
        "last_update_at": _str(state.get("last_update_at"), 64),
        "last_error": _str(state.get("last_error"), 512),
        "hostname": _str(state.get("hostname"), 255),
        "ips": [ip for ip in state.get("ips") or [] if isinstance(ip, str)][:8],
        "os": _str(state.get("os")),
        "agent_version": _str(state.get("agent_version"), 64),
    }


def snapshot(db: Session) -> dict:
    """Everything Home Assistant shows, in one read."""
    users_total = db.scalar(select(func.count()).select_from(User)) or 0
    admins = db.scalar(select(func.count()).select_from(User).where(User.is_admin.is_(True))) or 0

    workspaces = db.scalars(select(Workspace)).all()
    by_status: dict[str, int] = {}
    for ws in workspaces:
        by_status[ws.status] = by_status.get(ws.status, 0) + 1
    running = [ws for ws in workspaces if ws.status == "running" and ws.container_id]
    stats = _workspace_stats(running)
    owners = dict(db.execute(select(User.id, User.username)).all())
    # Only running workspaces can be on a screen; a lease left on a stopped one
    # is stale and mustn't show up as what a device is showing.
    ws_names = {ws.id: (ws.name, owners.get(ws.user_id, "")) for ws in workspaces if ws.status == "running"}

    zones_out = []
    for zone in db.scalars(select(Zone).order_by(Zone.id)).all():
        if zone.id != 0 and zone.status not in ("enrolled", "offline"):
            continue  # registered but never enrolled: nothing to report
        in_zone = [ws for ws in workspaces if ws.zone_id == zone.id]
        zone_running = [ws for ws in in_zone if ws.status == "running"]
        samples = [stats[ws.id] for ws in zone_running if ws.id in stats]
        zones_out.append({
            "id": zone.public_id,
            "zone_id": zone.id,
            "name": zone.name,
            "local": zone.id == 0,
            # The status monitor flips a remote zone to offline when its daemon
            # stops answering; the local zone is this process's own host.
            "online": zone.id == 0 or zone.status == "enrolled",
            "last_seen_at": _iso(zone.last_seen_at),
            "workspaces_total": len(in_zone),
            "workspaces_running": len(zone_running),
            "users_active": len({ws.user_id for ws in zone_running}),
            # Summed over the zone's running workspaces; null while none of
            # them reports (rather than a misleading zero).
            "cpu_percent": round(sum(s["cpu_pct"] for s in samples), 1) if samples or not zone_running else None,
            "memory_used_bytes": sum(s["mem_used"] for s in samples) if samples or not zone_running else None,
        })

    showing = stream_leases.by_device()
    clients = db.scalars(select(ThinClient).order_by(ThinClient.name, ThinClient.id)).all()
    clients_out = [thin_client_out(db, c, showing, ws_names) for c in clients if c.status == "enrolled"]

    running_any = [ws for ws in workspaces if ws.status == "running"]
    return {
        "instance_id": instance_id(db),
        "generated_at": tc.now_utc().isoformat(),
        "users": {
            "total": users_total,
            "admins": admins,
            # Owners of a running workspace.
            "active": len({ws.user_id for ws in running_any}),
            "on_thin_clients": len({c["session_username"] for c in clients_out if c["session_username"]}),
        },
        "workspaces": {
            "total": len(workspaces),
            "running": by_status.get("running", 0),
            "starting": by_status.get("creating", 0),
            "stopping": by_status.get("stopping", 0),
            "stopped": by_status.get("stopped", 0),
            "error": by_status.get("error", 0),
            "migrating": by_status.get("migrating", 0),
        },
        "thin_clients_summary": {
            "total": len(clients_out),
            "online": sum(1 for c in clients_out if c["online"]),
            "in_use": sum(1 for c in clients_out if c["session_username"]),
        },
        "zones": zones_out,
        "thin_clients": clients_out,
    }
