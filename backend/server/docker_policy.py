"""Policy enforced on the Docker API at the zone agent.

The control plane reaches a zone's Docker daemon *through* the cove-agent app
(over the single mTLS port), not via a raw exposed Docker socket. The agent
inspects the requests that could hand a caller the host — ``containers/create``,
``volumes/create``, ``networks/create``, ``networks/{id}/connect``, exec-create,
``archive`` and ``rename`` — and rejects anything outside the envelope Cove's own
launch flow needs, so even a caller holding the control plane's client cert
cannot escape a container to the agent host.

The create check is an ALLOW-list: every ``HostConfig`` key must be one Cove
uses, and the dangerous ones (namespaces, capabilities, devices, mounts,
security options) are further constrained. Keys are matched case-insensitively
and duplicates are rejected, because the daemon's JSON decoder is case-insensitive
and last-duplicate-wins — a check that only looks at ``"HostConfig"`` would miss
``"hostconfig"``. Keep this in sync with ``DockerManager`` if the launch flow
gains a new primitive.
"""

import json
from pathlib import Path

from server.config import get_settings

# Capabilities Cove legitimately uses. Its launch does cap_drop=ALL then adds
# back only this safe set, so allowing exactly these matches Cove's own hardened
# posture. None permit host escape; anything else (esp. SYS_ADMIN, SYS_PTRACE,
# SYS_MODULE, DAC_READ_SEARCH) is rejected.
_ALLOWED_CAPS = {
    # _build_hardening's add-back set — webtop s6-init needs these to chown
    # /config and drop to the unprivileged user.
    "CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID", "KILL",
    # The egress guard + Tailscale/Gluetun sidecars.
    "NET_ADMIN", "NET_RAW",
}
# Devices Cove maps: the VPN sidecars need the TUN device. Nothing else.
_ALLOWED_DEVICES = {"/dev/net/tun"}

# HostConfig keys Cove's launch flow (and docker-py's defaults) produce. Anything
# else is rejected outright — that is what closes VolumesFrom, DeviceCgroupRules,
# DeviceRequests, MaskedPaths, ReadonlyPaths and whatever Docker adds next.
_ALLOWED_HOSTCONFIG_KEYS = {
    "binds", "mounts", "networkmode", "portbindings", "publishallports",
    "restartpolicy", "autoremove", "capadd", "capdrop", "securityopt",
    "devices", "dns", "dnsoptions", "dnssearch", "extrahosts", "shmsize",
    "memory", "memoryswap", "memoryreservation", "memoryswappiness",
    "nanocpus", "cpushares", "cpuperiod", "cpuquota", "cpusetcpus",
    "pidslimit", "oomkilldisable", "oomscoreadj", "ulimits", "logconfig",
    "init", "tmpfs", "readonlyrootfs", "groupadd", "privileged",
    "pidmode", "ipcmode", "utsmode", "usernsmode", "cgroupnsmode",
    "consolesize", "isolation", "links", "volumedriver", "storageopt",
    "sysctls", "runtime", "cpucount", "cpupercent", "iomaximumiops",
    "iomaximumbandwidth", "blkioweight", "cgroupparent", "containeridfile",
    "annotations",
}
# Namespace-mode keys: only the private/default forms are allowed. ``host`` and
# ``container:<x>`` are rejected (sharing a namespace with the updater, the proxy
# or the agent itself is a host-root pivot).
_NS_KEYS = {
    "pidmode": "PidMode", "ipcmode": "IpcMode", "utsmode": "UTSMode",
    "usernsmode": "UsernsMode", "cgroupnsmode": "CgroupnsMode",
}
_NS_ALLOWED_VALUES = {"", "private", "shareable", "none"}

# Container-name prefixes a helper may share a network namespace with (the egress
# guard and readiness probe join the workspace's or its routing sidecar's netns).
_NETNS_OWNER_PREFIXES = ("cove-ws-", "cove-ts-", "cove-gluetun-")
# Named-volume / network name prefixes Cove creates.
_VOLUME_PREFIXES = ("cove-ts-state-", "cove-dind-state-")
_NETWORK_PREFIX = "cove-ws-net-"
# Containers the control plane may exec into: the workspace (screen previews),
# the routing sidecars (``tailscale ip``), and — with exactly one fixed command —
# the updater sidecar (see ``agent_update``).
_EXEC_TARGET_PREFIXES = ("cove-ws-", "cove-ts-", "cove-gluetun-")
UPDATER_CONTAINER = "cove-agent-updater"
# Containers the central Traefik is connected to per-workspace networks as.
_CONNECT_ALLOWED = ("cove-traefik",)

# Sysctls Cove sets (none today) — reject everything so a caller can't flip
# ``kernel.*`` or ``net.ipv4.ip_forward`` on the host's namespaces.
_ALLOWED_SYSCTLS: set[str] = set()


class _DuplicateKey(ValueError):
    pass


def _pairs_hook(pairs):
    """Casefold every key; refuse duplicates (also case-variant duplicates)."""
    out: dict = {}
    for k, v in pairs:
        key = k.casefold() if isinstance(k, str) else k
        if key in out:
            raise _DuplicateKey(f"duplicate key {k!r}")
        out[key] = v
    return out


def _load(body: bytes) -> dict | str:
    """Parse a request body into a casefolded dict, or return a rejection reason."""
    try:
        cfg = json.loads(body or b"{}", object_pairs_hook=_pairs_hook)
    except _DuplicateKey as exc:
        return f"rejected body: {exc}"
    except (ValueError, TypeError):
        return "unparseable body"
    if not isinstance(cfg, dict):
        return "body is not an object"
    return cfg


def _storage_root() -> Path:
    s = get_settings()
    return Path(s.storage_path or (s.data_dir / "workspaces")).resolve()


def _under_root(root: Path, src: str) -> bool:
    try:
        rp = Path(src).resolve()
    except (OSError, ValueError):
        return False
    return rp == root or root in rp.parents


def _named_volume_ok(name: str) -> bool:
    return bool(name) and name.startswith(_VOLUME_PREFIXES)


def _bind_source_ok(root: Path, source: str) -> str | None:
    """A bind source is either an absolute host path under the storage root or
    a Cove-named volume. Anything else (``/etc``, ``docker.sock``, an unknown
    volume, a relative path) is rejected."""
    if source.startswith("/"):
        if not _under_root(root, source):
            return f"bind mount {source} is outside the workspace storage root"
        return None
    if not _named_volume_ok(source):
        return f"volume {source!r} is not a Cove volume"
    return None


def _netns_owner_ok(mode: str) -> bool:
    target = mode.split(":", 1)[1]
    return target.startswith(_NETNS_OWNER_PREFIXES)


def check_create_policy(body: bytes) -> str | None:
    """Return a violation reason, or None if the create request is allowed.

    Conservative: an unparseable or unexpectedly-shaped body is rejected."""
    cfg = _load(body)
    if isinstance(cfg, str):
        return cfg
    hc = cfg.get("hostconfig") or {}
    if not isinstance(hc, dict):
        return "HostConfig is not an object"

    for key in hc:
        if key not in _ALLOWED_HOSTCONFIG_KEYS:
            return f"HostConfig.{key} is not allowed"

    if hc.get("privileged"):
        return "privileged containers are not allowed"

    for key, display in _NS_KEYS.items():
        v = hc.get(key)
        if v is None:
            continue
        if not isinstance(v, str) or v not in _NS_ALLOWED_VALUES:
            return f"{display}={v} is not allowed"

    nm = hc.get("networkmode")
    if nm is not None:
        if not isinstance(nm, str):
            return "NetworkMode is not a string"
        if nm.startswith("container:"):
            if not _netns_owner_ok(nm):
                return f"network_mode {nm} is not allowed"
        elif nm in ("host",):
            return "NetworkMode=host is not allowed"
        elif nm not in ("", "default", "bridge", "none") and not nm.startswith(_NETWORK_PREFIX):
            return f"network {nm!r} is not a Cove workspace network"

    for cap in hc.get("capadd") or []:
        norm = str(cap).upper().removeprefix("CAP_")
        if norm not in _ALLOWED_CAPS:
            return f"capability {cap} is not allowed"

    for dev in hc.get("devices") or []:
        path = dev.get("pathonhost") if isinstance(dev, dict) else None
        if path not in _ALLOWED_DEVICES:
            return f"device {path} is not allowed"

    for opt in hc.get("securityopt") or []:
        if str(opt) not in ("no-new-privileges", "no-new-privileges:true"):
            return f"security option {opt!r} is not allowed"

    for key in (hc.get("sysctls") or {}):
        if key not in _ALLOWED_SYSCTLS:
            return f"sysctl {key} is not allowed"

    if hc.get("volumedriver") or hc.get("storageopt") or hc.get("runtime"):
        return "VolumeDriver/StorageOpt/Runtime are not allowed"

    root = _storage_root()
    for b in hc.get("binds") or []:
        source = str(b).split(":", 1)[0]
        reason = _bind_source_ok(root, source)
        if reason:
            return reason
    for m in hc.get("mounts") or []:
        if not isinstance(m, dict):
            return "mount entry is not an object"
        mtype = m.get("type")
        source = str(m.get("source", ""))
        if mtype == "bind":
            if not source.startswith("/") or not _under_root(root, source):
                return f"bind mount {source} is outside the workspace storage root"
            if m.get("bindoptions"):
                return "bind options are not allowed"
        elif mtype == "volume":
            if not _named_volume_ok(source):
                return f"volume {source!r} is not a Cove volume"
            if m.get("volumeoptions"):
                return "volume driver options are not allowed"
        elif mtype == "tmpfs":
            pass
        else:
            return f"mount type {mtype!r} is not allowed"

    return None


def check_volume_create_policy(body: bytes) -> str | None:
    """``POST /volumes/create``: Cove-named, local driver, no driver options
    (``type=none,o=bind,device=/`` would mount host root through a named volume)."""
    cfg = _load(body)
    if isinstance(cfg, str):
        return cfg
    name = cfg.get("name")
    if not isinstance(name, str) or not _named_volume_ok(name):
        return f"volume {name!r} is not a Cove volume"
    if cfg.get("driveropts"):
        return "volume driver options are not allowed"
    driver = cfg.get("driver")
    if driver not in (None, "", "local"):
        return f"volume driver {driver!r} is not allowed"
    return None


def check_network_create_policy(body: bytes) -> str | None:
    """``POST /networks/create``: only per-workspace bridges."""
    cfg = _load(body)
    if isinstance(cfg, str):
        return cfg
    name = cfg.get("name")
    if not isinstance(name, str) or not name.startswith(_NETWORK_PREFIX):
        return f"network {name!r} is not a Cove workspace network"
    driver = cfg.get("driver")
    if driver not in (None, "", "bridge"):
        return f"network driver {driver!r} is not allowed"
    if cfg.get("options"):
        return "network driver options are not allowed"
    return None


def check_network_connect_policy(body: bytes) -> str | None:
    """``POST /networks/{id}/connect``: only Traefik (routing) and Cove's own
    containers may be attached — never the agent, the socket proxies or the updater."""
    cfg = _load(body)
    if isinstance(cfg, str):
        return cfg
    name = cfg.get("container")
    if not isinstance(name, str):
        return "container is required"
    if name in _CONNECT_ALLOWED or name.startswith(_NETNS_OWNER_PREFIXES):
        return None
    return f"connecting {name!r} to a workspace network is not allowed"


def check_exec_policy(container_name: str, body: bytes, recreate_cmd: list[str]) -> str | None:
    """``POST /containers/{id}/exec``: unprivileged exec into a workspace or its
    routing sidecar, or the single fixed agent-recreate command in the updater."""
    cfg = _load(body)
    if isinstance(cfg, str):
        return cfg
    if cfg.get("privileged"):
        return "privileged exec is not allowed"
    if container_name == UPDATER_CONTAINER:
        if cfg.get("cmd") != recreate_cmd:
            return "only the agent-recreate command may run in the updater"
        return None
    if not container_name.startswith(_EXEC_TARGET_PREFIXES):
        return f"exec into {container_name!r} is not allowed"
    return None


def check_archive_policy(container_name: str) -> str | None:
    """``PUT/GET /containers/{id}/archive``: workspace containers only (copying a
    file into the updater or the proxy is a host-root pivot)."""
    if not container_name.startswith("cove-ws-"):
        return f"archive access to {container_name!r} is not allowed"
    return None
