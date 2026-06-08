"""Unit tests for DockerManager._sidecar_failure.

Surfaces a failed routing sidecar (Gluetun/Tailscale) as a workspace error so a
non-functional VPN shows a message instead of a bare 404. The Docker client is
faked — no daemon involved.
"""

from types import SimpleNamespace

import docker

from server.docker_manager import DockerManager


class _FakeContainers:
    def __init__(self, mapping):
        self.mapping = mapping

    def get(self, name):
        if name not in self.mapping:
            raise docker.errors.NotFound("absent")
        val = self.mapping[name]
        if isinstance(val, Exception):
            raise val
        return val


def _mgr(mapping):
    m = object.__new__(DockerManager)
    m._client = SimpleNamespace(containers=_FakeContainers(mapping))
    return m


def _container(status="running", health=None):
    state = {"Status": status}
    if health is not None:
        state["Health"] = {"Status": health}
    return SimpleNamespace(attrs={"State": state})


def _ws(**kw):
    base = dict(id=24, use_gluetun=False, use_tailscale=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_plain_workspace_has_no_sidecar_to_fail():
    assert _mgr({})._sidecar_failure(_ws()) is None


def test_gluetun_missing_sidecar():
    msg = _mgr({})._sidecar_failure(_ws(use_gluetun=True))
    assert msg is not None and "VPN" in msg


def test_gluetun_unhealthy_reports_vpn_failure():
    mapping = {"cove-gluetun-24": _container(status="running", health="unhealthy")}
    msg = _mgr(mapping)._sidecar_failure(_ws(use_gluetun=True))
    assert msg == "VPN failed to connect — check your Gluetun config"


def test_gluetun_starting_is_not_a_failure():
    mapping = {"cove-gluetun-24": _container(status="running", health="starting")}
    assert _mgr(mapping)._sidecar_failure(_ws(use_gluetun=True)) is None


def test_gluetun_healthy_is_ok():
    mapping = {"cove-gluetun-24": _container(status="running", health="healthy")}
    assert _mgr(mapping)._sidecar_failure(_ws(use_gluetun=True)) is None


def test_gluetun_exited_reports_stopped():
    mapping = {"cove-gluetun-24": _container(status="exited")}
    msg = _mgr(mapping)._sidecar_failure(_ws(use_gluetun=True))
    assert msg is not None and "stopped" in msg


def test_tailscale_exited_reports_stopped():
    mapping = {"cove-ts-24": _container(status="dead")}
    msg = _mgr(mapping)._sidecar_failure(_ws(use_tailscale=True))
    assert msg is not None and "Tailscale" in msg


def test_tailscale_running_without_healthcheck_is_ok():
    # Tailscale has no healthcheck and its stream works even with egress down, so
    # a plain running sidecar is never flagged.
    mapping = {"cove-ts-24": _container(status="running")}
    assert _mgr(mapping)._sidecar_failure(_ws(use_tailscale=True)) is None


def test_docker_api_error_does_not_flap_status():
    mapping = {"cove-gluetun-24": docker.errors.APIError("boom")}
    assert _mgr(mapping)._sidecar_failure(_ws(use_gluetun=True)) is None
