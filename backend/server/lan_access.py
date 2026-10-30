"""LAN-only sign-in: accounts that only work from the local network.

An admin ticks **Local network only** on an account (Admin -> Users). From then
on the account can't sign in (password, passkey, SSO), refresh a session, call
the API or open a workspace stream unless the request comes from one of the
LAN networks in Admin -> Settings. A leaked password or a stolen session
cookie is then useless from the internet.

The check fails closed:
- the client must be inside the LAN networks (an unparseable address isn't);
- a client that is itself a trusted proxy means Cove can't see past it (for
  example Traefik dropped the proxy's X-Forwarded-For), so it isn't LAN;
- if any other address the request claims for its client (X-Forwarded-For
  hops further left, CF-Connecting-IP, True-Client-IP, Forwarded) is outside
  the LAN, it isn't LAN: that's how a tunnel or proxy in front of Cove that
  the admin didn't list still gives away a visitor from the internet.

The LAN list deliberately leaves out 172.16.0.0/12 and 127.0.0.0/8 by
default: Docker's userland proxy can present outside clients (notably IPv6
ones) as the bridge gateway, which is in 172.16.0.0/12.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from server import net, settings_store
from server.models import User

DETAIL = "This account can only be used from the local network."
# Lets the SPA tell "you left the LAN" apart from any other 403.
ERROR_HEADER = {"X-Cove-Error": "lan-only"}


def request_is_lan(db: Session, request: Request) -> bool:
    lan = net.networks(settings_store.get_signin_lan_networks(db))
    client = net.parse_address(net.client_ip(request))
    if not net.in_networks(client, lan):
        return False
    if net.in_networks(client, net.trusted_proxies()):
        return False
    return all(net.in_networks(net.parse_address(a), lan) for a in net.claimed_addresses(request))


def allows(db: Session, user: Optional[User], request: Optional[Request]) -> bool:
    if user is None or not user.lan_only:
        return True
    if request is None:
        return False
    return request_is_lan(db, request)


def require(db: Session, user: User, request: Request) -> None:
    if not allows(db, user, request):
        raise HTTPException(status_code=403, detail=DETAIL, headers=ERROR_HEADER)


def describe(db: Session, request: Request) -> dict:
    """What Cove sees of this request, for the admin's network check."""
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    claimed = net.claimed_addresses(request)
    client = net.client_ip(request)
    is_lan = request_is_lan(db, request)
    warning = None
    if request.headers.get("cf-connecting-ip") or request.headers.get("true-client-ip"):
        warning = (
            "This request came through a tunnel or CDN (it carries CF-Connecting-IP or True-Client-IP). "
            "Add that proxy's address under Trusted proxies so Cove sees each visitor's own address."
        )
    elif net.in_networks(net.parse_address(client), net.trusted_proxies()):
        warning = (
            "Cove only sees a trusted proxy's address for this request: the proxy's X-Forwarded-For didn't "
            "reach Cove. Add the proxy to Traefik's forwardedHeaders.trustedIPs too (see Deployment)."
        )
    return {
        "client_ip": client,
        "is_lan": is_lan,
        "forwarded_for": hops,
        "claimed": claimed,
        "warning": warning,
    }
