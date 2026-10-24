"""Thin clients: kiosk devices that show the Cove SPA in a locked-down browser.

The device runs a small stdlib-only agent (``thinclient/agent/``) that dials OUT
to Cove over HTTPS and long-polls for work, so a thin client never needs an
inbound port or anything beyond the one HTTPS entrypoint Cove already has.

Trust and session model, in one place:

* **Enrolment** uses a single-use, short-lived, human-typable code an admin mints
  (only its sha256 is stored). The device swaps it for a long-lived random bearer
  token (also stored only as a sha256).
* **The device token never reaches the browser.** To open Chromium the agent asks
  for a one-time *ticket*; Chromium opens ``/thin-client/bind?ticket=…`` which
  swaps it for an httpOnly ``cove_tc`` cookie naming the device. That cookie
  grants nothing on its own: it only marks sessions created in that browser as
  belonging to the device.
* **Device-scoped sessions.** Tokens minted while the cookie is present carry a
  ``tc`` claim (and ``sst``, the session start). They are valid only while the
  device is enrolled and were issued after ``ThinClient.session_valid_from``,
  and never past ``sst + session_max_hours``. Signing a thin client out — idle
  timeout on the device, the SPA's logout, or an admin — bumps that one
  timestamp, so it never signs the same user out of their other browsers.
"""

import asyncio
import hashlib
import json
import logging
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

import jwt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.config import get_settings
from server.models import ThinClient, ThinClientCommand
from server.schemas import ThinClientPolicy

logger = logging.getLogger(__name__)

# A device that has not checked in for this long is shown as offline. The agent
# long-polls (~25 s per request), so this is a few missed polls.
ONLINE_WINDOW = timedelta(seconds=75)
# Longest a check-in may be held open. Comfortably under common proxy read
# timeouts (nginx/NPM default 60 s).
MAX_WAIT_SECONDS = 25
TICKET_SECONDS = 120
BIND_COOKIE = "cove_tc"
BIND_COOKIE_DAYS = 30
# A command the device never answered is shown as lost after this long.
COMMAND_LOST_AFTER = timedelta(minutes=30)
STATE_MAX_BYTES = 32 * 1024
SCREENSHOT_MAX_BYTES = 8 * 1024 * 1024


# ── Codes and tokens ─────────────────────────────────────────────────────────

# Crockford base32: no I, L, O or U, so a code read off a screen and typed on a
# console keyboard can't be misread.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_TRANSLATE = str.maketrans({"O": "0", "I": "1", "L": "1"})


def new_enroll_code() -> str:
    """80 random bits as ``XXXX-XXXX-XXXX-XXXX``."""
    raw = "".join(secrets.choice(_CROCKFORD) for _ in range(16))
    return "-".join(raw[i : i + 4] for i in range(0, 16, 4))


def normalize_code(code: str) -> str:
    return re.sub(r"[\s-]", "", (code or "").upper()).translate(_CODE_TRANSLATE)


def hash_code(code: str) -> str:
    return hashlib.sha256(normalize_code(code).encode()).hexdigest()


def new_device_token() -> str:
    return "ctc_" + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def enroll_code_valid(client: Optional[ThinClient]) -> bool:
    if client is None or client.enroll_token_hash is None or client.enroll_consumed_at:
        return False
    exp = _utc(client.enroll_token_expires_at)
    return exp is None or exp > now_utc()


def find_by_enroll_code(db: Session, code: str) -> Optional[ThinClient]:
    if not normalize_code(code):
        return None
    return db.scalar(select(ThinClient).where(ThinClient.enroll_token_hash == hash_code(code)))


def find_by_device_token(db: Session, token: str) -> Optional[ThinClient]:
    if not token:
        return None
    client = db.scalar(select(ThinClient).where(ThinClient.device_token_hash == hash_token(token)))
    if client is None or client.status != "enrolled":
        return None
    return client


# ── Policy and state ─────────────────────────────────────────────────────────

def load_policy(client: ThinClient) -> ThinClientPolicy:
    if client.policy_json:
        try:
            return ThinClientPolicy.model_validate(json.loads(client.policy_json))
        except Exception:  # noqa: BLE001 - a bad stored policy falls back to defaults
            logger.warning("Thin client %s has an unreadable policy; using defaults", client.public_id)
    return ThinClientPolicy()


def save_policy(client: ThinClient, policy: ThinClientPolicy) -> None:
    client.policy_json = policy.model_dump_json()
    client.policy_version = (client.policy_version or 0) + 1


def load_state(client: ThinClient) -> dict[str, Any]:
    if not client.state_json:
        return {}
    try:
        data = json.loads(client.state_json)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def is_online(client: ThinClient) -> bool:
    seen = _utc(client.last_seen_at)
    return bool(seen and now_utc() - seen < ONLINE_WINDOW)


def _host_of(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.lower() if host else None


def allowed_hosts(client: ThinClient, cp_url: str) -> list[str]:
    """Hosts the device's Chromium may navigate to, in Chromium URLBlocklist
    filter syntax: a leading ``.`` means that exact host only; a bare host also
    matches its subdomains (used for the workspace domain)."""
    settings = get_settings()
    hosts: list[str] = []
    cp_host = _host_of(cp_url)
    if cp_host:
        hosts.append("." + cp_host)
    if settings.workspace_domain:
        hosts.append(settings.workspace_domain.lower().strip("."))
    oidc_host = _host_of(settings.oidc_issuer) if settings.oidc_enabled else None
    if oidc_host:
        hosts.append("." + oidc_host)
    for h in load_policy(client).extra_allowed_hosts:
        hosts.append(h[2:] if h.startswith("*.") else "." + h)
    seen: list[str] = []
    for h in hosts:
        if h not in seen:
            seen.append(h)
    return seen


def cp_url_for(request_base_url: str) -> str:
    return (get_settings().app_origin or request_base_url).rstrip("/")


# ── Agent file ───────────────────────────────────────────────────────────────

_AGENT_PATHS = (
    Path("/app/thinclient/agent/cove_thin_client.py"),
    Path(__file__).resolve().parents[2] / "thinclient" / "agent" / "cove_thin_client.py",
)
_VERSION_RE = re.compile(r'^AGENT_VERSION\s*=\s*"([^"]+)"', re.M)
_agent_cache: Optional[tuple[str, bytes, str, str]] = None  # (sig, bytes, sha256, version)
_agent_lock = threading.Lock()


def agent_bundle() -> Optional[tuple[bytes, str, str]]:
    """``(source, sha256, version)`` of the agent Cove hands out, or None."""
    global _agent_cache
    for path in _AGENT_PATHS:
        if path.is_file():
            st = path.stat()
            sig = f"{path}:{st.st_size}:{st.st_mtime_ns}"
            with _agent_lock:
                if _agent_cache is None or _agent_cache[0] != sig:
                    data = path.read_bytes()
                    m = _VERSION_RE.search(data.decode("utf-8", "replace"))
                    _agent_cache = (
                        sig,
                        data,
                        hashlib.sha256(data).hexdigest(),
                        m.group(1) if m else "unknown",
                    )
                return _agent_cache[1], _agent_cache[2], _agent_cache[3]
    return None


# ── Wake-ups for long-polling check-ins ──────────────────────────────────────

# client id -> (event, loop). Cove runs one uvicorn worker, so an in-process
# event wakes a waiting check-in the moment an admin queues work. Check-ins also
# re-read the database every few seconds, so a missed wake only adds latency.
_waiters: dict[int, tuple[asyncio.Event, asyncio.AbstractEventLoop]] = {}
_waiters_lock = threading.Lock()


def waiter_event(client_id: int) -> asyncio.Event:
    loop = asyncio.get_running_loop()
    with _waiters_lock:
        entry = _waiters.get(client_id)
        if entry is None or entry[1] is not loop:
            entry = (asyncio.Event(), loop)
            _waiters[client_id] = entry
        return entry[0]


def notify(client_id: int) -> None:
    """Wake a waiting check-in for this device. Safe from any thread."""
    with _waiters_lock:
        entry = _waiters.get(client_id)
    if entry is None:
        return
    event, loop = entry
    try:
        loop.call_soon_threadsafe(event.set)
    except RuntimeError:  # loop closed (tests, shutdown)
        pass


# ── Commands ─────────────────────────────────────────────────────────────────

# kind -> validator for its args (returns the cleaned args or raises ValueError).
def _no_args(args: dict) -> dict:
    return {}


def _reason_args(args: dict) -> dict:
    reason = str(args.get("reason", "") or "")[:64]
    return {"reason": reason} if reason else {}


def _seconds_args(args: dict) -> dict:
    try:
        seconds = int(args.get("seconds", 10))
    except (TypeError, ValueError):
        raise ValueError("seconds must be a number")
    return {"seconds": max(3, min(seconds, 60))}


def _logs_args(args: dict) -> dict:
    try:
        lines = int(args.get("lines", 200))
    except (TypeError, ValueError):
        raise ValueError("lines must be a number")
    unit = args.get("unit", "agent")
    if unit not in ("agent", "session", "system"):
        raise ValueError("unit must be agent, session or system")
    return {"lines": max(10, min(lines, 2000)), "unit": unit}


COMMANDS = {
    "screen_on": _no_args,
    "screen_off": _no_args,
    "restart_browser": _reason_args,
    "identify": _seconds_args,
    "screenshot": _no_args,
    "apt_upgrade": _no_args,
    "reboot": _no_args,
    "poweroff": _no_args,
    "logs": _logs_args,
}


def clean_command(kind: str, args: dict) -> dict:
    validator = COMMANDS.get(kind)
    if validator is None:
        raise ValueError(f"Unknown command '{kind}'")
    return validator(args or {})


def queue_command(
    db: Session, client: ThinClient, kind: str, args: Optional[dict] = None, *, by: Optional[str] = None
) -> ThinClientCommand:
    cmd = ThinClientCommand(
        client_id=client.id,
        kind=kind,
        args_json=json.dumps(clean_command(kind, args or {})),
        status="queued",
        created_by=by,
    )
    db.add(cmd)
    db.commit()
    db.refresh(cmd)
    notify(client.id)
    return cmd


def command_args(cmd: ThinClientCommand) -> dict:
    try:
        data = json.loads(cmd.args_json or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def command_status(cmd: ThinClientCommand) -> str:
    if cmd.status == "sent":
        sent = _utc(cmd.sent_at)
        if sent and now_utc() - sent > COMMAND_LOST_AFTER:
            return "lost"
    return cmd.status


def pending_count(db: Session, client: ThinClient) -> int:
    return db.scalar(
        select(func.count())
        .select_from(ThinClientCommand)
        .where(ThinClientCommand.client_id == client.id, ThinClientCommand.status.in_(("queued", "sent")))
    ) or 0


def take_queued(db: Session, client: ThinClient) -> list[ThinClientCommand]:
    """Mark every queued command as sent and return them (oldest first)."""
    cmds = db.scalars(
        select(ThinClientCommand)
        .where(ThinClientCommand.client_id == client.id, ThinClientCommand.status == "queued")
        .order_by(ThinClientCommand.id)
    ).all()
    now = now_utc()
    for c in cmds:
        c.status = "sent"
        c.sent_at = now
    if cmds:
        db.commit()
    return list(cmds)


def has_queued(db: Session, client_id: int) -> bool:
    return bool(
        db.scalar(
            select(ThinClientCommand.id)
            .where(ThinClientCommand.client_id == client_id, ThinClientCommand.status == "queued")
            .limit(1)
        )
    )


# ── Sessions ─────────────────────────────────────────────────────────────────

def revoke_sessions(db: Session, client: ThinClient, *, by: Optional[str] = None, reset_browser: bool = True) -> None:
    """Sign the device out: revoke every token and browser binding minted for it,
    and (unless the device asked for this itself) tell it to restart Chromium on
    a wiped profile."""
    client.session_valid_from = now_utc()
    client.session_user_id = None
    client.session_username = None
    client.session_started_at = None
    db.commit()
    if reset_browser and client.status == "enrolled":
        queue_command(db, client, "restart_browser", {"reason": "signout"}, by=by)


def session_cap(client: ThinClient, sst: float) -> Optional[datetime]:
    """Absolute end of a device session that started at ``sst`` (epoch secs)."""
    hours = load_policy(client).session_max_hours
    if not hours:
        return None
    return datetime.fromtimestamp(sst, tz=timezone.utc) + timedelta(hours=hours)


def _issued_after(client: ThinClient, payload: dict) -> bool:
    valid_from = _utc(client.session_valid_from)
    if valid_from is None:
        return True
    try:
        iat = float(payload.get("iat", 0))
    except (TypeError, ValueError):
        return False
    return datetime.fromtimestamp(iat, tz=timezone.utc) >= valid_from


def session_valid(db: Session, public_id: str, payload: dict) -> bool:
    """Is a token carrying ``tc=public_id`` still good for this device?"""
    client = db.scalar(select(ThinClient).where(ThinClient.public_id == str(public_id)))
    if client is None or client.status != "enrolled":
        return False
    if not _issued_after(client, payload):
        return False
    sst = payload.get("sst")
    if sst is not None:
        try:
            cap = session_cap(client, float(sst))
        except (TypeError, ValueError):
            return False
        if cap is not None and now_utc() >= cap:
            return False
    return True


def active_session_username(client: ThinClient) -> Optional[str]:
    """Who is signed in, unless that session has already hit its cap."""
    if not client.session_username:
        return None
    started = _utc(client.session_started_at)
    if started is not None:
        cap = session_cap(client, started.timestamp())
        if cap is not None and now_utc() >= cap:
            return None
    return client.session_username


def note_session(db: Session, public_id: str, user) -> None:
    client = db.scalar(select(ThinClient).where(ThinClient.public_id == public_id))
    if client is None:
        return
    if client.session_user_id != user.id:
        client.session_started_at = now_utc()
    client.session_user_id = user.id
    client.session_username = user.username
    db.commit()


# ── Browser binding (ticket -> cookie) ───────────────────────────────────────

def mint_ticket(db: Session, client: ThinClient) -> str:
    ticket = secrets.token_urlsafe(32)
    client.ticket_hash = hash_token(ticket)
    client.ticket_expires_at = now_utc() + timedelta(seconds=TICKET_SECONDS)
    db.commit()
    return ticket


def consume_ticket(db: Session, ticket: str) -> Optional[ThinClient]:
    if not ticket:
        return None
    client = db.scalar(select(ThinClient).where(ThinClient.ticket_hash == hash_token(ticket)))
    if client is None or client.status != "enrolled":
        return None
    exp = _utc(client.ticket_expires_at)
    client.ticket_hash = None
    client.ticket_expires_at = None
    db.commit()
    if exp is None or exp < now_utc():
        return None
    return client


def mint_bind_cookie(client: ThinClient) -> str:
    settings = get_settings()
    now = now_utc()
    payload = {
        "type": "tc_bind",
        "tc": client.public_id,
        "iat": now.timestamp(),
        "exp": now + timedelta(days=BIND_COOKIE_DAYS),
    }
    return jwt.encode(payload, settings.get_secret_key(), algorithm=settings.jwt_algorithm)


def client_from_bind_cookie(db: Session, cookie: Optional[str]) -> Optional[ThinClient]:
    """The thin client this browser is bound to, or None."""
    if not cookie:
        return None
    from server.security import decode_token

    payload = decode_token(cookie)
    if not payload or payload.get("type") != "tc_bind" or not payload.get("tc"):
        return None
    client = db.scalar(select(ThinClient).where(ThinClient.public_id == str(payload["tc"])))
    if client is None or client.status != "enrolled" or not _issued_after(client, payload):
        return None
    return client
