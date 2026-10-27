"""Home automation integration endpoints.

* ``admin_router`` (``/api/admin/integration-tokens``) — admins mint, list and
  revoke integration tokens.
* ``router`` (``/api/integration/v1``) — what Home Assistant calls, on an
  integration token only. Thin clients are addressed by their public id, which
  survives renames, so Home Assistant entity ids stay put.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from server import integration
from server import thin_clients as tc
from server.deps import AdminUser, DbSession, get_db
from server.models import IntegrationToken, ThinClient
from server.net import client_ip
from server.schemas import (
    IntegrationCommandIn,
    IntegrationTokenCreate,
    IntegrationTokenCreated,
    IntegrationTokenOut,
    ThinClientKeepAwakeIn,
)

admin_router = APIRouter(prefix="/api/admin/integration-tokens", tags=["integration"])
router = APIRouter(prefix="/api/integration/v1", tags=["integration"])


def _audit(db, action, *, detail=None, user=None, request: Request | None = None):
    from server.main import record_audit

    record_audit(db, action, detail=detail, user=user, ip=client_ip(request) if request else None)


# ── Admin ────────────────────────────────────────────────────────────────────

@admin_router.get("/home-assistant.zip")
def download_home_assistant(admin: AdminUser):
    """The Home Assistant custom component this Cove ships, to unzip into
    Home Assistant's config folder."""
    bundle = integration.home_assistant_bundle()
    if bundle is None:
        raise HTTPException(status_code=404, detail="This deployment doesn't include the Home Assistant integration")
    data, version = bundle
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="cove-home-assistant-{version}.zip"'},
    )


@admin_router.get("", response_model=list[IntegrationTokenOut])
def list_tokens(admin: AdminUser, db: DbSession):
    return db.scalars(select(IntegrationToken).order_by(IntegrationToken.id)).all()


@admin_router.post("", response_model=IntegrationTokenCreated, status_code=status.HTTP_201_CREATED)
def create_token(body: IntegrationTokenCreate, admin: AdminUser, db: DbSession, request: Request):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    token = integration.new_token()
    row = IntegrationToken(
        name=name,
        token_hash=integration.hash_token(token),
        prefix=token[: len(integration.TOKEN_PREFIX) + 4],
        scope=body.scope,
        created_by=admin.username,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _audit(db, "admin.integration_token.create", detail=f"{name} ({body.scope})", user=admin, request=request)
    return IntegrationTokenCreated(**IntegrationTokenOut.model_validate(row).model_dump(), token=token)


@admin_router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(token_id: int, admin: AdminUser, db: DbSession, request: Request):
    row = db.get(IntegrationToken, token_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Token not found")
    name = row.name
    db.delete(row)
    db.commit()
    _audit(db, "admin.integration_token.revoke", detail=name, user=admin, request=request)


# ── Integration (token-authenticated) ────────────────────────────────────────

def _token(request: Request, db: Annotated[Session, Depends(get_db)]) -> IntegrationToken:
    auth = request.headers.get("authorization") or ""
    raw = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    row = integration.find_token(db, raw)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unknown or revoked integration token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    integration.touch(db, row, client_ip(request))
    return row


def _control_token(row: Annotated[IntegrationToken, Depends(_token)]) -> IntegrationToken:
    if row.scope != "control":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This token can only read")
    return row


ReadToken = Annotated[IntegrationToken, Depends(_token)]
ControlToken = Annotated[IntegrationToken, Depends(_control_token)]


def _client(db: Session, public_id: str) -> ThinClient:
    client = db.scalar(select(ThinClient).where(ThinClient.public_id == public_id))
    if client is None or client.status != "enrolled":
        raise HTTPException(status_code=404, detail="Thin client not found")
    return client


def _by(row: IntegrationToken) -> str:
    # Shown as "created by" on the device's command list (64 chars max).
    return f"integration:{row.name}"[:64]


@router.get("/status")
def status_snapshot(token: ReadToken, db: DbSession):
    """Counters for users, workspaces and zones, and every enrolled thin
    client's state. Also says what this token may do."""
    out = integration.snapshot(db)
    out["token"] = {"name": token.name, "scope": token.scope}
    return out


@router.get("/thin-clients/{public_id}/screenshot.png")
def screenshot(public_id: str, token: ReadToken, db: DbSession):
    client = _client(db, public_id)
    if not client.screenshot_png:
        raise HTTPException(status_code=404, detail="No screenshot yet")
    return Response(content=client.screenshot_png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/thin-clients/{public_id}/commands", status_code=status.HTTP_202_ACCEPTED)
def command(public_id: str, body: IntegrationCommandIn, token: ControlToken, db: DbSession, request: Request):
    """Queue a command. Returns once it is queued; the device runs it on its
    next check-in (at once when it is online) and its effect shows up in the
    next status read. Every command is listed on the device with the token's
    name; only the disruptive ones also go to the audit log, so an automation
    that switches a screen on motion doesn't drown it."""
    client = _client(db, public_id)
    if body.kind not in integration.CONTROL_COMMANDS:
        raise HTTPException(status_code=400, detail=f"Command '{body.kind}' is not available to integrations")
    try:
        cmd = tc.queue_command(db, client, body.kind, body.args, by=_by(token))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if cmd.kind in integration.AUDITED_COMMANDS:
        detail = f"{client.name}: {cmd.kind} ({token.name})"
        _audit(db, "integration.thin_client.command", detail=detail, request=request)
    return {"id": cmd.id, "kind": cmd.kind, "args": tc.command_args(cmd)}


@router.post("/thin-clients/{public_id}/signout", status_code=status.HTTP_204_NO_CONTENT)
def signout(public_id: str, token: ControlToken, db: DbSession, request: Request):
    client = _client(db, public_id)
    tc.revoke_sessions(db, client, by=_by(token))
    _audit(db, "integration.thin_client.signout", detail=f"{client.name} ({token.name})", request=request)


@router.post("/thin-clients/{public_id}/keep-awake", status_code=status.HTTP_204_NO_CONTENT)
def keep_awake(public_id: str, body: ThinClientKeepAwakeIn, token: ControlToken, db: DbSession, request: Request):
    """Treat the device as active for ``minutes`` (0 clears it). Not audited:
    automations toggle it often (the device's own state shows it)."""
    client = _client(db, public_id)
    client.keep_awake_until = tc.now_utc() + timedelta(minutes=body.minutes) if body.minutes else None
    client.policy_version = (client.policy_version or 0) + 1
    db.commit()
    tc.notify(client.id)
