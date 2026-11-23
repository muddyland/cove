"""The workspace an account opens right after signing in.

The SPA does the opening (the dashboard starts the workspace if it's halted and
goes to its stream); the server only stores the choice and checks it. Either
the user picks it in Preferences, or an admin does in Admin -> Users, and an
admin may lock it: the user then can't change it or delete that workspace.

The choice is stored as the workspace's public_id (never reused), and the
``after_delete`` listener in server.models clears it when that workspace goes.
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.models import User, Workspace


def _owned(db: Session, owner: User, public_id: str) -> Workspace:
    ws = db.scalar(select(Workspace).where(Workspace.public_id == public_id))
    if ws is None or ws.user_id != owner.id:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.auto_remove:
        raise HTTPException(
            status_code=400,
            detail="That workspace deletes itself when it stops, so it can't be the sign-in workspace",
        )
    return ws


def set_choice(db: Session, owner: User, public_id: str | None, *, locked: bool = False) -> Workspace | None:
    """Make ``public_id`` (one of ``owner``'s workspaces, or None for the
    dashboard) the account's sign-in workspace. Commits."""
    ws = _owned(db, owner, public_id) if public_id else None
    owner.auto_launch_workspace_id = ws.public_id if ws else None
    # Locking "the dashboard" would only stop the user picking a workspace.
    owner.auto_launch_locked = bool(locked and ws)
    db.commit()
    db.refresh(owner)
    return ws


def locks(owner: User, ws: Workspace) -> bool:
    """Is ``ws`` the workspace an admin locked ``owner`` to?"""
    return owner.auto_launch_locked and owner.auto_launch_workspace_id == ws.public_id


LOCKED_DETAIL = "Your administrator set this for your account, so it can't be changed here"
