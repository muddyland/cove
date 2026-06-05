from datetime import datetime, timezone

from fastapi import APIRouter, Request
from sqlalchemy import select

from server.deps import CurrentUser, DbSession
from server.models import UserTailscale
from server.schemas import _UNSET, TailscaleConfigOut, TailscaleConfigUpdate

router = APIRouter(prefix="/api/users", tags=["users"])


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        first = fwd.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _audit(db, action, *, detail=None, user=None, request=None):
    from server.main import record_audit

    ip = _client_ip(request) if request is not None else None
    record_audit(db, action, detail=detail, user=user, ip=ip)


def _masked(ts: UserTailscale | None) -> TailscaleConfigOut:
    if ts is None:
        return TailscaleConfigOut(
            enabled=False,
            has_auth_key=False,
            login_server=None,
            exit_node=None,
            accept_routes=True,
            accept_dns=True,
        )
    return TailscaleConfigOut(
        enabled=ts.enabled,
        has_auth_key=bool(ts.auth_key),
        login_server=ts.login_server,
        exit_node=ts.exit_node,
        accept_routes=ts.accept_routes,
        accept_dns=ts.accept_dns,
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
    # non-empty string -> replace.
    if body.auth_key != _UNSET:
        ts.auth_key = body.auth_key if body.auth_key else None

    if body.login_server is not None:
        ts.login_server = body.login_server or None
    if body.exit_node is not None:
        ts.exit_node = body.exit_node or None
    if body.accept_routes is not None:
        ts.accept_routes = body.accept_routes
    if body.accept_dns is not None:
        ts.accept_dns = body.accept_dns
    if body.enabled is not None:
        ts.enabled = body.enabled

    ts.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(ts)

    _audit(db, "user.tailscale.update", user=user, request=request)
    return _masked(ts)
