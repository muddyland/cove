import logging
import os
import re
import time
from datetime import datetime, timezone
from functools import lru_cache
from threading import Lock

import docker
import docker.errors
from sqlalchemy import select

from server.config import get_settings
from server.db import SessionLocal
from server.models import UserTailscale, Workspace, WorkspaceImage
from server.settings_store import get_tailscale_image, get_workspace_lan_access

logger = logging.getLogger(__name__)

_LAUNCH_URL_SCRIPT_HOST_PATH = "/app/scripts/launch-url.sh"

# Helper image used to apply egress firewall rules inside a workspace netns.
# netshoot ships iptables; it runs briefly and is removed immediately.
EGRESS_GUARD_IMAGE = "nicolaka/netshoot:latest"

# Private/link-local/CGNAT ranges blocked for WAN-only workspaces (by destination).
_PRIVATE_RANGES = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "100.64.0.0/10",
]


def _sanitize(name: str) -> str:
    """Make a name safe for use as a directory component."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", name).strip("-").lower() or "workspace"


def _resolve_mount(username: str, ws_name: str, user_id: int) -> tuple[str, bool]:
    """Return (mount_source, is_bind_mount).

    If COVE_STORAGE_PATH is set, creates {path}/{username}/workspace-{name}/ on
    the host and returns it as a bind mount source (same absolute path inside the
    container — the user must mount it at the same path in docker-compose.yml).

    If unset, falls back to a path inside /app/data (already mounted) so no
    extra volume entry is needed.
    """
    settings = get_settings()
    base = settings.storage_path or (settings.data_dir / "workspaces")
    host_path = base / username / f"workspace-{_sanitize(ws_name)}"
    host_path.mkdir(parents=True, exist_ok=True)
    return str(host_path), True


class DockerManager:
    def __init__(self):
        # Honor DOCKER_API_VERSION so the client skips version negotiation through
        # the socket proxy (which can otherwise fall back to an API version the
        # daemon rejects). Falls back to default behavior when unset.
        api_version = os.environ.get("DOCKER_API_VERSION")
        self._client = docker.from_env(version=api_version) if api_version else docker.from_env()
        self._lock = Lock()

    def _get_db(self):
        return SessionLocal()

    def _ensure_named_volume(self, volume_name: str) -> None:
        try:
            self._client.volumes.get(volume_name)
        except docker.errors.NotFound:
            self._client.volumes.create(name=volume_name)
            logger.info("Created volume %s", volume_name)

    @staticmethod
    def _split_ref(ref: str) -> tuple[str, str | None]:
        """Split an image reference into (repository, tag). A ':' only counts as a
        tag separator when it's after the final '/' (so registry:port is safe)."""
        last = ref.rsplit("/", 1)[-1]
        if ":" in last:
            repo, tag = ref.rsplit(":", 1)
            return repo, tag
        return ref, None

    def _pull_image(self, ref: str) -> None:
        """Always pull so a restarted workspace gets the latest image. Best-effort:
        if the pull fails (e.g. offline) we fall back to any local copy."""
        repo, tag = self._split_ref(ref)
        try:
            self._client.images.pull(repo, tag=tag)
            logger.info("Pulled image %s", ref)
        except docker.errors.APIError as exc:
            logger.warning("Could not pull %s (%s); using local image if present", ref, exc)

    @staticmethod
    def _ws_network_name(ws_id: int) -> str:
        """Name of the per-workspace isolated network."""
        return f"cove-ws-net-{ws_id}"

    @staticmethod
    def _ts_sidecar_name(ws_id: int) -> str:
        """Name of the per-workspace Tailscale sidecar container."""
        return f"cove-ts-{ws_id}"

    @staticmethod
    def _ts_volume_name(ws_id: int) -> str:
        """Name of the per-workspace Tailscale state volume."""
        return f"cove-ts-state-{ws_id}"

    def _ensure_ws_network(self, network_name: str) -> None:
        """Create the per-workspace bridge network if it does not exist."""
        try:
            self._client.networks.get(network_name)
        except docker.errors.NotFound:
            self._client.networks.create(network_name, driver="bridge")
            logger.info("Created network %s", network_name)

    def _cleanup_ws_network(self, network_name: str) -> None:
        """Disconnect Traefik from the per-workspace network and remove it.

        Only removes the network once no workspace container remains attached.
        All steps are best-effort and never raise.
        """
        settings = get_settings()
        try:
            net = self._client.networks.get(network_name)
        except (docker.errors.NotFound, docker.errors.APIError):
            return

        try:
            net.disconnect(settings.traefik_container, force=True)
        except docker.errors.APIError:
            pass

        # Only remove the network if no containers remain on it.
        try:
            net.reload()
            if net.attrs.get("Containers"):
                return
        except docker.errors.APIError:
            pass

        try:
            net.remove()
        except docker.errors.APIError:
            pass

    def _cleanup_tailscale_sidecar(self, ws_id: int) -> None:
        """Stop+remove the Tailscale sidecar and its state volume. Best-effort."""
        sidecar_name = self._ts_sidecar_name(ws_id)
        try:
            sidecar = self._client.containers.get(sidecar_name)
            try:
                sidecar.stop(timeout=10)
            except docker.errors.APIError:
                pass
            sidecar.remove(force=True)
        except (docker.errors.NotFound, docker.errors.APIError):
            pass

        try:
            vol = self._client.volumes.get(self._ts_volume_name(ws_id))
            vol.remove(force=True)
        except (docker.errors.NotFound, docker.errors.APIError):
            pass

    def _wait_for_ready(self, container, timeout: int = 120) -> bool:
        """Docker-API-only readiness check (no workspace network access).

        Ready when the container is "running" and, if it defines a healthcheck,
        once health is "healthy". For containers without a healthcheck, ready
        once running plus a short grace period.
        """
        deadline = time.time() + timeout
        grace = 5
        while time.time() < deadline:
            try:
                container.reload()
            except docker.errors.NotFound:
                return False
            except docker.errors.APIError:
                time.sleep(2)
                continue

            state = container.attrs.get("State", {})
            status = state.get("Status")
            if status in ("exited", "dead"):
                return False
            if status != "running":
                time.sleep(2)
                continue

            health = state.get("Health")
            if health:
                if health.get("Status") == "healthy":
                    return True
                if health.get("Status") == "unhealthy":
                    return False
                time.sleep(2)
                continue

            # No healthcheck defined: running + short grace is enough.
            time.sleep(grace)
            try:
                container.reload()
            except docker.errors.NotFound:
                return False
            return container.attrs.get("State", {}).get("Status") == "running"
        return False

    def launch_workspace(self, ws_id: int) -> None:
        db = self._get_db()
        try:
            ws = db.get(Workspace, ws_id)
            if not ws:
                return
            image: WorkspaceImage = ws.image

            settings = get_settings()
            container_name = f"cove-ws-{ws.id}"
            net_name = self._ws_network_name(ws.id)

            self._ensure_ws_network(net_name)

            mount_source, _ = _resolve_mount(ws.user.username, ws.name, ws.user_id)

            env = {
                "PUID": str(settings.workspace_puid),
                "PGID": str(settings.workspace_pgid),
                "TZ": settings.workspace_tz,
            }
            # Browser images open a startup URL via their native CLI env var
            # (e.g. CHROME_CLI / BRAVE_CLI / FIREFOX_CLI).
            if ws.target_url and image.url_env:
                env[image.url_env] = ws.target_url
            elif ws.workspace_type == "link" and ws.target_url:
                # Legacy webtop-based link workspaces use a custom init script.
                env["LAUNCH_URL"] = ws.target_url

            volumes = {
                mount_source: {"bind": "/config", "mode": "rw"},
            }
            if ws.workspace_type == "link" and not image.url_env:
                volumes[_LAUNCH_URL_SCRIPT_HOST_PATH] = {
                    "bind": "/custom-cont-init.d/99-launch-url.sh",
                    "mode": "ro",
                }

            labels = self._build_traefik_labels(ws, image, net_name)

            try:
                # Remove any stale container with the same name
                try:
                    old = self._client.containers.get(container_name)
                    old.remove(force=True)
                except docker.errors.NotFound:
                    pass

                # Always pull so a halted-then-restarted workspace gets the latest.
                self._pull_image(image.docker_image)

                if ws.use_tailscale:
                    ts_cfg = db.scalar(
                        select(UserTailscale).where(UserTailscale.user_id == ws.user_id)
                    )
                    ts_image = get_tailscale_image(db)
                    self._launch_tailscale_sidecar(
                        ws, image, net_name, labels, ts_cfg, ts_image
                    )
                    # The workspace container shares the sidecar's netns. No own
                    # network, no traefik labels (the sidecar carries routing).
                    container = self._client.containers.run(
                        image.docker_image,
                        name=container_name,
                        detach=True,
                        environment=env,
                        volumes=volumes,
                        network_mode=f"container:{self._ts_sidecar_name(ws.id)}",
                        shm_size="1g",
                        security_opt=["no-new-privileges:true"],
                        cap_drop=["ALL"],
                        cap_add=[
                            "CHOWN",
                            "DAC_OVERRIDE",
                            "FOWNER",
                            "SETGID",
                            "SETUID",
                            "KILL",
                        ],
                    )
                else:
                    container = self._client.containers.run(
                        image.docker_image,
                        name=container_name,
                        detach=True,
                        environment=env,
                        volumes=volumes,
                        labels=labels,
                        network=net_name,
                        shm_size="1g",
                        security_opt=["no-new-privileges:true"],
                        cap_drop=["ALL"],
                        cap_add=[
                            "CHOWN",
                            "DAC_OVERRIDE",
                            "FOWNER",
                            "SETGID",
                            "SETUID",
                            "KILL",
                        ],
                    )
                logger.info("Started container %s for workspace %s", container.id[:12], ws_id)

                # Connect Traefik to this isolated network so it can route to the
                # workspace. The backend itself is never attached.
                try:
                    self._client.networks.get(net_name).connect(settings.traefik_container)
                except docker.errors.APIError:
                    # Already connected (or transient) is fine.
                    pass

                ws.container_id = container.id
                ws.container_name = container_name
                ws.volume_name = mount_source
                db.commit()

                # EGRESS GUARD: WAN-only enforcement for non-tailscale workspaces.
                # Tailscale workspaces route through the sidecar and are skipped.
                if not ws.use_tailscale and not get_workspace_lan_access(db):
                    self._apply_egress_guard(ws.id)

                ready = self._wait_for_ready(container)
                ws = db.get(Workspace, ws_id)  # re-fetch after wait
                if ready:
                    ws.status = "running"
                    ws.started_at = datetime.now(timezone.utc)
                else:
                    ws.status = "error"
                    ws.error_message = "Workspace did not become ready in time"
                db.commit()

            except docker.errors.APIError as exc:
                logger.error("Docker error launching workspace %s: %s", ws_id, exc)
                ws = db.get(Workspace, ws_id)
                ws.status = "error"
                ws.error_message = str(exc)
                db.commit()
        finally:
            db.close()

    def stop_workspace(self, ws_id: int) -> None:
        db = self._get_db()
        try:
            ws = db.get(Workspace, ws_id)
            if not ws or not ws.container_id:
                if ws:
                    self._cleanup_tailscale_sidecar(ws.id)
                    self._cleanup_ws_network(self._ws_network_name(ws.id))
                    ws.status = "stopped"
                    ws.stopped_at = datetime.now(timezone.utc)
                    db.commit()
                return
            try:
                container = self._client.containers.get(ws.container_id)
                container.stop(timeout=10)
                container.remove()
            except docker.errors.NotFound:
                pass
            except docker.errors.APIError as exc:
                logger.warning("Error stopping container %s: %s", ws.container_id[:12], exc)

            self._cleanup_tailscale_sidecar(ws.id)
            self._cleanup_ws_network(self._ws_network_name(ws.id))

            ws.status = "stopped"
            ws.stopped_at = datetime.now(timezone.utc)
            db.commit()
        finally:
            db.close()

    def remove_workspace(self, ws_id: int) -> None:
        """Stop container and delete workspace record."""
        db = self._get_db()
        try:
            ws = db.get(Workspace, ws_id)
            if not ws:
                return
            if ws.container_id:
                try:
                    container = self._client.containers.get(ws.container_id)
                    container.stop(timeout=5)
                    container.remove()
                except (docker.errors.NotFound, docker.errors.APIError):
                    pass
            self._cleanup_tailscale_sidecar(ws.id)
            self._cleanup_ws_network(self._ws_network_name(ws.id))
            db.delete(ws)
            db.commit()
        finally:
            db.close()

    def sync_workspace_statuses(self) -> None:
        """Check running workspaces against actual container states."""
        db = self._get_db()
        try:
            from sqlalchemy import select
            running = db.scalars(
                select(Workspace).where(Workspace.status == "running")
            ).all()
            for ws in running:
                if not ws.container_id:
                    continue
                try:
                    container = self._client.containers.get(ws.container_id)
                    if container.status in ("exited", "dead"):
                        ws.status = "error"
                        ws.error_message = f"Container exited unexpectedly (status: {container.status})"
                        db.commit()
                except docker.errors.NotFound:
                    ws.status = "error"
                    ws.error_message = "Container not found"
                    db.commit()
        except Exception as exc:
            logger.warning("sync_workspace_statuses error: %s", exc)
        finally:
            db.close()

    def _apply_egress_guard(self, ws_id: int) -> None:
        """Apply WAN-only egress firewall rules inside the workspace netns.

        Runs a short-lived helper container that shares the workspace
        container's network namespace and installs iptables rules dropping
        traffic destined for RFC1918 / link-local / CGNAT ranges. Allowed:
        loopback, the embedded Docker DNS (127.0.0.11), ESTABLISHED/RELATED,
        and everything else (i.e. the public internet / WAN).

        Filtering is by DESTINATION address, so a workspace can still reach the
        WAN through its RFC1918 default gateway (the gateway is only the L2 next
        hop — the packet's destination IP is the public host, which is allowed).

        Best-effort: any failure is logged and never aborts the launch. Note
        there is a brief startup race — rules are applied just after the
        container starts running, so a packet sent in that window could slip
        through. For a home-lab egress control this is acceptable.
        """
        target = f"cove-ws-{ws_id}"
        # Build the iptables script. Drop private destinations on OUTPUT while
        # allowing loopback, embedded DNS, and established/related flows.
        rules = [
            "iptables -P OUTPUT ACCEPT",
            "iptables -A OUTPUT -o lo -j ACCEPT",
            "iptables -A OUTPUT -d 127.0.0.11 -j ACCEPT",
            "iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
        ]
        for cidr in _PRIVATE_RANGES:
            rules.append(f"iptables -A OUTPUT -d {cidr} -j DROP")
        script = " && ".join(rules)

        try:
            self._pull_image(EGRESS_GUARD_IMAGE)
            self._client.containers.run(
                EGRESS_GUARD_IMAGE,
                network_mode=f"container:{target}",
                cap_add=["NET_ADMIN"],
                remove=True,
                detach=False,
                entrypoint="/bin/sh",
                command=["-c", script],
            )
            logger.info("Applied egress guard (WAN-only) for workspace %s", ws_id)
        except Exception as exc:
            logger.warning("Egress guard failed for workspace %s: %s", ws_id, exc)

    def _launch_tailscale_sidecar(
        self,
        ws: Workspace,
        image: WorkspaceImage,
        net_name: str,
        labels: dict,
        ts_cfg: "UserTailscale | None",
        ts_image: str = "tailscale/tailscale:latest",
    ) -> None:
        """Launch the Tailscale sidecar that carries this workspace's netns + routing.

        The sidecar holds the Traefik labels; the workspace container later joins
        its network namespace via network_mode=container:<sidecar>.
        """
        sidecar_name = self._ts_sidecar_name(ws.id)
        volume_name = self._ts_volume_name(ws.id)

        # Remove any stale sidecar with the same name.
        try:
            old = self._client.containers.get(sidecar_name)
            old.remove(force=True)
        except docker.errors.NotFound:
            pass

        self._ensure_named_volume(volume_name)

        auth_key = ts_cfg.auth_key if ts_cfg else None
        extra_args: list[str] = []
        if ts_cfg:
            if ts_cfg.exit_node:
                extra_args.append(f"--exit-node={ts_cfg.exit_node}")
            if ts_cfg.accept_routes:
                extra_args.append("--accept-routes")
            extra_args.append(f"--accept-dns={'true' if ts_cfg.accept_dns else 'false'}")
            if ts_cfg.login_server:
                extra_args.append(f"--login-server={ts_cfg.login_server}")

        environment = {
            "TS_AUTHKEY": auth_key or "",
            "TS_USERSPACE": "false",
            "TS_STATE_DIR": "/var/lib/tailscale",
            "TS_HOSTNAME": f"cove-{ws.id}",
            "TS_EXTRA_ARGS": " ".join(extra_args),
        }

        self._pull_image(ts_image)
        self._client.containers.run(
            ts_image,
            name=sidecar_name,
            detach=True,
            environment=environment,
            cap_add=["NET_ADMIN"],
            devices=["/dev/net/tun:/dev/net/tun"],
            volumes={volume_name: {"bind": "/var/lib/tailscale", "mode": "rw"}},
            labels=labels,
            network=net_name,
        )
        logger.info("Started Tailscale sidecar %s for workspace %s", sidecar_name, ws.id)

    @staticmethod
    def _build_traefik_labels(
        ws: Workspace, image: WorkspaceImage, network_name: str
    ) -> dict:
        """Build Traefik labels for a workspace container.

        Router/service/middleware names use the stable, label-safe integer id;
        the public_id is only used in the user-facing route path/host.

        Subdomain mode (COVE_WORKSPACE_DOMAIN set): the app is served at ``/`` on
        its own host ``{public_id}.{domain}`` — no stripprefix. Subpath mode
        (unset): the current PathPrefix + stripprefix behavior is preserved.
        """
        settings = get_settings()
        ws_id = ws.id
        name = f"cove-ws-{ws_id}"

        if settings.workspace_domain:
            host = settings.workspace_host(ws.public_id)
            labels = {
                "traefik.enable": "true",
                f"traefik.http.routers.{name}.rule": f"Host(`{host}`)",
                f"traefik.http.routers.{name}.entrypoints": "web,websecure",
                f"traefik.http.routers.{name}.service": name,
                f"traefik.http.services.{name}.loadbalancer.server.port": str(
                    image.internal_port
                ),
                "traefik.docker.network": network_name,
                f"traefik.http.routers.{name}.middlewares": "cove-auth@docker,cove-headers@docker",
                "cove.workspace_id": str(ws_id),
                "cove.user_id": str(ws.user_id),
            }
            # Only request TLS termination in prod/HTTPS deployments.
            if settings.cookie_secure:
                labels[f"traefik.http.routers.{name}.tls"] = "true"
            return labels

        prefix = f"/workspace/{ws.public_id}"
        middlewares = f"cove-auth@docker,cove-headers@docker,{name}-strip"
        return {
            "traefik.enable": "true",
            f"traefik.http.routers.{name}.rule": f"PathPrefix(`{prefix}/`)",
            f"traefik.http.routers.{name}.entrypoints": "web,websecure",
            f"traefik.http.routers.{name}.service": name,
            f"traefik.http.services.{name}.loadbalancer.server.port": str(
                image.internal_port
            ),
            "traefik.docker.network": network_name,
            f"traefik.http.routers.{name}.middlewares": middlewares,
            f"traefik.http.middlewares.{name}-strip.stripprefix.prefixes": prefix,
            "cove.workspace_id": str(ws_id),
            "cove.user_id": str(ws.user_id),
        }


@lru_cache
def get_docker_manager() -> DockerManager:
    return DockerManager()
