"""Update a zone's Cove agent in place, from the control plane.

The control plane already holds a full mTLS Docker channel to every zone (the
same one ``DockerManager`` drives workspaces over). An agent update is two steps
over that channel:

  1. **Push the image.** ``docker save`` the control plane's current agent image
     and ``docker load`` it onto the zone's daemon. The image is locally built and
     in no registry, so this mirrors what the install script's
     ``curl …/agent-image | docker load`` does — but driven from the CP.
  2. **Recreate the agent.** Boot ``cove-agent`` on the freshly loaded image.

Step 2 can't be a plain Docker restart (that reuses the old container and image)
and can't run from the control plane's own request: recreating ``cove-agent``
tears down the very policy proxy the request rides through, so the call that
issues the recreate would lose its own response path mid-flight — and could never
issue the follow-up create. So the agent stack ships an idle ``cove-agent-updater``
sidecar (the Docker CLI + the compose project + the host socket). The control
plane execs the recreate *inside the sidecar*, detached; the sidecar outlives the
``cove-agent`` restart and carries it to completion. The UI then polls the agent's
health to confirm it came back on the new image.
"""

import logging

from server.config import get_settings
from server.docker_manager import get_docker_manager

logger = logging.getLogger(__name__)

# The idle sidecar baked into the agent compose stack (see the install template in
# routers/enroll.py). Agents enrolled before it existed won't have it.
UPDATER_CONTAINER = "cove-agent-updater"

# Recreate ONLY cove-agent: --no-deps leaves sockproxy/traefik (and the updater
# itself) up, so the channel the control plane reconnects through survives;
# --force-recreate is required because the image tag is unchanged, so compose would
# otherwise see "nothing to do". The project name must match the host's
# `--project-directory /var/lib/cove-agent` (compose derives it from the directory
# basename) — NOT the in-container /agent mount path, or compose would target a
# different, empty project.
_RECREATE_CMD = [
    "docker",
    "compose",
    "-p",
    "cove-agent",
    "-f",
    "/agent/docker-compose.yml",
    "--env-file",
    "/agent/.env",
    "up",
    "-d",
    "--no-deps",
    "--force-recreate",
    "cove-agent",
]


def updater_present(zone_id: int) -> bool:
    """True if the zone's stack has the updater sidecar (and is reachable). Raises
    if the daemon can't be reached, so callers can distinguish "no updater" from
    "agent down"."""
    return get_docker_manager(zone_id).container_exists(UPDATER_CONTAINER)


def run_agent_update(zone_id: int) -> None:
    """Push the control plane's agent image to the zone and trigger a recreate.

    Runs as a background task. The recreate is dispatched detached into the updater
    sidecar, so this function returns as soon as the work is handed off — the agent
    restarts a moment later, out of band."""
    ref = get_settings().zone_agent_image
    src = get_docker_manager(0)
    dst = get_docker_manager(zone_id)

    logger.info("Agent update (zone %s): pushing image %s", zone_id, ref)
    dst.load_image(src.save_image_stream(ref))

    logger.info("Agent update (zone %s): dispatching recreate via %s", zone_id, UPDATER_CONTAINER)
    dst.exec_detached(UPDATER_CONTAINER, _RECREATE_CMD)
    logger.info("Agent update (zone %s): recreate dispatched; agent will restart", zone_id)
