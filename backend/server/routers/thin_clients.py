"""Thin client endpoints.

Three audiences, three routers:

* ``admin_router`` (``/api/admin/thin-clients``) — admins register devices, mint
  enrolment codes, edit policy and queue commands.
* ``device_router`` (``/api/thin-client``) — the on-device agent, authenticated by
  its bearer device token. ``/checkin`` is a long-poll: it returns at once when
  there is work, otherwise holds the request open for up to ~25 s.
* ``public_router`` — the bootstrap paths that can't require either: the install
  script and enrolment (single-use code), the browser binding (one-time ticket),
  and ``/api/thin-client/self`` (the bind cookie) for the SPA running on a kiosk.
"""

import asyncio
import json
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from server import thin_clients as tc
from server.config import get_settings
from server.db import SessionLocal
from server.deps import AdminUser, DbSession
from server.models import ThinClient, ThinClientCommand, User, Workspace
from server.net import client_ip
from server.schemas import (
    ThinClientCheckinIn,
    ThinClientCheckinOut,
    ThinClientCommandForDevice,
    ThinClientCommandIn,
    ThinClientCommandOut,
    ThinClientCreate,
    ThinClientEnrollIn,
    ThinClientEnrollOut,
    ThinClientEnrollTokenIn,
    ThinClientEnrollTokenOut,
    ThinClientKeepAwakeIn,
    ThinClientOut,
    ThinClientPolicy,
    ThinClientResultIn,
    ThinClientSelfOut,
    ThinClientSignedOutIn,
    ThinClientTicketOut,
    ThinClientUpdate,
    ThinClientWorkspaceOption,
)
from server.thin_client_install import render_install_script

admin_router = APIRouter(prefix="/api/admin/thin-clients", tags=["thin-clients"])
device_router = APIRouter(prefix="/api/thin-client", tags=["thin-clients"])
public_router = APIRouter(tags=["thin-clients"])


def _audit(db, action, *, detail=None, user=None, request: Optional[Request] = None):
    from server.main import record_audit

    record_audit(db, action, detail=detail, user=user, ip=client_ip(request) if request else None)


def _out(db: Session, client: ThinClient) -> ThinClientOut:
    state = tc.load_state(client)
    bundle = tc.agent_bundle()
    applied = state.pop("applied_policy_version", None)
    return ThinClientOut(
        id=client.id,
        public_id=client.public_id,
        name=client.name,
        location=client.location,
        status=client.status,
        online=tc.is_online(client),
        enrolled_at=client.enrolled_at,
        last_seen_at=client.last_seen_at,
        created_at=client.created_at,
        policy=tc.load_policy(client),
        policy_version=client.policy_version,
        applied_policy_version=applied if isinstance(applied, int) else None,
        state=state,
        session_username=tc.active_session_username(client),
        session_started_at=client.session_started_at if tc.active_session_username(client) else None,
        keep_awake_until=client.keep_awake_until,
        bound_at=client.bound_at,
        screenshot_at=client.screenshot_at,
        pending_commands=tc.pending_count(db, client),
        latest_agent_version=bundle[2] if bundle else None,
    )


def _cmd_out(cmd: ThinClientCommand) -> ThinClientCommandOut:
    return ThinClientCommandOut(
        id=cmd.id,
        kind=cmd.kind,
        args=tc.command_args(cmd),
        status=tc.command_status(cmd),
        result=cmd.result,
        created_by=cmd.created_by,
        created_at=cmd.created_at,
        sent_at=cmd.sent_at,
        finished_at=cmd.finished_at,
    )


def _get(db: Session, client_id: int) -> ThinClient:
    client = db.get(ThinClient, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Thin client not found")
    return client


# ── Admin ────────────────────────────────────────────────────────────────────

@admin_router.get("", response_model=list[ThinClientOut])
def list_thin_clients(admin: AdminUser, db: DbSession):
    clients = db.scalars(select(ThinClient).order_by(ThinClient.name, ThinClient.id)).all()
    return [_out(db, c) for c in clients]


@admin_router.post("", response_model=ThinClientOut, status_code=status.HTTP_201_CREATED)
def create_thin_client(body: ThinClientCreate, admin: AdminUser, db: DbSession, request: Request):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    client = ThinClient(name=name, location=(body.location or "").strip() or None, status="pending")
    client.policy_json = ThinClientPolicy().model_dump_json()
    db.add(client)
    db.commit()
    db.refresh(client)
    _audit(db, "admin.thin_client.create", detail=client.name, user=admin, request=request)
    return _out(db, client)


@admin_router.get("/workspace-options", response_model=list[ThinClientWorkspaceOption])
def workspace_options(admin: AdminUser, db: DbSession):
    """Every workspace (with its owner), for the 'pinned workspace' picker."""
    rows = db.execute(
        select(Workspace, User.username)
        .join(User, User.id == Workspace.user_id)
        .order_by(User.username, Workspace.name)
    ).all()
    return [
        ThinClientWorkspaceOption(public_id=w.public_id, name=w.name, username=u, status=w.status) for w, u in rows
    ]


@admin_router.get("/{client_id}", response_model=ThinClientOut)
def get_thin_client(client_id: int, admin: AdminUser, db: DbSession):
    return _out(db, _get(db, client_id))


@admin_router.patch("/{client_id}", response_model=ThinClientOut)
def update_thin_client(client_id: int, body: ThinClientUpdate, admin: AdminUser, db: DbSession, request: Request):
    client = _get(db, client_id)
    changed = []
    if body.name is not None and body.name.strip():
        client.name = body.name.strip()
        changed.append("name")
    if body.location is not None:
        client.location = body.location.strip() or None
        changed.append("location")
    if body.policy is not None:
        before = tc.load_policy(client).model_dump()
        tc.save_policy(client, body.policy)
        after = body.policy.model_dump()
        # Name what changed, with values: extra allowed hosts widen what the
        # kiosk browser may open, so they belong in the audit trail.
        diff = [f"{k}={after[k]!r}" for k in sorted(after) if before.get(k) != after[k]]
        changed.append(f"policy ({'; '.join(diff)})" if diff else "policy")
    db.commit()
    if changed:
        tc.notify(client.id)
        detail = f"{client.name}: {', '.join(changed)}"
        _audit(db, "admin.thin_client.update", detail=detail, user=admin, request=request)
    return _out(db, client)


@admin_router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_thin_client(client_id: int, admin: AdminUser, db: DbSession, request: Request):
    client = _get(db, client_id)
    name = client.name
    db.delete(client)
    db.commit()
    tc.notify(client_id)
    _audit(db, "admin.thin_client.delete", detail=name, user=admin, request=request)


@admin_router.post("/{client_id}/enroll-token", response_model=ThinClientEnrollTokenOut)
def mint_enroll_token(
    client_id: int, admin: AdminUser, db: DbSession, request: Request, body: Optional[ThinClientEnrollTokenIn] = None
):
    """A single-use enrolment code + install one-liner. Only the code's sha256 is
    stored. Re-enrolling an enrolled device replaces its device token when the
    code is used, so the old install stops working."""
    client = _get(db, client_id)
    ttl = (body or ThinClientEnrollTokenIn()).ttl_hours
    code = tc.new_enroll_code()
    client.enroll_token_hash = tc.hash_code(code)
    client.enroll_token_expires_at = tc.now_utc() + timedelta(hours=ttl)
    client.enroll_consumed_at = None
    db.commit()
    _audit(db, "admin.thin_client.enroll_token", detail=client.name, user=admin, request=request)
    cp = tc.cp_url_for(str(request.base_url))
    return ThinClientEnrollTokenOut(
        code=code,
        expires_at=client.enroll_token_expires_at,
        install_command=tc.install_command(cp, code),
    )


@admin_router.get("/{client_id}/commands", response_model=list[ThinClientCommandOut])
def list_commands(client_id: int, admin: AdminUser, db: DbSession):
    _get(db, client_id)
    cmds = db.scalars(
        select(ThinClientCommand)
        .where(ThinClientCommand.client_id == client_id)
        .order_by(ThinClientCommand.id.desc())
        .limit(50)
    ).all()
    return [_cmd_out(c) for c in cmds]


@admin_router.post("/{client_id}/commands", response_model=ThinClientCommandOut, status_code=status.HTTP_201_CREATED)
def queue_command(client_id: int, body: ThinClientCommandIn, admin: AdminUser, db: DbSession, request: Request):
    client = _get(db, client_id)
    if client.status != "enrolled":
        raise HTTPException(status_code=409, detail="This thin client is not enrolled yet")
    try:
        cmd = tc.queue_command(db, client, body.kind, body.args, by=admin.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _audit(db, "admin.thin_client.command", detail=f"{client.name}: {cmd.kind}", user=admin, request=request)
    return _cmd_out(cmd)


@admin_router.post("/{client_id}/signout", response_model=ThinClientOut)
def sign_out(client_id: int, admin: AdminUser, db: DbSession, request: Request):
    client = _get(db, client_id)
    tc.revoke_sessions(db, client, by=admin.username)
    _audit(db, "admin.thin_client.signout", detail=client.name, user=admin, request=request)
    return _out(db, client)


@admin_router.post("/{client_id}/keep-awake", response_model=ThinClientOut)
def keep_awake(client_id: int, body: ThinClientKeepAwakeIn, admin: AdminUser, db: DbSession, request: Request):
    client = _get(db, client_id)
    client.keep_awake_until = tc.now_utc() + timedelta(minutes=body.minutes) if body.minutes else None
    # Bump the policy version so the agent picks it up on its next check-in.
    client.policy_version = (client.policy_version or 0) + 1
    db.commit()
    tc.notify(client.id)
    _audit(db, "admin.thin_client.keep_awake", detail=f"{client.name}: {body.minutes} min", user=admin, request=request)
    return _out(db, client)


@admin_router.get("/{client_id}/screenshot.png")
def get_screenshot(client_id: int, admin: AdminUser, db: DbSession):
    client = _get(db, client_id)
    if not client.screenshot_png:
        raise HTTPException(status_code=404, detail="No screenshot yet")
    return Response(content=client.screenshot_png, media_type="image/png", headers={"Cache-Control": "no-store"})


# ── Device (agent) ───────────────────────────────────────────────────────────

def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


def _device(db: Session, request: Request) -> ThinClient:
    client = tc.find_by_device_token(db, _bearer(request))
    if client is None:
        raise HTTPException(status_code=401, detail="Unknown or revoked device")
    return client


def _checkin_once(token: str, body: ThinClientCheckinIn, cp: str, record: bool) -> Optional[ThinClientCheckinOut]:
    with SessionLocal() as db:
        client = tc.find_by_device_token(db, token)
        if client is None:
            return None
        if record:
            state = dict(body.state)
            state["applied_policy_version"] = body.applied_policy_version
            blob = json.dumps(state, default=str)
            if len(blob) <= tc.STATE_MAX_BYTES:
                client.state_json = blob
        client.last_seen_at = tc.now_utc()
        db.commit()
        who = client.session_username
        if tc.enforce_session_limits(db, client) == "cap":
            _audit(db, "thin_client.session_cap", detail=f"{client.name}: {who}")
        cmds = tc.take_queued(db, client)
        bundle = tc.agent_bundle()
        return ThinClientCheckinOut(
            name=client.name,
            policy=tc.load_policy(client),
            policy_version=client.policy_version,
            commands=[ThinClientCommandForDevice(id=c.id, kind=c.kind, args=tc.command_args(c)) for c in cmds],
            allowed_hosts=tc.allowed_hosts(client, cp),
            keep_awake_until=client.keep_awake_until,
            agent_version=bundle[2] if bundle else None,
            agent_sha256=bundle[1] if bundle else None,
            bound_at=client.bound_at,
            session_username=tc.active_session_username(client),
            server_time=tc.now_utc(),
        )


def _has_work(client_id: int, applied: Optional[int]) -> bool:
    with SessionLocal() as db:
        client = db.get(ThinClient, client_id)
        if client is None or client.status != "enrolled":
            return True  # let the next check-in tell the device it's gone
        return client.policy_version != applied or tc.has_queued(db, client_id)


def _client_id_for(token: str) -> Optional[int]:
    with SessionLocal() as db:
        client = tc.find_by_device_token(db, token)
        return client.id if client else None


@device_router.post("/checkin", response_model=ThinClientCheckinOut)
async def checkin(body: ThinClientCheckinIn, request: Request):
    """Report state, receive policy and commands. With ``wait`` > 0 and nothing
    to do, the request is held open until work arrives or ``wait`` elapses."""
    token = _bearer(request)
    cp = tc.cp_url_for(str(request.base_url))
    client_id = await run_in_threadpool(_client_id_for, token)
    if client_id is None:
        raise HTTPException(status_code=401, detail="Unknown or revoked device")
    # Arm the wake-up before looking for work, so a command queued between the
    # look and the wait still wakes us.
    event = tc.waiter_event(client_id)
    event.clear()
    out = await run_in_threadpool(_checkin_once, token, body, cp, True)
    if out is None:
        raise HTTPException(status_code=401, detail="Unknown or revoked device")
    wait = min(body.wait, tc.MAX_WAIT_SECONDS)
    if out.commands or not wait or out.policy_version != body.applied_policy_version:
        return out
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait
    while (remaining := deadline - loop.time()) > 0:
        try:
            await asyncio.wait_for(event.wait(), timeout=min(remaining, 5))
        except asyncio.TimeoutError:
            pass
        event.clear()
        if await request.is_disconnected():
            return Response(status_code=499)
        if await run_in_threadpool(_has_work, client_id, body.applied_policy_version):
            break
    out = await run_in_threadpool(_checkin_once, token, body, cp, False)
    if out is None:
        raise HTTPException(status_code=401, detail="Unknown or revoked device")
    return out


@device_router.post("/commands/{command_id}/result", status_code=status.HTTP_204_NO_CONTENT)
def command_result(command_id: int, body: ThinClientResultIn, request: Request, db: DbSession):
    client = _device(db, request)
    cmd = db.get(ThinClientCommand, command_id)
    if cmd is None or cmd.client_id != client.id:
        raise HTTPException(status_code=404, detail="Command not found")
    cmd.status = "done" if body.ok else "failed"
    cmd.result = body.output[-64_000:]
    cmd.finished_at = tc.now_utc()
    db.commit()


@device_router.post("/screenshot", status_code=status.HTTP_204_NO_CONTENT)
async def upload_screenshot(request: Request):
    # Authenticate before reading a byte of the body, so an anonymous caller
    # can't make Cove buffer megabytes per request.
    if await run_in_threadpool(_client_id_for, _bearer(request)) is None:
        raise HTTPException(status_code=401, detail="Unknown or revoked device")
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > tc.SCREENSHOT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Screenshot too large")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > tc.SCREENSHOT_MAX_BYTES:
            raise HTTPException(status_code=413, detail="Screenshot too large")
    data = bytes(buf)
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(status_code=400, detail="Expected a PNG")

    def _store():
        with SessionLocal() as db:
            client = _device(db, request)
            client.screenshot_png = data
            client.screenshot_at = tc.now_utc()
            db.commit()

    await run_in_threadpool(_store)


@device_router.post("/ticket", response_model=ThinClientTicketOut)
def browser_ticket(request: Request, db: DbSession):
    """A one-time URL that binds a freshly launched browser to this device."""
    client = _device(db, request)
    ticket = tc.mint_ticket(db, client)
    cp = tc.cp_url_for(str(request.base_url))
    return ThinClientTicketOut(
        url=f"{cp}/thin-client/bind?ticket={ticket}", expires_in=tc.TICKET_SECONDS, issued_at=tc.now_utc()
    )


@device_router.post("/signed-out", status_code=status.HTTP_204_NO_CONTENT)
def device_signed_out(request: Request, db: DbSession, body: Optional[ThinClientSignedOutIn] = None):
    """The device reset its browser (idle timeout, or a restart command). Revoke
    its sessions; the agent resets the browser itself, so no restart command is
    queued. Agents before 1.1.1 send no reason; theirs was always "idle"
    unless a command asked for the reset."""
    client = _device(db, request)
    who = client.session_username
    reason = (body.reason if body else "") or "idle"
    tc.revoke_sessions(db, client, reset_browser=False)
    if who:
        action = "thin_client.idle_signout" if reason == "idle" else "thin_client.browser_reset"
        detail = f"{client.name}: {who}" + ("" if reason == "idle" else f" ({reason})")
        _audit(db, action, detail=detail, request=request)


@device_router.get("/agent")
def download_agent(request: Request, db: DbSession):
    _device(db, request)
    bundle = tc.agent_bundle()
    if bundle is None:
        raise HTTPException(status_code=404, detail="Agent not available on this server")
    data, sha, version = bundle
    return Response(
        content=data,
        media_type="text/x-python",
        headers={"X-Agent-Sha256": sha, "X-Agent-Version": version, "Cache-Control": "no-store"},
    )


# ── Public bootstrap ─────────────────────────────────────────────────────────

def _rate_ok(request: Request, scope: str) -> bool:
    from server.routers.auth import _check_rate_limit

    return _check_rate_limit(client_ip(request), scope)


@public_router.get("/thin-client/install.sh", response_class=PlainTextResponse)
def install_script(code: str, request: Request, db: DbSession):
    if not _rate_ok(request, "tc-enroll"):
        raise HTTPException(status_code=429, detail="Too many attempts")
    client = tc.find_by_enroll_code(db, code)
    if not tc.enroll_code_valid(client):
        raise HTTPException(status_code=404, detail="Invalid or expired enrolment code")
    cp = tc.cp_url_for(str(request.base_url))
    bundle = tc.trust_bundle()
    return PlainTextResponse(
        render_install_script(
            cp_url=cp, code=tc.normalize_code(code), name=client.name, ca_pem=bundle[0] if bundle else ""
        )
    )


@public_router.post("/api/thin-client/enroll", response_model=ThinClientEnrollOut)
def enroll(code: str, request: Request, db: DbSession, body: Optional[ThinClientEnrollIn] = None):
    if not _rate_ok(request, "tc-enroll"):
        raise HTTPException(status_code=429, detail="Too many attempts")
    client = tc.find_by_enroll_code(db, code)
    if not tc.enroll_code_valid(client):
        raise HTTPException(status_code=403, detail="Invalid or expired enrolment code")
    now = tc.now_utc()
    # Atomic single use: only the first caller flips consumed_at.
    res = db.execute(
        update(ThinClient)
        .where(ThinClient.id == client.id, ThinClient.enroll_consumed_at.is_(None))
        .values(enroll_consumed_at=now)
    )
    db.commit()
    if res.rowcount != 1:
        raise HTTPException(status_code=409, detail="Enrolment code already used")
    db.refresh(client)
    token = tc.new_device_token()
    client.device_token_hash = tc.hash_token(token)
    client.status = "enrolled"
    client.enrolled_at = now
    client.last_seen_at = now
    # A fresh install starts with no live sessions from a previous one.
    client.session_valid_from = now
    client.session_user_id = client.session_username = client.session_started_at = None
    if not client.policy_json:
        client.policy_json = ThinClientPolicy().model_dump_json()
    hostname = ((body.hostname if body else "") or "").strip()[:255]
    if hostname:
        client.state_json = json.dumps({"hostname": hostname})
    db.commit()
    _audit(db, "thin_client.enroll", detail=f"{client.name} ({hostname or 'unknown host'})", request=request)
    return ThinClientEnrollOut(
        device_token=token,
        public_id=client.public_id,
        name=client.name,
        cp_url=tc.cp_url_for(str(request.base_url)),
    )


_BIND_FAILED = """<!doctype html><html><head><meta charset="utf-8"><title>Cove</title>
<style>html,body{margin:0;height:100%;background:#06060f;color:#c8d8ff;font:16px 'Share Tech Mono',monospace}
main{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px}
h1{color:#ff2055;letter-spacing:4px;font-size:20px;text-shadow:0 0 8px #ff2055}p{color:#7d8bb5}</style></head>
<body><main><h1>// BINDING FAILED</h1><p>This thin client could not bind its browser to Cove.</p>
<p>It will retry automatically.</p></main></body></html>"""


@public_router.get("/thin-client/bind")
def bind_browser(ticket: str, request: Request, db: DbSession):
    """Swap a one-time ticket for the httpOnly ``cove_tc`` cookie and land on the
    SPA. Fails closed: a kiosk that can't bind shows an error instead of an
    unscoped login page."""
    client = tc.consume_ticket(db, ticket)
    if client is None:
        # Only failures count toward the limit: a ticket is 256 random bits, so
        # the limit isn't what protects it, and charging every bind to the
        # source IP let junk requests lock out real kiosks behind the same NAT.
        if not _rate_ok(request, "tc-bind"):
            raise HTTPException(status_code=429, detail="Too many attempts")
        return HTMLResponse(_BIND_FAILED, status_code=403)
    client.bound_at = tc.now_utc()
    db.commit()
    settings = get_settings()
    resp = RedirectResponse(url="/app", status_code=303)
    resp.set_cookie(
        tc.BIND_COOKIE,
        tc.mint_bind_cookie(client),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        max_age=tc.BIND_COOKIE_DAYS * 86400,
    )
    return resp


@public_router.get("/api/thin-client/self", response_model=ThinClientSelfOut)
def thin_client_self(request: Request, db: DbSession):
    """The thin client this browser runs on (404 on an ordinary browser)."""
    client = tc.client_from_bind_cookie(db, request.cookies.get(tc.BIND_COOKIE))
    if client is None:
        raise HTTPException(status_code=404, detail="Not a thin client")
    policy = tc.load_policy(client)
    return ThinClientSelfOut(
        public_id=client.public_id,
        name=client.name,
        auto_open=policy.auto_open,
        pinned_workspace_id=policy.pinned_workspace_id,
        signout_minutes=policy.signout_minutes,
        screen_off_minutes=policy.screen_off_minutes,
    )
