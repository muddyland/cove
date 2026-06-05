from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from sqlalchemy import select

from server.deps import AdminUser, DbSession
from server.models import AuditLog, User, Workspace
from server.schemas import (
    AdminUserCreate,
    AdminUserUpdate,
    AuditOut,
    UserOut,
    WorkspaceOut,
)
from server.security import hash_password

router = APIRouter(prefix="/api/admin", tags=["admin"])


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


@router.get("/users", response_model=list[UserOut])
def list_users(user: AdminUser, db: DbSession):
    return db.scalars(select(User).order_by(User.created_at)).all()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: AdminUserCreate, user: AdminUser, db: DbSession, request: Request):
    from sqlalchemy import select as sa_select
    existing = db.scalar(sa_select(User).where(User.username == body.username))
    if existing:
        raise HTTPException(status_code=409, detail="Username already taken")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    new_user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        auth_provider="local",
        is_admin=body.is_admin,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    _audit(db, "admin.user.create", detail=new_user.username, user=user, request=request)
    return new_user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: AdminUserUpdate, admin: AdminUser, db: DbSession, request: Request):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if body.username is not None:
        target.username = body.username
    if body.is_admin is not None:
        target.is_admin = body.is_admin
    if body.password is not None:
        if len(body.password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
        target.password_hash = hash_password(body.password)
        target.tokens_valid_from = datetime.now(timezone.utc)
    db.commit()
    db.refresh(target)
    _audit(db, "admin.user.update", detail=target.username, user=admin, request=request)
    return target


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, admin: AdminUser, db: DbSession, bg: BackgroundTasks, request: Request):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    # Stop any running workspaces
    running = db.scalars(
        select(Workspace).where(
            Workspace.user_id == user_id,
            Workspace.status.in_(["running", "creating", "stopping"]),
        )
    ).all()
    from server.docker_manager import get_docker_manager
    for ws in running:
        bg.add_task(get_docker_manager().remove_workspace, ws.id)
    username = target.username
    db.delete(target)
    db.commit()
    _audit(db, "admin.user.delete", detail=username, user=admin, request=request)


@router.get("/sessions", response_model=list[WorkspaceOut])
def list_sessions(admin: AdminUser, db: DbSession):
    workspaces = db.scalars(
        select(Workspace)
        .where(Workspace.status.in_(["running", "creating", "stopping"]))
        .order_by(Workspace.started_at.desc())
    ).all()
    return [WorkspaceOut.from_workspace(ws) for ws in workspaces]


@router.delete("/sessions/{ws_id}", status_code=status.HTTP_204_NO_CONTENT)
def kill_session(ws_id: int, admin: AdminUser, db: DbSession, bg: BackgroundTasks, request: Request):
    ws = db.get(Workspace, ws_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    _audit(db, "admin.session.kill", detail=ws.public_id, user=admin, request=request)
    from server.docker_manager import get_docker_manager
    bg.add_task(get_docker_manager().remove_workspace, ws.id)


@router.get("/audit", response_model=list[AuditOut])
def list_audit(admin: AdminUser, db: DbSession):
    return db.scalars(
        select(AuditLog).order_by(AuditLog.ts.desc(), AuditLog.id.desc()).limit(200)
    ).all()
