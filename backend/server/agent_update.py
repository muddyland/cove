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
import re
import socket

from docker.utils import parse_repository_tag

from server.config import get_settings

logger = logging.getLogger(__name__)

# The idle sidecar baked into the agent compose stack (see the install template in
# routers/enroll.py). Agents enrolled before it existed won't have it.
UPDATER_CONTAINER = "cove-agent-updater"
AGENT_CONTAINER = "cove-agent"

# Used only when the control plane can't find its own container (e.g. it runs
# straight from a checkout, not in Docker) and no image is configured.
_FALLBACK_AGENT_IMAGE = "cove:local"
_CONTAINER_ID_RE = re.compile(r"/containers/([0-9a-f]{64})/")

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


def get_docker_manager(zone_id: int):
    # Looked up at call time, so tests that patch server.docker_manager reach it.
    from server import docker_manager

    return docker_manager.get_docker_manager(zone_id)


class AgentImageUnavailable(RuntimeError):
    """The control plane can't find the image it should ship to zones."""


def _mountinfo() -> str:
    try:
        with open("/proc/self/mountinfo") as fh:
            return fh.read()
    except OSError:
        return ""


def _self_container(client):
    """This control plane's own container, or None when it isn't one.

    Docker sets the hostname to the short container id unless compose overrides
    it; the container's own bind mounts (/etc/hostname, resolv.conf) carry the
    full id in /proc/self/mountinfo either way.
    """
    candidates = _CONTAINER_ID_RE.findall(_mountinfo())
    candidates.append(socket.gethostname())
    for ref in dict.fromkeys(c for c in candidates if c):
        try:
            return client.containers.get(ref)
        except Exception:  # noqa: BLE001 - not a container, or not ours
            continue
    return None


def agent_image(src=None):
    """The image zones should run: ``(image, ref)`` on the control plane's daemon.

    ``COVE_ZONE_AGENT_IMAGE`` wins when set. Otherwise it's the exact image this
    control plane container runs (by id, so a newer pull of the same tag that
    hasn't been deployed yet isn't shipped), with that container's image ref.
    Before this, the default was a fixed ``cove:local``: a control plane deployed
    from a registry image kept shipping whatever stale ``cove:local`` its host
    still had, so agent updates never delivered the new agent code.
    """
    client = (src or get_docker_manager(0))._client
    configured = get_settings().zone_agent_image
    try:
        if configured:
            return client.images.get(configured), configured
        me = _self_container(client)
        if me is not None:
            ref = (me.attrs.get("Config") or {}).get("Image")
            if isinstance(ref, str) and ref:
                return client.images.get(me.attrs["Image"]), ref
        return client.images.get(_FALLBACK_AGENT_IMAGE), _FALLBACK_AGENT_IMAGE
    except Exception as exc:  # noqa: BLE001
        ref = configured or _FALLBACK_AGENT_IMAGE
        raise AgentImageUnavailable(
            f"the control plane has no agent image to ship ({ref!r}: {exc}). Set "
            "COVE_ZONE_AGENT_IMAGE to an image present on the control plane host."
        ) from exc


def agent_image_ref() -> str:
    """The agent image ref for a fresh install (best-effort, never raises)."""
    try:
        return agent_image()[1]
    except Exception:  # noqa: BLE001 - the export endpoint reports the details
        return get_settings().zone_agent_image or _FALLBACK_AGENT_IMAGE


def updater_present(zone_id: int) -> bool:
    """True if the zone's stack has the updater sidecar (and is reachable). Raises
    if the daemon can't be reached, so callers can distinguish "no updater" from
    "agent down"."""
    return get_docker_manager(zone_id).container_exists(UPDATER_CONTAINER)


def agent_current(zone_id: int) -> "bool | None":
    """Whether the zone's agent runs the image the control plane would ship.

    Compared by the image's build time, not its id: the id depends on the
    daemon's image store (the containerd store and the classic one give the same
    image different ids), the config's ``Created`` stamp doesn't. None when it
    can't be told (agent unreachable, image missing on either side).
    """
    try:
        image, _ = agent_image()
        dst = get_docker_manager(zone_id)._client
        agent = dst.containers.get(AGENT_CONTAINER)
        running = dst.images.get(agent.attrs["Image"])
        want, have = image.attrs.get("Created"), running.attrs.get("Created")
        if not isinstance(want, str) or not isinstance(have, str) or not want or not have:
            return None
        return want == have
    except Exception:  # noqa: BLE001 - best-effort status
        return None


def _tag(image, ref: str) -> None:
    if not ref or ref.startswith("sha256:"):
        return
    repo, tag = parse_repository_tag(ref)
    image.tag(repo, tag or "latest")


def run_agent_update(zone_id: int) -> None:
    """Push the control plane's agent image to the zone and trigger a recreate.

    Runs as a background task. The image is saved by id and, once loaded, tagged
    on the zone with the ref the zone's compose file runs (whatever its
    installer wrote, e.g. an old ``cove:local``) as well as the control plane's
    own ref, so the recreate picks it up without touching the zone's files. The
    recreate is dispatched detached into the updater sidecar, so this function
    returns as soon as the work is handed off — the agent restarts a moment
    later, out of band."""
    src = get_docker_manager(0)
    dst = get_docker_manager(zone_id)
    image, ref = agent_image(src)
    zone_ref = dst._client.containers.get(AGENT_CONTAINER).attrs["Config"]["Image"]

    logger.info("Agent update (zone %s): pushing %s (%s) as %s", zone_id, ref, image.id, zone_ref)
    loaded = dst.load_image(src.save_image_stream(image.id))
    if not loaded:
        raise RuntimeError("the zone's daemon loaded no image")
    for target in dict.fromkeys((zone_ref, ref)):
        _tag(loaded[0], target)

    logger.info("Agent update (zone %s): dispatching recreate via %s", zone_id, UPDATER_CONTAINER)
    dst.exec_detached(UPDATER_CONTAINER, _RECREATE_CMD)
    logger.info("Agent update (zone %s): recreate dispatched; agent will restart", zone_id)
