"""Public (token-authenticated) zone enrollment endpoints.

These bootstrap mTLS, so they cannot themselves require mTLS or a Cove login —
they are guarded by a single-use enrollment token an admin mints. ``install.sh``
is fetched by a fresh host; ``/api/zones/enroll`` is called by the install script
with a locally-generated CSR and returns the CA + signed server cert.
"""

import hashlib
import ipaddress
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import select, update

from server import ca
from server.config import get_settings
from server.deps import DbSession
from server.models import Zone
from server.schemas import ZoneEnrollRequest, ZoneEnrollResponse
from server.security import encrypt_secret

router = APIRouter(tags=["enroll"])


def _find_zone_by_token(db, token: str) -> Zone | None:
    if not token:
        return None
    h = hashlib.sha256(token.encode()).hexdigest()
    return db.scalar(select(Zone).where(Zone.enroll_token_hash == h))


def _token_valid(zone: Zone | None) -> bool:
    if zone is None or zone.enroll_consumed_at is not None:
        return False
    exp = zone.enroll_token_expires_at
    if exp is not None:
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            return False
    return True


def _host_sans(host: str) -> tuple[list[str], list[str]]:
    """Split an endpoint host into (ip_addresses, dns_names) for the SAN."""
    try:
        ipaddress.ip_address(host)
        return [host], []
    except ValueError:
        return [], [host]


def _cp_base(request: Request) -> str:
    return (get_settings().app_origin or str(request.base_url)).rstrip("/")


@router.get("/install.sh", response_class=PlainTextResponse)
def install_script(token: str, request: Request, db: DbSession):
    zone = _find_zone_by_token(db, token)
    if not _token_valid(zone):
        raise HTTPException(status_code=404, detail="Invalid or expired enrollment token")
    if not zone.endpoint_host:
        raise HTTPException(
            status_code=409,
            detail="Set the zone's endpoint host (the address the control plane will dial) before enrolling.",
        )
    script = _render_install_sh(cp_url=_cp_base(request), token=token, zone=zone)
    return PlainTextResponse(script)


@router.get("/api/zones/agent-image")
def agent_image(token: str, db: DbSession):
    """Stream the configured agent image as a ``docker save`` tar, so a fresh
    agent can ``docker load`` a locally-built image with no registry. Token-gated
    (validated, not consumed) — the install script fetches this before enrolling.
    """
    zone = _find_zone_by_token(db, token)
    if not _token_valid(zone):
        raise HTTPException(status_code=403, detail="Invalid or expired enrollment token")

    from server.docker_manager import get_docker_manager

    ref = get_settings().zone_agent_image
    try:
        stream = get_docker_manager(0).save_image_stream(ref)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Control plane cannot export image '{ref}': {exc}"
        )
    return StreamingResponse(stream, media_type="application/x-tar")


@router.post("/api/zones/enroll", response_model=ZoneEnrollResponse)
def enroll(body: ZoneEnrollRequest, token: str, db: DbSession):
    zone = _find_zone_by_token(db, token)
    if not _token_valid(zone):
        raise HTTPException(status_code=403, detail="Invalid or expired enrollment token")

    # Atomic single-use consume: only the first caller flips consumed_at and
    # proceeds; a replay sees rowcount 0 and is rejected.
    now = datetime.now(timezone.utc)
    res = db.execute(
        update(Zone)
        .where(Zone.id == zone.id, Zone.enroll_consumed_at.is_(None))
        .values(enroll_consumed_at=now)
    )
    db.commit()
    if res.rowcount != 1:
        raise HTTPException(status_code=409, detail="Enrollment token already used")

    try:
        ip_addresses, dns_names = _host_sans(body.endpoint_host)
        server_cert = ca.sign_csr(
            body.csr_pem,
            f"cove-zone-{zone.public_id}",
            is_server=True,
            dns_names=dns_names,
            ip_addresses=ip_addresses,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Bad CSR: {exc}")

    # The control plane's own client cert for dialing this zone (its key stays here).
    client_cert, client_key = ca.issue_cert(f"cove-cp-{zone.public_id}", is_server=False)

    zone.endpoint_host = body.endpoint_host
    zone.endpoint_port = body.endpoint_port
    zone.ca_cert_pem = ca.ca_cert_pem()
    zone.server_cert_pem = server_cert
    zone.client_cert_pem = client_cert
    zone.client_key_enc = encrypt_secret(client_key)
    zone.agent_fingerprint = ca.cert_fingerprint(server_cert)
    zone.status = "enrolled"
    zone.enrolled_at = now
    db.commit()

    # Drop any cached (plain-TCP) client so the next call dials over mTLS.
    from server.docker_manager import reset_docker_manager

    reset_docker_manager(zone.id)

    settings = get_settings()
    return ZoneEnrollResponse(
        ca_cert_pem=zone.ca_cert_pem,
        server_cert_pem=server_cert,
        stream_signing_key=settings.get_stream_signing_key(),
        workspace_domain=settings.workspace_domain,
        expected_client_cn=f"cove-cp-{zone.public_id}",
    )


_INSTALL_TEMPLATE = r"""#!/usr/bin/env bash
set -euo pipefail

# Cove zone agent installer for zone "__ZONE_NAME__".
# Enrolls this host with the control plane. The agent exposes a SINGLE mutual-TLS
# port; the Docker daemon is reached *through* the cove-agent app (policy-filtered)
# and is never published on its own port.
CP_URL="__CP_URL__"
TOKEN="__TOKEN__"
ZONE_HOST="__ZONE_HOST__"
PORT="__PORT__"
AGENT_IMAGE="__AGENT_IMAGE__"
LOAD_IMAGE="__LOAD_IMAGE__"
STORAGE_PATH="__STORAGE_PATH__"
AGENT_DIR="/var/lib/cove-agent"
CERT_DIR="$AGENT_DIR/certs"

if [ "$(id -u)" -ne 0 ]; then echo "Please run as root (sudo)." >&2; exit 1; fi

# 1. Docker
if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker..."
  curl -fsSL https://get.docker.com | sh
fi

# 1b. Fetch the Cove agent image from the control plane (locally-built images
#     aren't in a registry). Skipped when AGENT_IMAGE is a registry ref.
if [ -n "$LOAD_IMAGE" ]; then
  echo "Loading the Cove agent image (${AGENT_IMAGE}) from the control plane..."
  curl -fsSL "$CP_URL/api/zones/agent-image?token=$TOKEN" | docker load
fi

# 2. Generate the agent's server keypair + CSR locally (private key never leaves
#    this host — only the public key, inside the CSR, is sent for signing).
mkdir -p "$CERT_DIR"; chmod 700 "$CERT_DIR"
if [ ! -f "$CERT_DIR/server.key" ]; then
  if printf '%s' "$ZONE_HOST" | grep -Eq '^[0-9.]+$'; then SAN="IP:$ZONE_HOST"; else SAN="DNS:$ZONE_HOST"; fi
  openssl req -newkey rsa:2048 -nodes \
    -keyout "$CERT_DIR/server.key" -out "$CERT_DIR/server.csr" \
    -subj "/CN=cove-zone" -addext "subjectAltName=$SAN" >/dev/null 2>&1
  chmod 600 "$CERT_DIR/server.key"
fi

# 3. Enroll: POST the CSR, receive the CA + signed server cert + provisioned
#    stream-signing key and workspace domain (so the agent can ForwardAuth).
DATA=$(CSR="$CERT_DIR/server.csr" ZH="$ZONE_HOST" PORT="$PORT" python3 - <<'PY'
import json, os
print(json.dumps({
    "csr_pem": open(os.environ["CSR"]).read(),
    "endpoint_host": os.environ["ZH"],
    "endpoint_port": int(os.environ["PORT"]),
}))
PY
)
RESP=$(curl -fsSL -X POST "$CP_URL/api/zones/enroll?token=$TOKEN" \
  -H 'Content-Type: application/json' -d "$DATA")
eval "$(CERT_DIR="$CERT_DIR" RESP="$RESP" python3 - <<'PY'
import json, os, shlex
r = json.loads(os.environ["RESP"]); d = os.environ["CERT_DIR"]
open(os.path.join(d, "ca.crt"), "w").write(r["ca_cert_pem"])
open(os.path.join(d, "server.crt"), "w").write(r["server_cert_pem"])
print("STREAM_SIGNING_KEY=" + shlex.quote(r["stream_signing_key"]))
print("WORKSPACE_DOMAIN=" + shlex.quote(r.get("workspace_domain") or ""))
print("EXPECTED_CLIENT_CN=" + shlex.quote(r["expected_client_cn"]))
PY
)"
echo "Enrolled. Writing the agent stack..."

# 4. Write the agent stack (.env + compose + Traefik dynamic config) and start it.
mkdir -p "$AGENT_DIR"
cat > "$AGENT_DIR/.env" <<ENV
PORT=${PORT}
CERT_DIR=${CERT_DIR}
AGENT_DIR=${AGENT_DIR}
AGENT_IMAGE=${AGENT_IMAGE}
STORAGE_PATH=${STORAGE_PATH}
STREAM_SIGNING_KEY=${STREAM_SIGNING_KEY}
WORKSPACE_DOMAIN=${WORKSPACE_DOMAIN}
EXPECTED_CLIENT_CN=${EXPECTED_CLIENT_CN}
ENV

cat > "$AGENT_DIR/traefik-dynamic.yml" <<'DYN'
tls:
  stores:
    default:
      defaultCertificate:
        certFile: /certs/server.crt
        keyFile: /certs/server.key
  options:
    cove-mtls:
      clientAuth:
        clientAuthType: RequireAndVerifyClientCert
        caFiles:
          - /certs/ca.crt
http:
  middlewares:
    # Forward the verified client cert's CN to cove-agent, which pins it to this
    # zone's control-plane cert (cove-cp-<id>).
    cove-clientcert:
      passTLSClientCert:
        info:
          subject:
            commonName: true
DYN

cat > "$AGENT_DIR/docker-compose.yml" <<'COMPOSE'
services:
  sockproxy:
    image: tecnativa/docker-socket-proxy
    container_name: cove-agent-sockproxy
    restart: unless-stopped
    environment: [POST=1, CONTAINERS=1, IMAGES=1, NETWORKS=1, VOLUMES=1, EXEC=1, PING=1, VERSION=1]
    volumes: ["/var/run/docker.sock:/var/run/docker.sock:ro"]
    networks: [cove-agent]
  sockproxy-ro:
    image: tecnativa/docker-socket-proxy
    container_name: cove-agent-sockproxy-ro
    restart: unless-stopped
    # Read-only set Traefik's Docker provider needs (matches the control plane).
    environment: [CONTAINERS=1, NETWORKS=1, EVENTS=1, INFO=1, VERSION=1, PING=1]
    volumes: ["/var/run/docker.sock:/var/run/docker.sock:ro"]
    networks: [cove-agent]
  cove-agent:
    image: ${AGENT_IMAGE}
    container_name: cove-agent
    restart: unless-stopped
    environment:
      - COVE_AGENT_MODE=1
      - COVE_STREAM_SIGNING_KEY=${STREAM_SIGNING_KEY}
      - COVE_WORKSPACE_DOMAIN=${WORKSPACE_DOMAIN}
      - COVE_STORAGE_PATH=${STORAGE_PATH}
      - COVE_AGENT_DOCKER_SOCKET_URL=http://cove-agent-sockproxy:2375
      - COVE_AGENT_EXPECTED_CLIENT_CN=${EXPECTED_CLIENT_CN}
    volumes:
      - "${STORAGE_PATH}:${STORAGE_PATH}"
      - cove-agent-data:/app/data
    networks: [cove-agent]
    labels:
      # Middlewares the workspace routers (created on this daemon by the control
      # plane) reference as @docker — defined here so they resolve locally.
      - traefik.enable=true
      - traefik.http.middlewares.cove-auth.forwardauth.address=http://cove-agent:8080/agent/auth/forward
      - traefik.http.middlewares.cove-auth.forwardauth.authResponseHeaders=X-Cove-User
      - traefik.http.middlewares.cove-errors.errors.status=502-504
      - traefik.http.middlewares.cove-errors.errors.service=cove-agent
      - traefik.http.middlewares.cove-errors.errors.query=/__cove_error/{status}
      # Catch-all (lowest priority): the agent API, the error page, AND the
      # policy-filtered Docker proxy all live in cove-agent. Workspace stream
      # routers (created by labels) match by host/longer path and win.
      - traefik.http.routers.cove-agent.rule=PathPrefix(`/`)
      - traefik.http.routers.cove-agent.priority=1
      - traefik.http.routers.cove-agent.entrypoints=websecure
      - traefik.http.routers.cove-agent.service=cove-agent
      - traefik.http.routers.cove-agent.middlewares=cove-clientcert@file
      # TLS + mTLS option live on the router (not the entrypoint): a host-less
      # PathPrefix(`/`) router can't take the entrypoint's default TLS option
      # (Traefik spawns a "conflicted" router without our middleware, so the
      # client-cert CN is never forwarded and cove-agent 403s every request).
      - traefik.http.routers.cove-agent.tls=true
      - traefik.http.routers.cove-agent.tls.options=cove-mtls@file
      - traefik.http.services.cove-agent.loadbalancer.server.port=8080
    depends_on: [sockproxy]
  traefik:
    image: traefik:v3.7
    container_name: cove-traefik
    restart: unless-stopped
    command:
      - --providers.docker=true
      - --providers.docker.endpoint=tcp://sockproxy-ro:2375
      - --providers.docker.exposedbydefault=false
      - --providers.docker.network=cove-agent
      - --providers.file.filename=/etc/traefik/dynamic.yml
      - --entrypoints.websecure.address=:${PORT}
      - --log.level=WARN
    environment:
      # Pin the Docker API version so Traefik's client doesn't fall back to 1.24,
      # which modern daemons reject ("client version 1.24 is too old").
      - DOCKER_API_VERSION=1.41
    ports: ["${PORT}:${PORT}"]
    volumes:
      - "${CERT_DIR}:/certs:ro"
      - "${AGENT_DIR}/traefik-dynamic.yml:/etc/traefik/dynamic.yml:ro"
    networks: [cove-agent]
    depends_on: [sockproxy-ro, cove-agent]
networks:
  cove-agent:
    name: cove-agent
volumes:
  cove-agent-data:
COMPOSE

docker compose --project-directory "$AGENT_DIR" --env-file "$AGENT_DIR/.env" up -d
echo "Zone agent ready on ${ZONE_HOST}:${PORT} (single mTLS port — streams, agent API, and Docker)."
"""


def _render_install_sh(*, cp_url: str, token: str, zone: Zone) -> str:
    settings = get_settings()
    storage_path = str(settings.storage_path) if settings.storage_path else "/var/lib/cove/workspaces"
    replacements = {
        "__CP_URL__": cp_url,
        "__TOKEN__": token,
        "__ZONE_NAME__": zone.name,
        "__ZONE_HOST__": zone.endpoint_host or "",
        "__PORT__": str(zone.endpoint_port),
        "__AGENT_IMAGE__": settings.zone_agent_image,
        "__LOAD_IMAGE__": "1" if settings.zone_agent_image_from_control_plane else "",
        "__STORAGE_PATH__": storage_path,
    }
    script = _INSTALL_TEMPLATE
    for k, v in replacements.items():
        script = script.replace(k, v)
    return script
