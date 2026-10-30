"""Per-user workspace caps: how many workspaces an account may have, and how
many of them may run at once.

Each cap has a deployment-wide default (Admin -> Settings, 0 = no limit) and a
per-account override (Admin -> Users): ``None`` follows the default, ``0`` means
no limit and ``N`` caps at N. Admins are never capped, and an admin acting on
someone else's workspace isn't held to that account's caps either, the same way
admins are exempt from zone limits.

A workspace counts as running from the moment it's asked to start
(``creating``) until it's asked to stop. Checks and the state change that
consumes the slot happen under a per-account lock, so two starts racing each
other can't both take the last slot. The lock is in-process: Cove runs one
uvicorn process (see the Dockerfile's CMD), so that covers every request.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server import settings_store
from server.models import User, Workspace

# States that hold a running slot. "stopping" has already given its slot back,
# so stopping one workspace frees room to start another straight away.
RUNNING_STATES = ("creating", "running")

_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


@contextmanager
def guard(user_id: int) -> Iterator[None]:
    """Serialise limit checks and the transitions they protect, per account."""
    with _locks_guard:
        lock = _locks.setdefault(user_id, threading.Lock())
    with lock:
        yield


def _effective(override: Optional[int], default: int) -> int:
    return default if override is None else max(0, override)


def caps(db: Session, user: User) -> tuple[int, int]:
    """(max workspaces, max running) for ``user``; 0 means no limit."""
    if user.is_admin:
        return 0, 0
    return (
        _effective(user.max_workspaces, settings_store.get_default_max_workspaces(db)),
        _effective(user.max_running, settings_store.get_default_max_running(db)),
    )


def usage(db: Session, user_id: int) -> tuple[int, int]:
    """(workspaces, running) the account has now."""
    total = db.scalar(select(func.count()).select_from(Workspace).where(Workspace.user_id == user_id)) or 0
    running = db.scalar(
        select(func.count())
        .select_from(Workspace)
        .where(Workspace.user_id == user_id, Workspace.status.in_(RUNNING_STATES))
    ) or 0
    return int(total), int(running)


def _exempt(owner: User, actor: User) -> bool:
    return owner.is_admin or (actor.is_admin and actor.id != owner.id)


def _who(owner: User, actor: User) -> str:
    return "You" if owner.id == actor.id else owner.username


def require_room_for_new(db: Session, owner: User, actor: User, *, starts: bool = True) -> None:
    """Refuse (409) adding a workspace past the account's caps. ``starts``: the
    new workspace starts right away (launch, clone), so it needs a running slot
    too."""
    if _exempt(owner, actor):
        return
    max_total, max_running = caps(db, owner)
    total, running = usage(db, owner.id)
    who = _who(owner, actor)
    if max_total and total >= max_total:
        raise HTTPException(
            status_code=409,
            detail=f"{who} can have at most {max_total} workspace{'s' if max_total != 1 else ''}. "
            "Delete one first, or ask an admin to raise the limit.",
        )
    if starts and max_running and running >= max_running:
        raise HTTPException(
            status_code=409,
            detail=_running_message(who, max_running),
        )


def require_room_to_start(db: Session, owner: User, actor: User) -> None:
    """Refuse (409) starting one more workspace past the running cap."""
    if _exempt(owner, actor):
        return
    _, max_running = caps(db, owner)
    if not max_running:
        return
    _, running = usage(db, owner.id)
    if running >= max_running:
        raise HTTPException(status_code=409, detail=_running_message(_who(owner, actor), max_running))


def _running_message(who: str, cap: int) -> str:
    return (
        f"{who} can run at most {cap} workspace{'s' if cap != 1 else ''} at once. "
        "Stop one first, or ask an admin to raise the limit."
    )
