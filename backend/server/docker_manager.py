import logging
import os
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from threading import Lock

import docker
import docker.errors
from sqlalchemy import select

from server.config import get_settings
from server.db import SessionLocal
from server.models import UserTailscale, Workspace, WorkspaceImage
from server.security import decrypt_secret
from server.settings_store import (
    get_tailscale_image,
    get_workspace_lan_access,
    get_workspace_max_runtime_hours,
    get_workspace_no_new_privileges,
)

logger = logging.getLogger(__name__)

# Where the helper scripts live inside the cove container (baked by the Dockerfile
# / bind-mounted from the host checkout).
_SCRIPTS_SRC_DIR = "/app/scripts"
_HELPER_SCRIPTS = ("install-proot-apps.sh", "launch-url.sh")


def _stage_helper_scripts() -> "Path":
    """Stage helper scripts into the storage tree and return that directory.

    Workspace bind-mount *sources* are resolved by the Docker daemon on the HOST,
    where the cove container's ``/app/scripts`` does not exist (the daemon would
    silently create an empty directory there and mount that instead of the script).
    The storage root, by contrast, is bind-mounted at an identical path on host
    and container, so files staged under it resolve correctly on the host.
    """
    settings = get_settings()
    base = settings.storage_path or (settings.data_dir / "workspaces")
    dest = Path(base) / ".cove-scripts"
    dest.mkdir(parents=True, exist_ok=True)
    for name in _HELPER_SCRIPTS:
        src = Path(_SCRIPTS_SRC_DIR) / name
        if src.exists():
            shutil.copyfile(src, dest / name)
            os.chmod(dest / name, 0o755)
    return dest


def _helper_script_path(name: str) -> str:
    """Host-resolvable bind-mount source for a staged helper script."""
    return str(_stage_helper_scripts() / name)


def _build_browser_cli(ws) -> str:
    """Assemble the browser CLI string (the *_CLI env value) for a URL workspace.

    Chromium/Brave flags:
      --kiosk           full-screen, locked (no context menu / shortcuts)
      --start-fullscreen full-screen but keeps the right-click menu + refresh
      --force-dark-mode --enable-features=WebContentsForceDark  force dark pages
    (Firefox ignores the Chromium-specific flags; --kiosk still applies.)
    """
    flags = []
    if ws.kiosk:
        # kiosk_menu keeps the context menu (right-click → Reload) and F5 by using
        # functional full-screen instead of the locked-down kiosk mode.
        flags.append("--start-fullscreen" if ws.kiosk_menu else "--kiosk")
    if ws.kiosk_dark:
        flags += ["--force-dark-mode", "--enable-features=WebContentsForceDark"]
    return " ".join([*flags, ws.target_url]) if flags else ws.target_url

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


def _split_packages(text: str | None) -> list[str]:
    """Parse a free-text package list into a clean list of names.

    Splits on commas and any whitespace, dropping empty tokens. Returns [] for
    None/empty/whitespace-only input.
    """
    if not text:
        return []
    return [tok for tok in re.split(r"[,\s]+", text.strip()) if tok]


def build_ts_extra_args(
    *,
    exit_node: str | None,
    accept_routes: bool,
    accept_dns: bool,
    login_server: str | None,
) -> list[str]:
    """Build the ``tailscale up`` extra args from per-workspace routing options.

    exit_node/accept_routes/accept_dns come from the Workspace; login_server
    comes from the user's UserTailscale config.

    When an exit node is set we also pass ``--exit-node-allow-lan-access``: without
    it the container routes local-subnet replies through the tunnel, so Traefik
    (on the local Docker network) can't reach the workspace's stream.
    """
    extra_args: list[str] = []
    if exit_node:
        extra_args.append(f"--exit-node={exit_node}")
        extra_args.append("--exit-node-allow-lan-access")
    if accept_routes:
        extra_args.append("--accept-routes")
    extra_args.append(f"--accept-dns={'true' if accept_dns else 'false'}")
    if login_server:
        extra_args.append(f"--login-server={login_server}")
    return extra_args


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


def delete_workspace_storage(ws) -> None:
    """Best-effort removal of a workspace's persistent home directory.

    Only deletes a path strictly *under* the configured storage base, so a
    crafted name (or a tampered ``volume_name``) can't escape it. No-op when the
    directory doesn't exist (e.g. a workspace that was never launched).
    """
    settings = get_settings()
    base = (settings.storage_path or (settings.data_dir / "workspaces")).resolve()

    # Prefer the recorded mount source; fall back to the derived path.
    if ws.volume_name:
        candidate = Path(ws.volume_name)
    elif ws.user and ws.user.username:
        candidate = base / ws.user.username / f"workspace-{_sanitize(ws.name)}"
    else:
        return

    try:
        candidate = candidate.resolve()
    except OSError:
        return

    if candidate == base or base not in candidate.parents:
        logger.warning("Refusing to purge storage outside base: %s", candidate)
        return
    if candidate.is_dir():
        shutil.rmtree(candidate, ignore_errors=True)
        logger.info("Purged workspace storage %s", candidate)


# Public resolvers used when a workspace opts into custom DNS without naming any.
_DEFAULT_PUBLIC_DNS = ["1.1.1.1", "9.9.9.9"]


def _dns_list(ws) -> list[str] | None:
    """DNS servers to apply to a workspace, or None to use the Docker default.

    When ``custom_dns`` is set, the container resolves via the listed servers
    (falling back to public defaults if none were given) instead of the local
    network's DNS.
    """
    if not getattr(ws, "custom_dns", False):
        return None
    raw = (getattr(ws, "dns_servers", None) or "").strip()
    servers = [s for s in re.split(r"[,\s]+", raw) if s]
    return servers or list(_DEFAULT_PUBLIC_DNS)


def _parse_stats(raw: dict) -> dict | None:
    """Reduce a Docker ``stats(stream=False)`` payload to CPU% + memory.

    Returns ``{cpu_pct, mem_used, mem_limit, mem_pct}`` or ``None`` if the
    payload is too incomplete to compute a CPU delta (e.g. container just
    started). Memory excludes page cache so the figure tracks real usage.
    """
    try:
        cpu = raw["cpu_stats"]
        precpu = raw["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - precpu["cpu_usage"]["total_usage"]
        system_delta = cpu.get("system_cpu_usage", 0) - precpu.get("system_cpu_usage", 0)
        online = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or []) or 1
        cpu_pct = (cpu_delta / system_delta) * online * 100.0 if system_delta > 0 else 0.0

        mem = raw["memory_stats"]
        usage = mem.get("usage", 0)
        # cgroup v2 reports inactive_file; v1 reports cache. Subtract whichever
        # exists so the number reflects active RSS rather than reclaimable cache.
        detail = mem.get("stats", {}) or {}
        cache = detail.get("inactive_file", detail.get("cache", 0))
        mem_used = max(usage - cache, 0)
        mem_limit = mem.get("limit", 0)
        mem_pct = (mem_used / mem_limit) * 100.0 if mem_limit > 0 else 0.0
    except (KeyError, TypeError, ZeroDivisionError):
        return None

    return {
        "cpu_pct": round(cpu_pct, 1),
        "mem_used": int(mem_used),
        "mem_limit": int(mem_limit),
        "mem_pct": round(mem_pct, 1),
    }


class DockerManager:
    def __init__(self):
        # Honor DOCKER_API_VERSION so the client skips version negotiation through
        # the socket proxy (which can otherwise fall back to an API version the
        # daemon rejects). Falls back to default behavior when unset.
        api_version = os.environ.get("DOCKER_API_VERSION")
        self._client = docker.from_env(version=api_version) if api_version else docker.from_env()
        self._lock = Lock()
        # Image refs with a manual pull currently in flight (for the Images UI).
        self._pulling: set[str] = set()
        self._pulling_lock = Lock()

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

    def image_present(self, ref: str) -> bool:
        """True if the image is available locally (no registry round-trip)."""
        try:
            self._client.images.get(ref)
            return True
        except (docker.errors.ImageNotFound, docker.errors.NotFound):
            return False
        except docker.errors.APIError as exc:
            logger.debug("image_present check failed for %s: %s", ref, exc)
            return False

    def remove_image(self, ref: str) -> str:
        """Delete a local image from disk.

        Returns one of:
          'removed'  — the image was deleted
          'absent'   — nothing to do (it wasn't pulled)
          'in_use'   — a container still references it (409); left in place
          'error'    — any other daemon failure
        Never raises, so callers can map the status to an HTTP response.
        """
        try:
            self._client.images.remove(ref)
            logger.info("Removed image %s", ref)
            return "removed"
        except (docker.errors.ImageNotFound, docker.errors.NotFound):
            return "absent"
        except docker.errors.APIError as exc:
            resp = getattr(exc, "response", None)
            if resp is not None and resp.status_code == 409:
                logger.info("Image %s in use, not removed", ref)
                return "in_use"
            logger.warning("Failed to remove image %s: %s", ref, exc)
            return "error"

    def is_pulling(self, ref: str) -> bool:
        with self._pulling_lock:
            return ref in self._pulling

    def pull_status(self, ref: str) -> str:
        """One of 'pulling', 'present', or 'absent'."""
        if self.is_pulling(ref):
            return "pulling"
        return "present" if self.image_present(ref) else "absent"

    def pull_image_blocking(self, ref: str) -> bool:
        """Pull an image, tracking it as in-flight. Returns True on success.

        Intended to run as a background task behind the manual-pull endpoint; the
        ``_pulling`` set lets the status endpoint report progress meanwhile.
        """
        with self._pulling_lock:
            self._pulling.add(ref)
        try:
            repo, tag = self._split_ref(ref)
            self._client.images.pull(repo, tag=tag)
            logger.info("Manually pulled image %s", ref)
            return True
        except docker.errors.APIError as exc:
            logger.warning("Manual pull failed for %s: %s", ref, exc)
            return False
        finally:
            with self._pulling_lock:
                self._pulling.discard(ref)

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
        """Create the per-workspace bridge network if it does not exist.

        IPv6 is explicitly disabled: the egress guard (_apply_egress_guard) only
        installs IPv4 iptables rules, so allowing IPv6 would let a workspace reach
        IPv6 link-local/ULA/internal/metadata addresses unfiltered, bypassing the
        WAN-only (LAN-block) policy.
        """
        try:
            self._client.networks.get(network_name)
        except docker.errors.NotFound:
            self._client.networks.create(network_name, driver="bridge", enable_ipv6=False)
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

    def _wait_for_http_ready(self, ws_id: int, port: int, timeout: int) -> bool:
        """Wait until the workspace's web GUI actually answers on ``port``.

        The container reports "running" almost immediately, but LinuxServer init
        scripts (custom-cont-init.d — including our proot-apps / apt installs)
        block the web service from starting, so the stream would 502 if we marked
        the workspace running too early. We poll ``localhost:port`` from inside the
        workspace's network namespace using a short-lived helper container (the
        same netns trick as the egress guard), since the backend has no direct
        route onto the per-workspace network.

        Returns True once it answers, False on timeout. Fail-open (returns True)
        if the probe itself can't run, so a probe failure never blocks launches.
        """
        target = f"cove-ws-{ws_id}"
        script = (
            f"end=$(($(date +%s)+{timeout})); "
            f"while [ $(date +%s) -lt $end ]; do "
            f"curl -sf -o /dev/null http://localhost:{port}/ && exit 0; sleep 3; done; exit 1"
        )
        try:
            self._pull_image(EGRESS_GUARD_IMAGE)
            self._client.containers.run(
                EGRESS_GUARD_IMAGE,
                network_mode=f"container:{target}",
                remove=True,
                detach=False,
                entrypoint="/bin/sh",
                command=["-c", script],
            )
            return True  # exited 0 => GUI answered
        except docker.errors.ContainerError:
            logger.warning("Workspace %s web GUI not ready after %ds", ws_id, timeout)
            return False
        except Exception as exc:
            logger.warning("HTTP readiness probe failed for workspace %s: %s", ws_id, exc)
            return True  # don't block the launch on probe infrastructure issues

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
            # (e.g. CHROME_CLI / BRAVE_CLI / FIREFOX_CLI), which is appended to the
            # browser command. Assemble kiosk/full-screen + dark-mode flags.
            if ws.target_url and image.url_env:
                env[image.url_env] = _build_browser_cli(ws)
            elif ws.workspace_type == "link" and ws.target_url:
                # Legacy webtop-based link workspaces use a custom init script.
                env["LAUNCH_URL"] = ws.target_url

            volumes = {
                mount_source: {"bind": "/config", "mode": "rw"},
            }
            if ws.workspace_type == "link" and not image.url_env:
                volumes[_helper_script_path("launch-url.sh")] = {
                    "bind": "/custom-cont-init.d/99-launch-url.sh",
                    "mode": "ro",
                }

            # Per-workspace package installation (applies to both launch paths).
            self._apply_package_env(env, ws.install_packages)
            self._apply_proot_apps(env, volumes, ws.proot_apps)

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

                # Common container hardening. no-new-privileges blocks in-container
                # sudo (setuid): applied when the admin setting forces it OR the
                # workspace itself opts out of sudo.
                hardening = self._build_hardening(
                    no_new_privileges_setting=get_workspace_no_new_privileges(db),
                    allow_sudo=ws.allow_sudo,
                )

                # Custom DNS: forwarders for the workspace resolver. Only for
                # non-Tailscale workspaces — Tailscale routes DNS through tailscaled
                # (which owns the shared netns resolv.conf), so it isn't overridable.
                dns = None if ws.use_tailscale else _dns_list(ws)

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
                        **hardening,
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
                        **({"dns": dns} if dns else {}),
                        **hardening,
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

                started = self._wait_for_ready(container)
                ws = db.get(Workspace, ws_id)  # re-fetch after wait
                if not started:
                    ws.status = "error"
                    ws.error_message = "Container did not start"
                    db.commit()
                else:
                    # Fast path: give the GUI a short window to answer so simple
                    # workspaces flip to running promptly. Big package/proot
                    # installs run before the GUI starts and can take many minutes
                    # — those stay "creating" (the provisioning screen) and the
                    # status monitor promotes them once the GUI answers, so we
                    # never give up just because an install is slow.
                    if self._wait_for_http_ready(ws.id, image.internal_port, 60):
                        ws.status = "running"
                        ws.started_at = datetime.now(timezone.utc)
                        db.commit()

            except docker.errors.APIError as exc:
                logger.error("Docker error launching workspace %s: %s", ws_id, exc)
                ws = db.get(Workspace, ws_id)
                ws.status = "error"
                ws.error_message = str(exc)
                db.commit()
        finally:
            db.close()

    def get_stats(self, container_id: str) -> dict | None:
        """Live CPU/memory for one running container, or None if unavailable."""
        try:
            container = self._client.containers.get(container_id)
            if container.status != "running":
                return None
            raw = container.stats(stream=False)
        except (docker.errors.NotFound, docker.errors.APIError):
            return None
        except Exception as exc:  # defensive: never let a stats read break the API
            logger.debug("stats read failed for %s: %s", container_id[:12], exc)
            return None
        return _parse_stats(raw)

    def get_tailscale_ip(self, ws_id: int) -> str | None:
        """The tailnet IPv4 of a workspace's Tailscale sidecar, or None.

        The 100.x address lives on a tailscale interface *inside* the sidecar's
        network namespace, so Docker inspect can't see it — we ask tailscaled
        directly via ``tailscale ip -4``. Returns None until the sidecar has
        finished authenticating/joining the tailnet (or if it isn't running).
        """
        sidecar_name = self._ts_sidecar_name(ws_id)
        try:
            sidecar = self._client.containers.get(sidecar_name)
            if sidecar.status != "running":
                return None
            code, out = sidecar.exec_run(["tailscale", "ip", "-4"])
        except (docker.errors.NotFound, docker.errors.APIError):
            return None
        except Exception as exc:  # defensive: never let this break the stats API
            logger.debug("tailscale ip read failed for ws %s: %s", ws_id, exc)
            return None
        if code != 0 or not out:
            return None
        ip = out.decode(errors="ignore").strip().splitlines()
        return ip[0].strip() if ip and ip[0].strip() else None

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

    def remove_workspace(self, ws_id: int, purge_storage: bool = False) -> None:
        """Stop container and delete workspace record.

        When ``purge_storage`` is set, also delete the workspace's persistent
        home directory (otherwise it is left on disk for reuse).
        """
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
            if purge_storage:
                delete_workspace_storage(ws)
            db.delete(ws)
            db.commit()
        finally:
            db.close()

    # Max time a workspace may sit in "creating" before it's declared failed.
    # Generous because big proot-apps / package installs (a dozen+ apps, plus
    # ghcr rate-limiting) run before the GUI starts. Even if exceeded, an "error"
    # workspace is auto-recovered once its GUI finally answers (see sync below).
    _PROVISION_DEADLINE_SECONDS = 3600

    def sync_workspace_statuses(self) -> None:
        """Reconcile workspace status with reality.

        - running workspaces whose container has died -> error.
        - creating workspaces whose GUI now answers -> running (self-healing
          promotion, so slow installs that outlast the launch-time probe still
          come up); expired ones -> error.
        """
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

            # Promote provisioning workspaces once their GUI answers — and also
            # recover ones we previously marked "error" (e.g. a very large install
            # that outran the deadline but did finish and is now serving).
            pending = db.scalars(
                select(Workspace).where(Workspace.status.in_(("creating", "error")))
            ).all()
            now = datetime.now(timezone.utc)
            for ws in pending:
                if not ws.container_id:
                    continue  # launch task hasn't started the container yet
                try:
                    container = self._client.containers.get(ws.container_id)
                except docker.errors.NotFound:
                    continue  # mid-launch or genuinely gone; leave status as-is
                if container.status in ("exited", "dead"):
                    if ws.status == "creating":
                        ws.status = "error"
                        ws.error_message = "Container exited during startup"
                        db.commit()
                    continue
                if container.status != "running":
                    continue
                port = ws.image.internal_port if ws.image else 3000
                if self._wait_for_http_ready(ws.id, port, 5):
                    ws.status = "running"
                    ws.started_at = now
                    ws.error_message = None
                    db.commit()
                    continue
                # Still starting: only fail a "creating" one past the (generous)
                # deadline. An "error" one stays error until its GUI answers.
                if ws.status == "creating":
                    created = ws.created_at.replace(tzinfo=timezone.utc) if ws.created_at else now
                    if (now - created).total_seconds() > self._PROVISION_DEADLINE_SECONDS:
                        ws.status = "error"
                        ws.error_message = "Workspace did not become ready in time"
                        db.commit()
        except Exception as exc:
            logger.warning("sync_workspace_statuses error: %s", exc)
        finally:
            db.close()

    def enforce_runtime_limits(self) -> None:
        """Auto-stop running workspaces older than the configured max runtime."""
        db = self._get_db()
        try:
            from sqlalchemy import select

            hours = get_workspace_max_runtime_hours(db)
            if hours <= 0:
                return
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            running = db.scalars(
                select(Workspace).where(Workspace.status == "running")
            ).all()
            expired_ids = []
            for ws in running:
                started = ws.started_at
                if started is None:
                    continue
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                if started < cutoff:
                    expired_ids.append(ws.id)
        except Exception as exc:
            logger.warning("enforce_runtime_limits error: %s", exc)
            return
        finally:
            db.close()

        for ws_id in expired_ids:
            logger.info("Auto-stopping workspace %s (exceeded %dh runtime)", ws_id, hours)
            try:
                self.stop_workspace(ws_id)
            except Exception as exc:
                logger.warning("Auto-stop of workspace %s failed: %s", ws_id, exc)

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

        # auth_key + login_server are per-user; the routing options are per-workspace.
        # The auth_key is stored encrypted at rest; decrypt it for the sidecar.
        auth_key = decrypt_secret(ts_cfg.auth_key) if ts_cfg else None
        login_server = ts_cfg.login_server if ts_cfg else None
        extra_args = build_ts_extra_args(
            exit_node=ws.ts_exit_node,
            accept_routes=ws.ts_accept_routes,
            accept_dns=ws.ts_accept_dns,
            login_server=login_server,
        )

        environment = {
            "TS_AUTHKEY": auth_key or "",
            "TS_USERSPACE": "false",
            "TS_STATE_DIR": "/var/lib/tailscale",
            "TS_HOSTNAME": f"cove-{ws.id}",
            "TS_EXTRA_ARGS": " ".join(extra_args),
        }

        # Custom DNS is intentionally NOT applied here: Tailscale workspaces route
        # DNS through tailscaled (MagicDNS / accept-dns), which owns the netns
        # resolv.conf. Custom DNS only applies to non-Tailscale workspaces.
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
    def _build_hardening(*, no_new_privileges_setting: bool, allow_sudo: bool) -> dict:
        """Build the container hardening kwargs (cap_drop/cap_add + security_opt).

        no-new-privileges blocks in-container sudo (setuid). It is applied when the
        admin global setting forces it OR when the workspace itself does not request
        sudo. So: allow_sudo=True + setting False => sudo works; setting True =>
        sudo always disabled regardless of the workspace.
        """
        hardening: dict = {
            "cap_drop": ["ALL"],
            "cap_add": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID", "KILL"],
        }
        if no_new_privileges_setting or not allow_sudo:
            hardening["security_opt"] = ["no-new-privileges:true"]
        return hardening

    @staticmethod
    def _apply_package_env(env: dict, install_packages: str | None) -> None:
        """Wire distro packages into ``env`` via the universal-package-install Mod.

        Mutates ``env`` in place. No-op when there are no packages.
        """
        pkgs = _split_packages(install_packages)
        if not pkgs:
            return
        mod = "linuxserver/mods:universal-package-install"
        existing = env.get("DOCKER_MODS")
        env["DOCKER_MODS"] = f"{existing}|{mod}" if existing else mod
        env["INSTALL_PACKAGES"] = "|".join(pkgs)

    @staticmethod
    def _apply_proot_apps(env: dict, volumes: dict, proot_apps: str | None) -> None:
        """Wire proot-apps into ``env``/``volumes``.

        Sets PROOT_APPS (space-separated) and mounts the install init script.
        Mutates both dicts in place. No-op when there are no apps.
        """
        apps = _split_packages(proot_apps)
        if not apps:
            return
        env["PROOT_APPS"] = " ".join(apps)
        volumes[_helper_script_path("install-proot-apps.sh")] = {
            "bind": "/custom-cont-init.d/98-install-proot-apps.sh",
            "mode": "ro",
        }

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

        # Per-workspace headers middleware: the stream must be embeddable in the
        # SPA's iframe. In subdomain mode the iframe is cross-origin, so we strip
        # any upstream X-Frame-Options (which would block it) and instead allow
        # framing only from the SPA origin via CSP frame-ancestors.
        hdr = f"{name}-hdr"
        frame_ancestors = "frame-ancestors 'self'"
        if settings.app_origin:
            frame_ancestors += f" {settings.app_origin}"
        header_labels = {
            f"traefik.http.middlewares.{hdr}.headers.customResponseHeaders.X-Frame-Options": "",
            f"traefik.http.middlewares.{hdr}.headers.contentSecurityPolicy": frame_ancestors,
            f"traefik.http.middlewares.{hdr}.headers.contentTypeNosniff": "true",
        }

        base = {
            "traefik.enable": "true",
            f"traefik.http.routers.{name}.entrypoints": "web,websecure",
            f"traefik.http.routers.{name}.service": name,
            f"traefik.http.services.{name}.loadbalancer.server.port": str(image.internal_port),
            "traefik.docker.network": network_name,
            "cove.workspace_id": str(ws_id),
            "cove.user_id": str(ws.user_id),
            **header_labels,
        }

        if settings.workspace_domain:
            host = settings.workspace_host(ws.public_id)
            base[f"traefik.http.routers.{name}.rule"] = f"Host(`{host}`)"
            base[f"traefik.http.routers.{name}.middlewares"] = (
                f"cove-errors@docker,cove-auth@docker,{hdr}"
            )
            # Only request TLS termination in prod/HTTPS deployments.
            if settings.cookie_secure:
                base[f"traefik.http.routers.{name}.tls"] = "true"
            return base

        prefix = f"/workspace/{ws.public_id}"
        base[f"traefik.http.routers.{name}.rule"] = f"PathPrefix(`{prefix}/`)"
        base[f"traefik.http.routers.{name}.middlewares"] = (
            f"cove-errors@docker,cove-auth@docker,{hdr},{name}-strip"
        )
        base[f"traefik.http.middlewares.{name}-strip.stripprefix.prefixes"] = prefix
        return base


@lru_cache
def get_docker_manager() -> DockerManager:
    return DockerManager()
