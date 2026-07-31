"""Shared per-user /config profile: mount resolution + purge protection.

A shared-profile workspace mounts one per-user home (``<base>/<user>/_profile``)
instead of its own ``workspace-<name>`` dir, and that home must survive a purged
container/workspace.
"""

from pathlib import Path
from types import SimpleNamespace

from server.config import get_settings
from server.docker_manager import _resolve_mount, delete_workspace_storage


def _storage_base() -> Path:
    settings = get_settings()
    return (settings.storage_path or (settings.data_dir / "workspaces")).resolve()


def _fake_ws(**over):
    base = dict(
        id=1,
        name="my ws",
        shared_profile=False,
        volume_name=None,
        user=SimpleNamespace(username="alice"),
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_shared_profile_mounts_shared_home(client):
    # client fixture configures a temp storage root via get_settings().
    ws = _fake_ws(shared_profile=True, name="ubuntu")
    source, is_bind = _resolve_mount(ws)
    assert is_bind
    assert Path(source) == _storage_base() / "alice" / "_profile"

    # A second shared workspace with a different name resolves to the SAME home.
    ws2 = _fake_ws(shared_profile=True, name="fedora")
    assert _resolve_mount(ws2)[0] == source


def test_private_workspace_uses_own_home(client):
    ws = _fake_ws(shared_profile=False, name="solo")
    source, _ = _resolve_mount(ws)
    assert Path(source) == _storage_base() / "alice" / "workspace-solo"


def test_delete_storage_skips_shared_profile(client):
    base = _storage_base()
    shared = base / "alice" / "_profile"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "keep.txt").write_text("survives a deleted container")

    ws = _fake_ws(shared_profile=True, volume_name=str(shared))
    delete_workspace_storage(ws)  # must be a no-op

    assert shared.exists()
    assert (shared / "keep.txt").read_text() == "survives a deleted container"


def test_delete_storage_removes_private_home(client):
    base = _storage_base()
    home = base / "alice" / "workspace-solo"
    home.mkdir(parents=True, exist_ok=True)
    (home / "f.txt").write_text("x")

    ws = _fake_ws(shared_profile=False, name="solo", volume_name=str(home))
    delete_workspace_storage(ws)

    assert not home.exists()
