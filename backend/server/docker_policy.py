"""Policy enforced on Docker ``containers/create`` requests at the zone agent.

The control plane reaches a zone's Docker daemon *through* the cove-agent app
(over the single mTLS port), not via a raw exposed Docker socket. The agent
inspects every create request and rejects anything that would let a caller — even
one holding the control plane's client cert — escape the container to the host.

The allow-list is the envelope Cove's own launch flow needs (storage-root bind
mounts, NET_ADMIN for sidecars/egress, /dev/net/tun for the VPN sidecars,
cove-* networks) and nothing more. Keep it in sync with ``DockerManager`` if the
launch flow gains a new privileged primitive.
"""

import json
from pathlib import Path

from server.config import get_settings

# Capabilities Cove adds: NET_ADMIN (iptables egress guard, Tailscale/Gluetun),
# NET_RAW (ICMP). Anything else (esp. SYS_ADMIN) is a host-escape vector.
_ALLOWED_CAPS = {"NET_ADMIN", "NET_RAW"}
# Devices Cove maps: the VPN sidecars need the TUN device. Nothing else.
_ALLOWED_DEVICES = {"/dev/net/tun"}
# Namespaces that must never be shared with the host.
_HOST_NS_KEYS = ("NetworkMode", "PidMode", "IpcMode", "UTSMode", "UsernsMode", "CgroupnsMode")


def _storage_root() -> Path:
    s = get_settings()
    return Path(s.storage_path or (s.data_dir / "workspaces")).resolve()


def _under_root(root: Path, src: str) -> bool:
    try:
        rp = Path(src).resolve()
    except (OSError, ValueError):
        return False
    return rp == root or root in rp.parents


def _bind_outside_root(root: Path, source: str) -> bool:
    """A bind source that is an absolute host path must be under the storage root.
    Named volumes (no leading slash) are fine."""
    return source.startswith("/") and not _under_root(root, source)


def check_create_policy(body: bytes) -> str | None:
    """Return a violation reason, or None if the create request is allowed.

    Conservative: an unparseable or unexpectedly-shaped body is rejected."""
    try:
        cfg = json.loads(body or b"{}")
    except (ValueError, TypeError):
        return "unparseable create body"
    if not isinstance(cfg, dict):
        return "create body is not an object"
    hc = cfg.get("HostConfig") or {}
    if not isinstance(hc, dict):
        return "HostConfig is not an object"

    if hc.get("Privileged"):
        return "privileged containers are not allowed"

    for key in _HOST_NS_KEYS:
        v = hc.get(key)
        if isinstance(v, str) and v == "host":
            return f"{key}=host is not allowed"

    nm = hc.get("NetworkMode")
    if isinstance(nm, str) and nm.startswith("container:"):
        target = nm.split(":", 1)[1]
        if not target.startswith("cove-"):
            return f"network_mode {nm} is not allowed"

    for cap in hc.get("CapAdd") or []:
        norm = str(cap).upper().removeprefix("CAP_")
        if norm not in _ALLOWED_CAPS:
            return f"capability {cap} is not allowed"

    for dev in hc.get("Devices") or []:
        path = dev.get("PathOnHost") if isinstance(dev, dict) else None
        if path not in _ALLOWED_DEVICES:
            return f"device {path} is not allowed"

    root = _storage_root()
    for b in hc.get("Binds") or []:
        source = str(b).split(":", 1)[0]
        if _bind_outside_root(root, source):
            return f"bind mount {source} is outside the workspace storage root"
    for m in hc.get("Mounts") or []:
        if isinstance(m, dict) and m.get("Type") == "bind":
            if _bind_outside_root(root, str(m.get("Source", ""))):
                return f"bind mount {m.get('Source')} is outside the workspace storage root"

    return None
