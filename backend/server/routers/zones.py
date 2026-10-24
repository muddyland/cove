import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from sqlalchemy import func, select

from server.config import get_settings
from server.deps import AdminUser, CurrentUser, DbSession
from server.models import Workspace, Zone
from server.net import client_ip
from server.schemas import (
    ZoneCreate,
    ZoneEnrollTokenOut,
    ZoneHealthOut,
    ZoneListItem,
    ZoneOut,
    ZoneUpdate,
)

router = APIRouter(prefix="/api/admin/zones", tags=["zones"])

# User-facing (non-admin): a minimal list of enrolled zones for the launch and
# migrate pickers. Exposes only id + name, never endpoints or cert material.
user_router = APIRouter(prefix="/api/zones", tags=["zones"])


@user_router.get("", response_model=list[ZoneListItem])
def list_zones_for_user(user: CurrentUser, db: DbSession):
    zones = db.scalars(
        select(Zone).where(Zone.status == "enrolled").order_by(Zone.id)
    ).all()
    return [ZoneListItem(id=z.id, name=z.name) for z in zones]


def _audit(db, action, *, detail=None, user=None, request=None):
    from server.main import record_audit

    ip = client_ip(request) if request is not None else None
    record_audit(db, action, detail=detail, user=user, ip=ip)


def refresh_agent_dind(db, zone: Zone) -> "bool | None":
    """Ask a remote zone's agent whether its host allows the DinD sidecar and
    remember the answer on the zone row. Returns it (None when the agent can't
    be asked, or is too old to say). Best-effort, short timeout."""
    from server.docker_manager import _zone_has_mtls, zone_agent_client

    if not zone.id or not _zone_has_mtls(zone):
        return None
    try:
        with zone_agent_client(zone, timeout=5) as c:
            r = c.get("/agent/health")
            if r.status_code != 200:
                return zone.agent_allows_docker
            value = r.json().get("dind")
    except Exception:
        return zone.agent_allows_docker
    value = bool(value) if value is not None else None
    if zone.agent_allows_docker != value:
        zone.agent_allows_docker = value
        db.commit()
    return value


def _agent_current(zone_id: int) -> "bool | None":
    from server import agent_update

    return agent_update.agent_current(zone_id)


def _clean_zone_render_node(raw: "str | None") -> "str | None":
    """Empty clears the override; anything else must be a /dev/dri node path (it
    is interpolated into a probe command and a device mapping)."""
    import re

    value = (raw or "").strip()
    if not value:
        return None
    if not re.fullmatch(r"/dev/dri/[A-Za-z0-9_-]+", value):
        raise HTTPException(status_code=400, detail="Render node must look like /dev/dri/renderD128")
    return value


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


@router.get("/{zone_id}/health", response_model=ZoneHealthOut)
def zone_health(zone_id: int, admin: AdminUser, db: DbSession):
    """Live check of a zone's Docker daemon: reachable, round-trip time, engine
    version and running container count. Zone 0 is this host's own daemon,
    which has no heartbeat/last-seen of its own, so this is its only signal."""
    import time

    zone = db.get(Zone, zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    if zone_id != 0 and zone.status != "enrolled":
        return ZoneHealthOut(ok=False, error="Not enrolled")
    from server.docker_manager import get_docker_manager

    try:
        client = get_docker_manager(zone_id)._client
        t0 = time.monotonic()
        version = client.version()
        latency = int((time.monotonic() - t0) * 1000)
        running = None
        try:
            running = int(client.info().get("ContainersRunning"))
        except Exception:
            pass
        return ZoneHealthOut(
            ok=True,
            latency_ms=latency,
            docker_version=version.get("Version"),
            containers_running=running,
            agent_dind=refresh_agent_dind(db, zone) if zone_id != 0 else None,
            agent_current=_agent_current(zone_id) if zone_id != 0 else None,
        )
    except Exception as exc:  # unreachable daemon, TLS failure, proxy 403 …
        return ZoneHealthOut(ok=False, error=str(exc)[:300] or exc.__class__.__name__)


@router.post("", response_model=ZoneOut, status_code=status.HTTP_201_CREATED)
def create_zone(body: ZoneCreate, admin: AdminUser, db: DbSession, request: Request):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    # A zone is only usable once enrolled (mTLS). The pre-1.1 shortcut of
    # marking a manually-registered endpoint "enrolled" dialed it over plain TCP;
    # that now needs the explicit COVE_ALLOW_INSECURE_ZONES opt-in.
    insecure_ok = get_settings().allow_insecure_zones and (body.endpoint_host or "").strip()
    zone = Zone(
        name=name,
        endpoint_host=(body.endpoint_host or "").strip() or None,
        endpoint_port=body.endpoint_port,
        status="enrolled" if insecure_ok else "pending",
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
    # Rotate the relay (edge) cert alongside it.
    edge_cert, edge_key = ca.issue_cert(f"cove-edge-{zone.public_id}", is_server=False)
    zone.edge_cert_pem = edge_cert
    zone.edge_key_enc = encrypt_secret(edge_key)
    db.commit()
    reset_docker_manager(zone_id)
    _audit(db, "admin.zone.rotate_cert", detail=zone.public_id, user=admin, request=request)
    return _zone_out(db, zone)


@router.post("/{zone_id}/update-agent", status_code=status.HTTP_202_ACCEPTED)
def update_agent(
    zone_id: int, admin: AdminUser, db: DbSession, request: Request, background: BackgroundTasks
):
    """Update a zone's Cove agent in place to the control plane's current image.

    Pushes the CP's agent image to the zone over the per-zone Docker channel, then
    recreates the ``cove-agent`` container on it (via the agent's updater sidecar,
    which survives the restart). Build/redeploy the control plane first so its local
    agent image is the version you want to roll out. The agent briefly restarts.
    """
    from server import agent_update
    from server.docker_manager import _zone_has_mtls

    if zone_id == 0:
        raise HTTPException(
            status_code=400,
            detail="The local zone runs the control plane image; redeploy the control plane to update it.",
        )
    zone = db.get(Zone, zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    if not _zone_has_mtls(zone):
        raise HTTPException(status_code=409, detail="Zone is not enrolled for mTLS")

    # Pre-flight before scheduling so the admin gets an immediate, specific error:
    # an unreachable agent vs. one too old to carry the updater sidecar (those need
    # one manual redeploy/re-enroll to gain it).
    try:
        has_updater = agent_update.updater_present(zone_id)
    except Exception as exc:  # noqa: BLE001 — surface any daemon/transport failure
        raise HTTPException(status_code=502, detail=f"Zone agent unreachable: {exc}")
    if not has_updater:
        raise HTTPException(
            status_code=409,
            detail="This agent predates in-place updates. Re-enroll or redeploy it once "
            "to install the updater sidecar.",
        )
    try:
        agent_update.agent_image()
    except agent_update.AgentImageUnavailable as exc:
        raise HTTPException(status_code=409, detail=f"Can't update the agent: {exc}")

    background.add_task(agent_update.run_agent_update, zone_id)
    _audit(db, "admin.zone.update_agent", detail=zone.public_id, user=admin, request=request)
    return {"status": "updating", "detail": "Agent update started; the agent will briefly restart."}


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
        raise HTTPException(
            status_code=400,
            detail="The local zone cannot be edited; its GPU and Docker-in-Docker "
            "settings are in Administration → Settings.",
        )
    # Connection fields ignore null (as before); the GPU overrides take an
    # explicit null to go back to inheriting the global setting.
    sent = body.model_dump(exclude_unset=True)
    data = {k: v for k, v in sent.items() if v is not None}
    for key in ("gpu_accel", "gpu_render_node", "gpu_render_gid"):
        if key in sent:
            data[key] = sent[key]
    if "gpu_render_node" in data:
        data["gpu_render_node"] = _clean_zone_render_node(data["gpu_render_node"])
    if "name" in data:
        data["name"] = data["name"].strip()
    if "endpoint_host" in data:
        data["endpoint_host"] = (data["endpoint_host"] or "").strip() or None
    for field, value in data.items():
        setattr(zone, field, value)
    db.commit()
    db.refresh(zone)
    if data.get("docker_enabled"):
        # Show the admin straight away whether the host side is in place too.
        refresh_agent_dind(db, zone)
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
    public_id = zone.public_id
    db.delete(zone)
    db.commit()
    from server.docker_manager import _zone_cert_dir, reset_docker_manager

    reset_docker_manager(zone_id)
    # Don't leave the decrypted client/edge keys on disk for a zone that no
    # longer exists.
    import shutil

    shutil.rmtree(_zone_cert_dir(zone_id), ignore_errors=True)
    _audit(db, "admin.zone.delete", detail=public_id, user=admin, request=request)
