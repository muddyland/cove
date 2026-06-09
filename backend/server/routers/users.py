from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from server import ssh_keys
from server.deps import CurrentUser, DbSession
from server.models import UserGluetun, UserTailscale
from server.net import client_ip
from server.schemas import (
    _UNSET,
    GluetunConfigOut,
    GluetunConfigUpdate,
    SshKeyOut,
    SshKeyUpdate,
    TailscaleConfigOut,
    TailscaleConfigUpdate,
)
from server.security import encrypt_secret

# Cap the uploaded VPN config; real .ovpn / wg .conf files are a few KB.
_MAX_GLUETUN_CONFIG_BYTES = 128 * 1024
# Cap an uploaded SSH private key; even a 4096-bit RSA key is only a few KB.
_MAX_SSH_KEY_BYTES = 64 * 1024

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


def _masked_gluetun(g: UserGluetun | None) -> GluetunConfigOut:
    if g is None:
        return GluetunConfigOut(
            enabled=False,
            vpn_type="openvpn",
            has_config=False,
            config_filename=None,
            has_wireguard_private_key=False,
            has_openvpn_user=False,
            has_openvpn_password=False,
        )
    return GluetunConfigOut(
        enabled=g.enabled,
        vpn_type=g.vpn_type,
        has_config=bool(g.config_file),
        config_filename=g.config_filename,
        has_wireguard_private_key=bool(g.wireguard_private_key),
        has_openvpn_user=bool(g.openvpn_user),
        has_openvpn_password=bool(g.openvpn_password),
    )


@router.get("/me/gluetun", response_model=GluetunConfigOut)
def get_my_gluetun(user: CurrentUser, db: DbSession):
    g = db.scalar(select(UserGluetun).where(UserGluetun.user_id == user.id))
    return _masked_gluetun(g)


@router.put("/me/gluetun", response_model=GluetunConfigOut)
def update_my_gluetun(
    body: GluetunConfigUpdate, user: CurrentUser, db: DbSession, request: Request
):
    g = db.scalar(select(UserGluetun).where(UserGluetun.user_id == user.id))
    if g is None:
        g = UserGluetun(user_id=user.id)
        db.add(g)

    if body.vpn_type is not None:
        if body.vpn_type not in ("openvpn", "wireguard"):
            raise HTTPException(status_code=400, detail="vpn_type must be 'openvpn' or 'wireguard'")
        g.vpn_type = body.vpn_type

    # Sentinel semantics for the file + secrets: omitted -> unchanged; "" / null ->
    # clear; a value -> replace (encrypted at rest).
    if body.config_file != _UNSET:
        if body.config_file:
            if len(body.config_file.encode("utf-8")) > _MAX_GLUETUN_CONFIG_BYTES:
                raise HTTPException(status_code=400, detail="Config file is too large")
            g.config_file = encrypt_secret(body.config_file)
        else:
            g.config_file = None
    if body.config_filename != _UNSET:
        g.config_filename = (body.config_filename or None)
    if body.wireguard_private_key != _UNSET:
        g.wireguard_private_key = (
            encrypt_secret(body.wireguard_private_key) if body.wireguard_private_key else None
        )
    if body.openvpn_user != _UNSET:
        g.openvpn_user = encrypt_secret(body.openvpn_user) if body.openvpn_user else None
    if body.openvpn_password != _UNSET:
        g.openvpn_password = (
            encrypt_secret(body.openvpn_password) if body.openvpn_password else None
        )
    if body.enabled is not None:
        g.enabled = body.enabled

    g.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(g)

    _audit(db, "user.gluetun.update", user=user, request=request)
    return _masked_gluetun(g)


# ── SSH key (per-user) ──────────────────────────────────────────────────────────

def _masked_ssh(user) -> SshKeyOut:
    """SSH key view — never exposes the private key."""
    pub = user.ssh_public_key
    return SshKeyOut(
        has_key=bool(user.ssh_private_key),
        public_key=pub,
        key_type=user.ssh_key_type,
        fingerprint=ssh_keys.fingerprint(pub) if pub else None,
    )


def _store_ssh_key(user, parsed: ssh_keys.ParsedKey) -> None:
    user.ssh_private_key = encrypt_secret(parsed.private_key)
    user.ssh_public_key = parsed.public_key
    user.ssh_key_type = parsed.key_type


def _clear_ssh_key(user) -> None:
    user.ssh_private_key = None
    user.ssh_public_key = None
    user.ssh_key_type = None


@router.get("/me/ssh", response_model=SshKeyOut)
def get_my_ssh(user: CurrentUser, db: DbSession):
    return _masked_ssh(user)


@router.put("/me/ssh", response_model=SshKeyOut)
def update_my_ssh(body: SshKeyUpdate, user: CurrentUser, db: DbSession, request: Request):
    """Upload an existing private key, or clear it with an empty value."""
    if body.private_key:
        if len(body.private_key.encode("utf-8")) > _MAX_SSH_KEY_BYTES:
            raise HTTPException(status_code=400, detail="SSH key is too large")
        try:
            parsed = ssh_keys.parse_private_key(body.private_key, comment=f"cove:{user.username}")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        _store_ssh_key(user, parsed)
        action = "user.ssh.upload"
    else:
        _clear_ssh_key(user)
        action = "user.ssh.clear"

    db.commit()
    db.refresh(user)
    _audit(db, action, user=user, request=request)
    return _masked_ssh(user)


@router.post("/me/ssh/generate", response_model=SshKeyOut)
def generate_my_ssh(user: CurrentUser, db: DbSession, request: Request):
    """Generate a fresh Ed25519 keypair, replacing any existing key."""
    parsed = ssh_keys.generate_keypair(comment=f"cove:{user.username}")
    _store_ssh_key(user, parsed)
    db.commit()
    db.refresh(user)
    _audit(db, "user.ssh.generate", user=user, request=request)
    return _masked_ssh(user)


@router.delete("/me/ssh", response_model=SshKeyOut)
def delete_my_ssh(user: CurrentUser, db: DbSession, request: Request):
    _clear_ssh_key(user)
    db.commit()
    db.refresh(user)
    _audit(db, "user.ssh.clear", user=user, request=request)
    return _masked_ssh(user)
