"""Render-node GID probe: local daemon vs. remote zone (agent policy)."""

import json
from types import SimpleNamespace

import docker.errors
from docker.models.containers import _create_container_args

from server.docker_manager import EGRESS_GUARD_IMAGE, DockerManager
from server.docker_policy import check_create_policy


class _FakeContainer:
    def __init__(self, *, start_error=None, logs=b"990\n", status=0):
        self.start_error = start_error
        self._logs = logs
        self._status = status
        self.removed = False

    def start(self):
        if self.start_error:
            raise self.start_error

    def wait(self, timeout=None):
        return {"StatusCode": self._status}

    def logs(self, stdout=True, stderr=True):
        return self._logs

    def remove(self, force=False):
        self.removed = True


def _dm(zone_id, container=None, run_out=b"990\n"):
    calls = {}

    def create(image, **kwargs):
        calls["create"] = (image, kwargs)
        return container

    def run(image, **kwargs):
        calls["run"] = (image, kwargs)
        return run_out

    dm = DockerManager.__new__(DockerManager)
    dm.zone_id = zone_id
    dm._render_gid_cache = {}
    dm._pull_image = lambda image: None
    dm._client = SimpleNamespace(containers=SimpleNamespace(create=create, run=run))
    return dm, calls


def _create_body(image, kwargs) -> bytes:
    """The JSON docker-py sends for containers.create(image, **kwargs)."""
    args = _create_container_args({**kwargs, "image": image, "version": "1.41"})
    hc = args.pop("host_config")
    body = {k: v for k, v in args.items() if v is not None}
    body["HostConfig"] = dict(hc)
    return json.dumps(body).encode()


def test_zone_probe_passes_agent_policy_and_reads_gid():
    c = _FakeContainer(logs=b"990\n")
    dm, calls = _dm(5, c)
    assert dm._detect_render_gid("/dev/dri/renderD128") == 990
    image, kwargs = calls["create"]
    assert "run" not in calls  # no /dev bind on a zone
    assert kwargs["devices"] == ["/dev/dri/renderD128:/dev/dri/renderD128:r"]
    assert check_create_policy(_create_body(image, kwargs)) is None
    assert c.removed


def test_old_dev_bind_probe_is_what_the_agent_refused():
    """The local probe's /dev bind is outside the storage root, which is why the
    zone path can't use it."""
    dm, calls = _dm(0)
    dm._detect_render_gid("/dev/dri/renderD128")
    image, kwargs = calls["run"]
    run_only = {"remove", "detach"}
    body = _create_body(image, {k: v for k, v in kwargs.items() if k not in run_only})
    reason = check_create_policy(body)
    assert reason is not None and "outside" in reason


def test_zone_probe_missing_node():
    err = docker.errors.APIError(
        "500 Server Error",
        explanation='error gathering device information while adding custom device '
        '"/dev/dri/renderD128": no such file or directory',
    )
    c = _FakeContainer(start_error=err)
    dm, _ = _dm(5, c)
    assert dm._detect_render_gid("/dev/dri/renderD128") == "missing"
    assert c.removed


def test_zone_probe_other_failure_falls_back():
    c = _FakeContainer(start_error=docker.errors.APIError("403", explanation="blocked"))
    dm, _ = _dm(5, c)
    assert dm._detect_render_gid("/dev/dri/renderD128") is None
    assert c.removed
    # Not cached: the next launch probes again.
    assert dm._render_gid_cache == {}


def test_zone_probe_nonzero_exit_falls_back():
    c = _FakeContainer(logs=b"", status=1)
    dm, _ = _dm(5, c)
    assert dm._detect_render_gid("/dev/dri/renderD128") is None
    assert c.removed


def test_local_probe_still_uses_dev_bind():
    dm, calls = _dm(0, run_out=b"44\n")
    assert dm._detect_render_gid("/dev/dri/renderD128") == 44
    image, kwargs = calls["run"]
    assert image == EGRESS_GUARD_IMAGE
    assert kwargs["volumes"] == {"/dev": {"bind": "/hostdev", "mode": "ro"}}
    assert "create" not in calls
