"""Client-IP resolution for rate-limiting and audit logging.

The X-Forwarded-For header is a comma-separated list of hops, ordered
left-to-right as: <original client>, <proxy1>, <proxy2>, ...  Each proxy
*appends* the address it received the connection from. The leftmost entry is
fully attacker-controlled (the client can set any X-Forwarded-For it wants),
so we must NOT trust it for security decisions.

Because our trusted reverse proxy (Traefik) appends the address of whoever
connected to *it* as the last hop, the RIGHTMOST entry is the real client IP
as seen by our trusted boundary. We use that, falling back to the direct
socket peer when no XFF header is present.

ASSUMPTION: exactly one trusted proxy (Traefik) sits in front of the app, and
the app is not reachable directly. If you put another proxy in front of Traefik,
or expose the app without Traefik, the rightmost hop becomes attacker-influenced
and this value (used for the login rate-limit key and audit attribution) can no
longer be trusted — adjust accordingly.
"""

from fastapi import Request


def client_ip(request: Request) -> str:
    """Return the real client IP (rightmost X-Forwarded-For hop).

    Falls back to the direct socket peer (request.client.host), and finally
    to the literal string "unknown" when neither is available.
    """
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        hops = [h.strip() for h in fwd.split(",") if h.strip()]
        if hops:
            return hops[-1]
    return request.client.host if request.client else "unknown"
