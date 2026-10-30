"""Client-IP resolution for rate-limiting, audit logging and LAN-only sign-in.

The X-Forwarded-For header is a comma-separated list of hops, ordered
left-to-right as: <original client>, <proxy1>, <proxy2>, ...  Each proxy
*appends* the address it received the connection from. The leftmost entry is
fully attacker-controlled (the client can set any X-Forwarded-For it wants),
so we must NOT trust it for security decisions.

Because our trusted reverse proxy (Traefik) appends the address of whoever
connected to *it* as the last hop, the RIGHTMOST entry is the real client IP
as seen by our trusted boundary. When an admin lists **trusted proxies**
(Admin -> Settings: another reverse proxy or a tunnel in front of Cove's
Traefik), hops from those addresses are skipped from the right, so the client
is the first address that isn't one of them. Without that every user would
share the upstream proxy's address. We fall back to the direct socket peer
when no XFF header is present.

ASSUMPTION: Cove's Traefik sits in front of the app and the app is not
reachable directly. Traefik only keeps an incoming X-Forwarded-For from the
addresses in its own ``forwardedHeaders.trustedIPs`` (see docs/deployment.md),
so a proxy in front of Cove must be listed in both places.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Iterable, Optional

from fastapi import Request

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_trusted_proxies: tuple[IPNetwork, ...] = ()


def parse_networks(raw: str | Iterable[str] | None) -> list[str]:
    """Normalise a comma/space separated list of IPv4/IPv6 CIDRs. Bare
    addresses become /32 or /128; invalid entries are dropped."""
    if raw is None:
        return []
    items = re.split(r"[\s,]+", raw) if isinstance(raw, str) else list(raw)
    out: list[str] = []
    for item in items:
        item = (item or "").strip()
        if not item:
            continue
        try:
            net = ipaddress.ip_network(item, strict=False)
        except ValueError:
            continue
        text = str(net)
        if text not in out:
            out.append(text)
    return out


def networks(raw: str | Iterable[str] | None) -> tuple[IPNetwork, ...]:
    return tuple(ipaddress.ip_network(n) for n in parse_networks(raw))


def set_trusted_proxies(raw: str | Iterable[str] | None) -> None:
    """Install the admin's trusted-proxy list (called at startup and on save)."""
    global _trusted_proxies
    _trusted_proxies = networks(raw)


def trusted_proxies() -> tuple[IPNetwork, ...]:
    return _trusted_proxies


def parse_address(value: str | None) -> Optional[IPAddress]:
    """An IP from a header value: tolerates ``[v6]``, ``v4:port``, ``[v6]:port``
    and quotes (RFC 7239 ``for="[2001:db8::1]:443"``). None if it isn't one."""
    if not value:
        return None
    v = value.strip().strip('"').strip()
    if v.startswith("["):
        v = v[1:].split("]", 1)[0]
    elif v.count(":") == 1:  # IPv4 with a port
        v = v.split(":", 1)[0]
    try:
        addr = ipaddress.ip_address(v)
    except ValueError:
        return None
    # ::ffff:1.2.3.4 is an IPv4 client seen through a dual-stack socket.
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def in_networks(addr: Optional[IPAddress], nets: Iterable[IPNetwork]) -> bool:
    return addr is not None and any(addr.version == n.version and addr in n for n in nets)


def _xff_hops(request: Request) -> list[str]:
    fwd = request.headers.get("x-forwarded-for", "")
    return [h.strip() for h in fwd.split(",") if h.strip()]


def _client_index(hops: list[str]) -> int:
    """Index of the client hop: the rightmost that isn't a trusted proxy (or
    the leftmost when all of them are)."""
    for i in range(len(hops) - 1, -1, -1):
        if not in_networks(parse_address(hops[i]), _trusted_proxies):
            return i
    return 0


def client_ip(request: Request) -> str:
    """Return the real client IP (see the module docstring).

    Falls back to the direct socket peer (request.client.host), and finally
    to the literal string "unknown" when neither is available.
    """
    hops = _xff_hops(request)
    if hops:
        return hops[_client_index(hops)]
    return request.client.host if request.client else "unknown"


def claimed_addresses(request: Request) -> list[str]:
    """Every other address the request says its client has: X-Forwarded-For
    hops left of the client, and the headers tunnels and CDNs add
    (CF-Connecting-IP, True-Client-IP, Forwarded ``for=``).

    None of these can be trusted to say a request IS local (a client can set
    them), but a proxy in front of Cove that the admin didn't list as trusted
    still says here where its client really came from. So LAN-only checks
    treat any non-LAN address here as "not on the LAN": a client spoofing them
    can only lock itself out.
    """
    hops = _xff_hops(request)
    out = hops[: _client_index(hops)] if hops else []
    for name in ("cf-connecting-ip", "true-client-ip"):
        if request.headers.get(name):
            out.append(request.headers[name])
    for part in re.split(r"[,;]", request.headers.get("forwarded", "")):
        key, _, val = part.strip().partition("=")
        if key.lower() == "for" and val:
            out.append(val)
    return out
