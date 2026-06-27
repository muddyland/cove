import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from server.config import get_settings
from server.deps import AdminUser, DbSession
from server.models import Workspace, Zone
from server.net import client_ip
from server.schemas import ZoneCreate, ZoneEnrollTokenOut, ZoneOut, ZoneUpdate

router = APIRouter(prefix="/api/admin/zones", tags=["zones"])


def _audit(db, action, *, detail=None, user=None, request=None):
    from server.main import record_audit

    ip = client_ip(request) if request is not None else None
    record_audit(db, action, detail=detail, user=user, ip=ip)


def _zone_out(db, zone: Zone) -> ZoneOut:
    count = db.scalar(
        select(func.count()).select_from(Workspace).where(Workspace.zone_id == zone.id)
    )
    out = ZoneOut.model_validate(zone)
    out.workspace_count = count or 0
    return out


@router.get("", response_model=list[ZoneOut])
def list_zones(admin: AdminUser, db: DbSession):
    zones = db.scalars(select(Zone).order_by(Zone.id)).all()
    return [_zone_out(db, z) for z in zones]


@router.post("", response_model=ZoneOut, status_code=status.HTTP_201_CREATED)
def create_zone(body: ZoneCreate, admin: AdminUser, db: DbSession, request: Request):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    zone = Zone(
        name=name,
        endpoint_host=(body.endpoint_host or "").strip() or None,
        endpoint_port=body.endpoint_port,
        stream_port=body.stream_port,
        # A manually-registered endpoint is immediately usable; without one the
        # zone waits for enrollment (Phase 3).
        status="enrolled" if (body.endpoint_host or "").strip() else "pending",
    )
    db.add(zone)
    db.commit()
    db.refresh(zone)
    _audit(db, "admin.zone.create", detail=f"{zone.public_id}:{zone.name}", user=admin, request=request)
    return _zone_out(db, zone)


@router.post("/{zone_id}/enroll-token", response_model=ZoneEnrollTokenOut)
def mint_enroll_token(zone_id: int, admin: AdminUser, db: DbSession, request: Request):
    """Mint a single-use enrollment token + install one-liner for a zone.

    The plaintext token is shown once and only its sha256 is stored. The zone
    must have its endpoint host set (the address the control plane will dial), so
    the agent's server cert can be issued with the right SAN.
    """
    if zone_id == 0:
        raise HTTPException(status_code=400, detail="The local zone cannot be enrolled")
    zone = db.get(Zone, zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    if not zone.endpoint_host:
        raise HTTPException(
            status_code=400,
            detail="Set the zone's endpoint host (the address the control plane will dial) first",
        )

    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(
        minutes=get_settings().zone_enroll_token_minutes
    )
    zone.enroll_token_hash = hashlib.sha256(token.encode()).hexdigest()
    zone.enroll_token_expires_at = expires
    zone.enroll_consumed_at = None
    if zone.status != "enrolled":
        zone.status = "enrolling"
    db.commit()

    base = (get_settings().app_origin or str(request.base_url)).rstrip("/")
    install_command = f'curl -fsSL "{base}/install.sh?token={token}" | sudo bash'
    _audit(db, "admin.zone.enroll_token", detail=zone.public_id, user=admin, request=request)
    return ZoneEnrollTokenOut(token=token, expires_at=expires, install_command=install_command)


@router.post("/{zone_id}/rotate-client-cert", response_model=ZoneOut)
def rotate_client_cert(zone_id: int, admin: AdminUser, db: DbSession, request: Request):
    """Re-issue the control plane's mTLS client cert for a zone.

    The control plane owns its client keypair, so it can rotate without touching
    the agent: the new cert keeps the same CN (which the agent allow-lists) and is
    signed by the same CA the agent already trusts. The agent's *server* cert is
    rotated by re-enrolling instead.
    """
    from server import ca
    from server.docker_manager import _zone_has_mtls, reset_docker_manager
    from server.security import encrypt_secret

    zone = db.get(Zone, zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    if not _zone_has_mtls(zone):
        raise HTTPException(status_code=409, detail="Zone has no mTLS material to rotate")

    client_cert, client_key = ca.issue_cert(f"cove-cp-{zone.public_id}", is_server=False)
    zone.client_cert_pem = client_cert
    zone.client_key_enc = encrypt_secret(client_key)
    db.commit()
    reset_docker_manager(zone_id)
    _audit(db, "admin.zone.rotate_cert", detail=zone.public_id, user=admin, request=request)
    return _zone_out(db, zone)


@router.get("/{zone_id}", response_model=ZoneOut)
def get_zone(zone_id: int, admin: AdminUser, db: DbSession):
    zone = db.get(Zone, zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    return _zone_out(db, zone)


@router.patch("/{zone_id}", response_model=ZoneOut)
def update_zone(zone_id: int, body: ZoneUpdate, admin: AdminUser, db: DbSession, request: Request):
    zone = db.get(Zone, zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    if zone_id == 0:
        raise HTTPException(status_code=400, detail="The local zone cannot be edited")
    data = body.model_dump(exclude_none=True)
    if "name" in data:
        data["name"] = data["name"].strip()
    if "endpoint_host" in data:
        data["endpoint_host"] = (data["endpoint_host"] or "").strip() or None
    for field, value in data.items():
        setattr(zone, field, value)
    db.commit()
    db.refresh(zone)
    # The endpoint or certs may have changed — drop the cached client so the next
    # operation rebuilds it against the new target.
    from server.docker_manager import reset_docker_manager

    reset_docker_manager(zone_id)
    _audit(db, "admin.zone.update", detail=zone.public_id, user=admin, request=request)
    return _zone_out(db, zone)


@router.delete("/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_zone(zone_id: int, admin: AdminUser, db: DbSession, request: Request):
    if zone_id == 0:
        raise HTTPException(status_code=400, detail="The local zone cannot be deleted")
    zone = db.get(Zone, zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    count = db.scalar(
        select(func.count()).select_from(Workspace).where(Workspace.zone_id == zone_id)
    )
    if count:
        raise HTTPException(
            status_code=409,
            detail=f"{count} workspace(s) are pinned to this zone. Migrate or delete them first.",
        )
    db.delete(zone)
    db.commit()
    from server.docker_manager import reset_docker_manager

    reset_docker_manager(zone_id)
    _audit(db, "admin.zone.delete", detail=zone.public_id, user=admin, request=request)
