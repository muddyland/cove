"""Internal endpoints reached only by infrastructure (Traefik), not users.

Like the ForwardAuth endpoint, these are closed to public enumeration by the
``forward_auth_host`` Host check: Traefik's internal request carries the internal
authority (e.g. ``cove:8080``) as its Host, while a public request routed through
Traefik arrives with the public Host and is rejected.
"""

from fastapi import APIRouter, HTTPException, Request

from server.config import get_settings
from server.deps import DbSession
from server.traefik_config import build_dynamic_config

router = APIRouter(prefix="/api/internal", tags=["internal"])


@router.get("/traefik-config")
def traefik_config(request: Request, db: DbSession):
    """Traefik HTTP-provider dynamic config for workspaces on remote zones."""
    settings = get_settings()
    if settings.forward_auth_host and request.headers.get("host") != settings.forward_auth_host:
        raise HTTPException(status_code=404)
    return build_dynamic_config(db)
