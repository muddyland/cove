import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select, update

from server import ssh_keys
from server.deps import CurrentUser, DbSession
from server.models import TailscaleProfile, VpnProfile, Workspace
from server.net import client_ip
from server.schemas import (
    _UNSET,
    SshKeyOut,
    SshKeyUpdate,
    TailscaleProfileIn,
    TailscaleProfileOut,
    VpnProfileIn,
    VpnProfileOut,
    VpnProviderOut,
)
from server.security import encrypt_secret
from server.vpn_profiles import (
    PROVIDERS,
    count_profiles,
    profile_usage,
    provider_list,
    tailscale_profile_ready,
    vpn_profile_ready,
)

# Cap the uploaded VPN config; real .ovpn / wg .conf files are a few KB.
_MAX_GLUETUN_CONFIG_BYTES = 128 * 1024
# Per-user cap on saved VPN / Tailscale profiles.
_MAX_PROFILES = 20
# Cap an uploaded SSH private key; even a 4096-bit RSA key is only a few KB.
_MAX_SSH_KEY_BYTES = 64 * 1024

router = APIRouter(prefix="/api/users", tags=["users"])


def _audit(db, action, *, detail=None, user=None, request=None):
    from server.main import record_audit

    ip = client_ip(request) if request is not None else None
    record_audit(db, action, detail=detail, user=user, ip=ip)


_NAME_RE = re.compile(r"[^\x00-\x1f\x7f]{1,64}")
# Server filters / WireGuard addresses go into the sidecar's environment as-is.
# Comma lists of plain words: no control characters, bounded length.
_FILTER_RE = re.compile(r"[A-Za-z0-9 .,:/_\-'()]{0,512}")


def _clean_name(name: str | None) -> str:
    name = (name or "").strip()
    if not _NAME_RE.fullmatch(name):
        raise HTTPException(status_code=400, detail="Name must be 1-64 characters")
    return name


def _check_name_free(db, model, user_id: int, name: str, exclude_id: int | None = None) -> None:
    q = select(model.id).where(model.user_id == user_id, func.lower(model.name) == name.lower())
    if exclude_id is not None:
        q = q.where(model.id != exclude_id)
    if db.scalar(q):
        raise HTTPException(status_code=409, detail=f"You already have a profile named \"{name}\"")


def _enc(value: str | None) -> str | None:
    return encrypt_secret(value) if value else None


def _clean_filter(field: str, value: str | None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    if not _FILTER_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail=f"{field} has characters that aren't allowed")
    # Normalise "a ,b,, c" -> "a,b,c" (gluetun splits on commas).
    return ",".join(part.strip() for part in value.split(",") if part.strip()) or None


def _check_login_server(login_server: str | None) -> str | None:
    login_server = (login_server or "").strip() or None
    if login_server:
        parsed = urlparse(login_server)
        # It is joined into TS_EXTRA_ARGS, which the Tailscale image splits
        # on whitespace: any space/control char would smuggle extra flags
        # (--ssh, --advertise-routes…) onto ``tailscale up``.
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or any(c.isspace() or ord(c) < 32 for c in login_server)
            or not re.fullmatch(r"[A-Za-z0-9.\-:/_%@?=&]+", login_server)
        ):
            raise HTTPException(
                status_code=400,
                detail="login_server must be a plain https:// URL with no spaces",
            )
    return login_server


# ── Tailscale profiles ──────────────────────────────────────────────────────────

def _ts_out(db, ts: TailscaleProfile, first_id: int | None) -> TailscaleProfileOut:
    used = profile_usage(db, ts.user_id, Workspace.tailscale_profile_id, ts.id, ts.id == first_id)
    return TailscaleProfileOut(
        id=ts.id,
        name=ts.name,
        has_auth_key=bool(ts.auth_key),
        login_server=ts.login_server,
        problem=tailscale_profile_ready(ts),
        used_by=sorted(w.name for w in used),
    )


def _ts_list(db, user_id: int) -> list[TailscaleProfileOut]:
    rows = db.scalars(
        select(TailscaleProfile).where(TailscaleProfile.user_id == user_id).order_by(TailscaleProfile.id)
    ).all()
    first = rows[0].id if rows else None
    return [_ts_out(db, r, first) for r in rows]


def _own_ts(db, user, profile_id: int) -> TailscaleProfile:
    ts = db.get(TailscaleProfile, profile_id)
    if ts is None or ts.user_id != user.id:
        raise HTTPException(status_code=404, detail="Tailscale profile not found")
    return ts


def _apply_ts(ts: TailscaleProfile, body: TailscaleProfileIn) -> None:
    # auth_key semantics: omitted (sentinel) -> leave unchanged; "" or null -> clear;
    # non-empty string -> replace. Stored encrypted at rest.
    if body.auth_key != _UNSET:
        ts.auth_key = _enc((body.auth_key or "").strip())
    if body.login_server != _UNSET:
        ts.login_server = _check_login_server(body.login_server)
    ts.updated_at = datetime.now(timezone.utc)


@router.get("/me/tailscale-profiles", response_model=list[TailscaleProfileOut])
def list_tailscale_profiles(user: CurrentUser, db: DbSession):
    return _ts_list(db, user.id)


@router.post("/me/tailscale-profiles", response_model=TailscaleProfileOut, status_code=201)
def create_tailscale_profile(
    body: TailscaleProfileIn, user: CurrentUser, db: DbSession, request: Request
):
    name = _clean_name(body.name)
    _check_name_free(db, TailscaleProfile, user.id, name)
    if count_profiles(db, TailscaleProfile, user.id) >= _MAX_PROFILES:
        raise HTTPException(status_code=400, detail=f"At most {_MAX_PROFILES} Tailscale profiles")
    ts = TailscaleProfile(user_id=user.id, name=name)
    _apply_ts(ts, body)
    db.add(ts)
    db.commit()
    _audit(db, "user.tailscale.create", detail=name, user=user, request=request)
    return next(p for p in _ts_list(db, user.id) if p.id == ts.id)


@router.put("/me/tailscale-profiles/{profile_id}", response_model=TailscaleProfileOut)
def update_tailscale_profile(
    profile_id: int, body: TailscaleProfileIn, user: CurrentUser, db: DbSession, request: Request
):
    ts = _own_ts(db, user, profile_id)
    if body.name is not None:
        name = _clean_name(body.name)
        _check_name_free(db, TailscaleProfile, user.id, name, exclude_id=ts.id)
        ts.name = name
    _apply_ts(ts, body)
    db.commit()
    _audit(db, "user.tailscale.update", detail=ts.name, user=user, request=request)
    return next(p for p in _ts_list(db, user.id) if p.id == ts.id)


@router.delete("/me/tailscale-profiles/{profile_id}", status_code=204)
def delete_tailscale_profile(profile_id: int, user: CurrentUser, db: DbSession, request: Request):
    ts = _own_ts(db, user, profile_id)
    out = next(p for p in _ts_list(db, user.id) if p.id == ts.id)
    if out.used_by:
        raise HTTPException(
            status_code=409,
            detail="Still used by " + ", ".join(out.used_by)
            + ". Switch those workspaces to another profile (or turn Tailscale off) first.",
        )
    name = ts.name
    # Workspaces with Tailscale switched off may still remember this profile.
    db.execute(
        update(Workspace)
        .where(Workspace.user_id == user.id, Workspace.tailscale_profile_id == ts.id)
        .values(tailscale_profile_id=None)
    )
    db.delete(ts)
    db.commit()
    _audit(db, "user.tailscale.delete", detail=name, user=user, request=request)


# ── Gluetun VPN profiles ────────────────────────────────────────────────────────

@router.get("/vpn-providers", response_model=list[VpnProviderOut])
def list_vpn_providers(user: CurrentUser):
    return provider_list()


def _vpn_out(db, g: VpnProfile, first_id: int | None) -> VpnProfileOut:
    used = profile_usage(db, g.user_id, Workspace.gluetun_profile_id, g.id, g.id == first_id)
    active = [w for w in used if w.status in ("running", "creating")]
    return VpnProfileOut(
        id=g.id,
        name=g.name,
        mode=g.mode,
        provider=g.provider,
        provider_label=PROVIDERS.get(g.provider or "", {}).get("label") if g.mode == "provider" else None,
        vpn_type=g.vpn_type,
        has_config=bool(g.config_file),
        config_filename=g.config_filename,
        has_wireguard_private_key=bool(g.wireguard_private_key),
        has_wireguard_preshared_key=bool(g.wireguard_preshared_key),
        wireguard_addresses=g.wireguard_addresses,
        has_openvpn_user=bool(g.openvpn_user),
        has_openvpn_password=bool(g.openvpn_password),
        server_countries=g.server_countries,
        server_cities=g.server_cities,
        server_hostnames=g.server_hostnames,
        problem=vpn_profile_ready(g),
        used_by=sorted(w.name for w in used),
        active_in=active[0].name if active else None,
    )


def _vpn_list(db, user_id: int) -> list[VpnProfileOut]:
    rows = db.scalars(
        select(VpnProfile).where(VpnProfile.user_id == user_id).order_by(VpnProfile.id)
    ).all()
    first = rows[0].id if rows else None
    return [_vpn_out(db, r, first) for r in rows]


def _own_vpn(db, user, profile_id: int) -> VpnProfile:
    g = db.get(VpnProfile, profile_id)
    if g is None or g.user_id != user.id:
        raise HTTPException(status_code=404, detail="VPN profile not found")
    return g


def _apply_vpn(g: VpnProfile, body: VpnProfileIn) -> None:
    if body.mode is not None:
        if body.mode not in ("custom", "provider"):
            raise HTTPException(status_code=400, detail="mode must be 'custom' or 'provider'")
        g.mode = body.mode
    if body.provider != _UNSET:
        provider = (body.provider or "").strip().lower() or None
        if provider is not None and provider not in PROVIDERS:
            raise HTTPException(status_code=400, detail=f"Unknown VPN provider: {provider}")
        g.provider = provider
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
    for field in ("wireguard_private_key", "wireguard_preshared_key", "openvpn_user", "openvpn_password"):
        value = getattr(body, field)
        if value != _UNSET:
            setattr(g, field, _enc((value or "").strip()))
    for field, label in (
        ("wireguard_addresses", "WireGuard addresses"),
        ("server_countries", "Countries"),
        ("server_cities", "Cities"),
        ("server_hostnames", "Server hostnames"),
    ):
        value = getattr(body, field)
        if value != _UNSET:
            setattr(g, field, _clean_filter(label, value))

    if g.mode == "provider":
        if not g.provider:
            raise HTTPException(status_code=400, detail="Choose a VPN provider")
        types = PROVIDERS[g.provider]["types"]
        if g.vpn_type not in types:
            raise HTTPException(
                status_code=400,
                detail=f"{PROVIDERS[g.provider]['label']} supports {' and '.join(types)} here, "
                f"not {g.vpn_type}",
            )
    g.updated_at = datetime.now(timezone.utc)


@router.get("/me/vpn-profiles", response_model=list[VpnProfileOut])
def list_vpn_profiles(user: CurrentUser, db: DbSession):
    return _vpn_list(db, user.id)


@router.post("/me/vpn-profiles", response_model=VpnProfileOut, status_code=201)
def create_vpn_profile(body: VpnProfileIn, user: CurrentUser, db: DbSession, request: Request):
    name = _clean_name(body.name)
    _check_name_free(db, VpnProfile, user.id, name)
    if count_profiles(db, VpnProfile, user.id) >= _MAX_PROFILES:
        raise HTTPException(status_code=400, detail=f"At most {_MAX_PROFILES} VPN profiles")
    g = VpnProfile(user_id=user.id, name=name, mode="custom", vpn_type="openvpn")
    _apply_vpn(g, body)
    db.add(g)
    db.commit()
    _audit(db, "user.gluetun.create", detail=name, user=user, request=request)
    return next(p for p in _vpn_list(db, user.id) if p.id == g.id)


@router.put("/me/vpn-profiles/{profile_id}", response_model=VpnProfileOut)
def update_vpn_profile(
    profile_id: int, body: VpnProfileIn, user: CurrentUser, db: DbSession, request: Request
):
    g = _own_vpn(db, user, profile_id)
    if body.name is not None:
        name = _clean_name(body.name)
        _check_name_free(db, VpnProfile, user.id, name, exclude_id=g.id)
        g.name = name
    _apply_vpn(g, body)
    db.commit()
    _audit(db, "user.gluetun.update", detail=g.name, user=user, request=request)
    return next(p for p in _vpn_list(db, user.id) if p.id == g.id)


@router.delete("/me/vpn-profiles/{profile_id}", status_code=204)
def delete_vpn_profile(profile_id: int, user: CurrentUser, db: DbSession, request: Request):
    g = _own_vpn(db, user, profile_id)
    out = next(p for p in _vpn_list(db, user.id) if p.id == g.id)
    if out.used_by:
        raise HTTPException(
            status_code=409,
            detail="Still used by " + ", ".join(out.used_by)
            + ". Switch those workspaces to another profile (or turn the VPN off) first.",
        )
    name = g.name
    db.execute(
        update(Workspace)
        .where(Workspace.user_id == user.id, Workspace.gluetun_profile_id == g.id)
        .values(gluetun_profile_id=None)
    )
    db.delete(g)
    db.commit()
    _audit(db, "user.gluetun.delete", detail=name, user=user, request=request)


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
