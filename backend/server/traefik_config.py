"""Generate Traefik dynamic configuration for workspaces on remote zones.

The central Traefik discovers *local* workspaces via the Docker provider, but it
cannot see containers on remote agent nodes. For those, the control plane serves
this config to Traefik's HTTP provider: one router + service per running remote
workspace, pointing at the agent's own Traefik over mTLS (a per-zone
serversTransport carrying the control plane's client cert).

The router rule + headers middleware mirror ``DockerManager._build_traefik_labels``
so a remote workspace routes and frames identically to a local one. ForwardAuth
(cove-auth) stays on the central edge; the agent re-checks independently.
"""

from sqlalchemy import select

from server.config import get_settings
from server.docker_manager import _zone_has_mtls, stage_zone_certs
from server.models import Workspace, Zone


def build_dynamic_config(db) -> dict:
    settings = get_settings()
    routers: dict = {}
    services: dict = {}
    transports: dict = {}
    middlewares: dict = {}

    running = db.scalars(
        select(Workspace).where(Workspace.status == "running", Workspace.zone_id != 0)
    ).all()

    mount = settings.zone_certs_mount.rstrip("/")
    for ws in running:
        zone = db.get(Zone, ws.zone_id)
        if zone is None or not zone.endpoint_host or not _zone_has_mtls(zone):
            continue
        # Ensure the client cert files exist where Traefik can read them.
        stage_zone_certs(zone)

        name = f"cove-ws-{ws.id}"
        hdr = f"{name}-hdr"
        transport = f"cove-zone-{zone.id}"

        frame_ancestors = "frame-ancestors 'self'"
        if settings.app_origin:
            frame_ancestors += f" {settings.app_origin}"
        middlewares[hdr] = {
            "headers": {
                "customResponseHeaders": {"X-Frame-Options": ""},
                "contentSecurityPolicy": frame_ancestors,
                "contentTypeNosniff": True,
            }
        }

        services[name] = {
            "loadBalancer": {
                "servers": [{"url": f"https://{zone.endpoint_host}:{zone.endpoint_port}"}],
                "serversTransport": transport,
            }
        }
        transports[transport] = {
            "serverName": zone.endpoint_host,
            "certificates": [
                {
                    "certFile": f"{mount}/{zone.id}/client.crt",
                    "keyFile": f"{mount}/{zone.id}/client.key",
                }
            ],
            "rootCAs": [f"{mount}/{zone.id}/ca.crt"],
        }

        if settings.workspace_domain:
            host = settings.workspace_host(ws.public_id)
            router = {
                "rule": f"Host(`{host}`)",
                "entryPoints": ["web", "websecure"],
                "service": name,
                "middlewares": ["cove-errors@docker", "cove-auth@docker", hdr],
            }
            if settings.cookie_secure:
                router["tls"] = {}
        else:
            prefix = f"/workspace/{ws.public_id}"
            strip = f"{name}-strip"
            middlewares[strip] = {"stripPrefix": {"prefixes": [prefix]}}
            router = {
                "rule": f"PathPrefix(`{prefix}/`)",
                "entryPoints": ["web", "websecure"],
                "service": name,
                "middlewares": ["cove-errors@docker", "cove-auth@docker", hdr, strip],
            }
        routers[name] = router

    return {
        "http": {
            "routers": routers,
            "services": services,
            "serversTransports": transports,
            "middlewares": middlewares,
        }
    }
