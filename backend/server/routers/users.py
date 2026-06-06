from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from server.deps import CurrentUser, DbSession
from server.models import UserTailscale
from server.net import client_ip
from server.schemas import _UNSET, TailscaleConfigOut, TailscaleConfigUpdate
from server.security import encrypt_secret

router = APIRouter(prefix="/api/users", tags=["users"])


def _audit(db, action, *, detail=None, user=None, request=None):
    from server.main import record_audit

    ip = client_ip(request) if request is not None else None
    record_audit(db, action, detail=detail, user=user, ip=ip)


def _masked(ts: UserTailscale | None) -> TailscaleConfigOut:
    if ts is None:
        return TailscaleConfigOut(
            enabled=False,
            has_auth_key=False,
            login_server=None,
        )
    return TailscaleConfigOut(
        enabled=ts.enabled,
        has_auth_key=bool(ts.auth_key),
        login_server=ts.login_server,
    )


@router.get("/me/tailscale", response_model=TailscaleConfigOut)
def get_my_tailscale(user: CurrentUser, db: DbSession):
    ts = db.scalar(select(UserTailscale).where(UserTailscale.user_id == user.id))
    return _masked(ts)


@router.put("/me/tailscale", response_model=TailscaleConfigOut)
def update_my_tailscale(
    body: TailscaleConfigUpdate, user: CurrentUser, db: DbSession, request: Request
):
    ts = db.scalar(select(UserTailscale).where(UserTailscale.user_id == user.id))
    if ts is None:
        ts = UserTailscale(user_id=user.id)
        db.add(ts)

    # auth_key semantics: omitted (sentinel) -> leave unchanged; "" or null -> clear;
    # non-empty string -> replace. Stored encrypted at rest.
    if body.auth_key != _UNSET:
        ts.auth_key = encrypt_secret(body.auth_key) if body.auth_key else None

    if body.login_server is not None:
        login_server = body.login_server or None
        if login_server:
            parsed = urlparse(login_server)
            if parsed.scheme != "https" or not parsed.hostname:
                raise HTTPException(
                    status_code=400,
                    detail="login_server must be a valid https:// URL",
                )
        ts.login_server = login_server
    if body.enabled is not None:
        ts.enabled = body.enabled

    ts.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(ts)

    _audit(db, "user.tailscale.update", user=user, request=request)
    return _masked(ts)
