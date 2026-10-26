"""Which zones a user may run workspaces on.

By default every user may use every enrolled zone. An admin can limit a user to
a list of zones (``User.zones_restricted`` + ``user_zone`` rows), for example to
keep a LAN zone for themselves. Admins always have every zone.

A limit applies wherever a workspace is placed or run: launch, clone, migrate
and start. The check is always against the workspace's *owner*, so an admin
acting on someone's workspace can't put it where its owner isn't allowed.
"""

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from server.models import User, UserZone, Zone


def allowed_zone_ids(db: Session, user: User) -> Optional[set[int]]:
    """The zone ids ``user`` may use, or None for every zone."""
    if user.is_admin or not user.zones_restricted:
        return None
    return set(db.scalars(select(UserZone.zone_id).where(UserZone.user_id == user.id)).all())


def may_use(db: Session, user: User, zone_id: int) -> bool:
    allowed = allowed_zone_ids(db, user)
    return allowed is None or zone_id in allowed


def require_zone(db: Session, owner: User, zone_id: int, *, action: str = "use") -> None:
    """403 unless ``owner`` may use ``zone_id``."""
    if may_use(db, owner, zone_id):
        return
    zone = db.get(Zone, zone_id)
    name = zone.name if zone else f"#{zone_id}"
    raise HTTPException(
        status_code=403,
        detail=f"{owner.username} isn't allowed to {action} workspaces on zone '{name}'. "
        "An admin can change that under Admin → Users.",
    )


def set_allowed(db: Session, user: User, restricted: bool, zone_ids: Optional[list[int]]) -> None:
    """Replace a user's zone limit. ``zone_ids`` is ignored when unrestricted."""
    ids = sorted(set(zone_ids or []))
    if restricted and ids:
        known = set(db.scalars(select(Zone.id).where(Zone.id.in_(ids))).all())
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown zone id(s): {unknown}")
    user.zones_restricted = restricted
    db.execute(delete(UserZone).where(UserZone.user_id == user.id))
    if restricted:
        db.add_all(UserZone(user_id=user.id, zone_id=i) for i in ids)
