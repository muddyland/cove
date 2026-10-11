"""Saved VPN (Gluetun) and Tailscale profiles: provider catalogue, lookup, and
the per-profile connection lock.

A workspace names the profile it routes through (``gluetun_profile_id`` /
``tailscale_profile_id``). NULL means "the owner's first profile", which keeps
API callers that only send ``use_gluetun`` / ``use_tailscale`` working.
"""

from __future__ import annotations

from sqlalchemy import func, select

from server.models import TailscaleProfile, VpnProfile, Workspace

# Gluetun's built-in providers that work from a key or username/password alone.
# Providers whose OpenVPN setup needs client certificate/key files (CyberGhost,
# SlickVPN, VPN Unlimited, VPNSecure, AirVPN-over-OpenVPN) are left to the
# "config file" mode. ``value`` is gluetun's VPN_SERVICE_PROVIDER string.
# Source: github.com/qdm12/gluetun-wiki setup/providers/*.md
PROVIDERS: dict[str, dict] = {
    "airvpn": {"label": "AirVPN", "types": ["wireguard"], "wg_addresses": True, "wg_preshared_key": True},
    "expressvpn": {"label": "ExpressVPN", "types": ["openvpn"]},
    "fastestvpn": {"label": "FastestVPN", "types": ["wireguard", "openvpn"], "wg_addresses": True},
    "giganews": {"label": "Giganews", "types": ["openvpn"]},
    "hidemyass": {"label": "HideMyAss", "types": ["openvpn"]},
    "ipvanish": {"label": "IPVanish", "types": ["openvpn"]},
    "ivpn": {"label": "IVPN", "types": ["wireguard", "openvpn"], "wg_addresses": True},
    "mullvad": {"label": "Mullvad", "types": ["wireguard"], "wg_addresses": True},
    "nordvpn": {"label": "NordVPN", "types": ["wireguard", "openvpn"]},
    "perfect privacy": {"label": "Perfect Privacy", "types": ["openvpn"]},
    "privado": {"label": "Privado", "types": ["openvpn"]},
    "private internet access": {"label": "Private Internet Access", "types": ["openvpn"]},
    "privatevpn": {"label": "PrivateVPN", "types": ["openvpn"]},
    "protonvpn": {"label": "Proton VPN", "types": ["wireguard", "openvpn"]},
    "purevpn": {"label": "PureVPN", "types": ["openvpn"]},
    "surfshark": {"label": "Surfshark", "types": ["wireguard", "openvpn"], "wg_addresses": True},
    "torguard": {"label": "TorGuard", "types": ["openvpn"]},
    "vyprvpn": {"label": "VyprVPN", "types": ["openvpn"]},
    "windscribe": {
        "label": "Windscribe", "types": ["wireguard", "openvpn"],
        "wg_addresses": True, "wg_preshared_key": True,
    },
}


def provider_list() -> list[dict]:
    return [
        {
            "value": value,
            "label": p["label"],
            "types": p["types"],
            "wg_addresses": bool(p.get("wg_addresses")),
            "wg_preshared_key": bool(p.get("wg_preshared_key")),
        }
        for value, p in sorted(PROVIDERS.items(), key=lambda kv: kv[1]["label"].lower())
    ]


def vpn_profile_ready(p: VpnProfile) -> str | None:
    """None when the profile can start a tunnel, else what's missing."""
    if p.mode == "custom":
        return None if p.config_file else "no config file uploaded"
    if p.vpn_type == "wireguard":
        if not p.wireguard_private_key:
            return "no WireGuard private key"
        if PROVIDERS.get(p.provider or "", {}).get("wg_addresses") and not p.wireguard_addresses:
            return "no WireGuard address"
        return None
    return None if p.openvpn_user else "no OpenVPN username"


def tailscale_profile_ready(p: TailscaleProfile) -> str | None:
    return None if p.auth_key else "no auth key"


def get_vpn_profile(db, user_id: int, profile_id: int | None) -> VpnProfile | None:
    q = select(VpnProfile).where(VpnProfile.user_id == user_id)
    if profile_id is not None:
        return db.scalar(q.where(VpnProfile.id == profile_id))
    return db.scalar(q.order_by(VpnProfile.id).limit(1))


def get_tailscale_profile(db, user_id: int, profile_id: int | None) -> TailscaleProfile | None:
    q = select(TailscaleProfile).where(TailscaleProfile.user_id == user_id)
    if profile_id is not None:
        return db.scalar(q.where(TailscaleProfile.id == profile_id))
    return db.scalar(q.order_by(TailscaleProfile.id).limit(1))


def active_vpn_users(db, profile: VpnProfile, exclude_ws_id: int | None = None) -> list[Workspace]:
    """Running/starting workspaces holding this profile's VPN connection.

    A NULL profile id on a workspace means the owner's first profile, so those
    count against whichever profile is currently first."""
    first = get_vpn_profile(db, profile.user_id, None)
    cond = Workspace.gluetun_profile_id == profile.id
    if first is not None and first.id == profile.id:
        cond = cond | Workspace.gluetun_profile_id.is_(None)
    q = select(Workspace).where(
        Workspace.user_id == profile.user_id,
        Workspace.use_gluetun.is_(True),
        Workspace.status.in_(("running", "creating")),
        cond,
    )
    if exclude_ws_id is not None:
        q = q.where(Workspace.id != exclude_ws_id)
    return list(db.scalars(q).all())


def profile_usage(db, user_id: int, column, profile_id: int, is_first: bool) -> list[Workspace]:
    """Workspaces (any state) that route through the given profile."""
    cond = column == profile_id
    if is_first:
        cond = cond | column.is_(None)
    flag = Workspace.use_gluetun if column is Workspace.gluetun_profile_id else Workspace.use_tailscale
    return list(
        db.scalars(
            select(Workspace).where(Workspace.user_id == user_id, flag.is_(True), cond)
        ).all()
    )


def count_profiles(db, model, user_id: int) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.user_id == user_id)) or 0
