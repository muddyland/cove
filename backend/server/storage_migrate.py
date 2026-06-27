"""Stream a workspace's persistent home (/config) as a tar.gz for migration.

The migration payload is exactly one directory — ``workspace-{sanitized name}``
under a user's storage base — matching the clone boundary in
``copy_workspace_storage``. The ``.cove-*`` sidecar dirs are siblings and are
re-staged per launch on the destination, so they are never part of the payload.
"""

import logging
import os
import tarfile
from pathlib import Path
from typing import BinaryIO, Iterator

logger = logging.getLogger(__name__)


def workspace_dirname(ws_name: str) -> str:
    from server.docker_manager import _sanitize

    return f"workspace-{_sanitize(ws_name)}"


class _ChunkSink:
    """A write-only file object that buffers tar output for streaming."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def write(self, data: bytes) -> int:
        self._chunks.append(bytes(data))
        return len(data)

    def drain(self) -> list[bytes]:
        chunks = self._chunks
        self._chunks = []
        return chunks


# Top-level entries regenerated on the destination at launch — left out of the
# migration payload so it carries only real user data. ``proot-apps`` holds the
# installed app trees (often many GB: full Chrome/Edge/etc.), which are
# re-installed from the workspace's PROOT_APPS list by install-proot-apps.sh; one
# VDI home was 14 GB of proot-apps vs ~8 MB of actual config, and shipping it ran
# the transfer long enough that the cross-segment connection dropped (HTTP 499).
_MIGRATION_EXCLUDE_TOP = {"proot-apps"}


def export_tar_stream(src_dir: Path) -> Iterator[bytes]:
    """Yield gzip-compressed tar chunks of ``src_dir`` (symlinks preserved),
    skipping regeneratable top-level entries (see ``_MIGRATION_EXCLUDE_TOP``)."""
    sink = _ChunkSink()
    tar = tarfile.open(fileobj=sink, mode="w|gz")
    try:
        for item in sorted(src_dir.rglob("*")):
            rel = item.relative_to(src_dir)
            if rel.parts[0] in _MIGRATION_EXCLUDE_TOP:
                continue
            tar.add(str(item), arcname=str(rel), recursive=False)
            yield from sink.drain()
    finally:
        tar.close()
        yield from sink.drain()


def _tolerant_data_filter(member: tarfile.TarInfo, dest_path: str):
    """Apply the secure ``data`` filter, but SKIP (don't abort on) members it
    rejects. Workspace homes accumulate runtime junk the strict filter refuses —
    e.g. Chromium's ``SingletonSocket``, a symlink to an absolute path. Those
    artifacts are disposable, so dropping them lets the real files through while
    keeping the filter's guarantee that nothing is written outside ``dest_path``
    (a rejected member is never extracted)."""
    try:
        return tarfile.data_filter(member, dest_path)
    except tarfile.FilterError as e:
        logger.warning("migration import: skipping unsafe tar member %r (%s)", member.name, e)
        return None


def import_tar(dst_dir: Path, fileobj: BinaryIO) -> None:
    """Extract a tar.gz stream into ``dst_dir`` (created if absent).

    Uses a tolerant wrapper around the ``data`` extraction filter: a crafted or
    junk archive still cannot write outside ``dst_dir`` (absolute paths / ``..`` /
    absolute symlinks are rejected), but rejected members are skipped rather than
    failing the whole migration."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=fileobj, mode="r|gz") as tar:
        tar.extractall(dst_dir, filter=_tolerant_data_filter)
    _normalize_ownership(dst_dir)


def _normalize_ownership(root: Path) -> None:
    """Chown the imported tree to the workspace user (PUID/PGID).

    The tar preserves the source's uids, which on a webtop home include
    root-owned subdirectories (e.g. ``.config/pulse``, ``.cache``) created by
    root-run processes. The destination webtop runs as PUID (1000), and those
    root-owned dirs break it — pulseaudio's secure-dir check fails on a root-owned
    ``.config/pulse``, so it never starts and the Selkies stream hangs. Normalising
    to PUID:PGID makes a migrated home behave like a freshly-created one.
    Best-effort: a no-op when not running as root (chown raises)."""
    from server.config import get_settings

    settings = get_settings()
    uid, gid = settings.workspace_puid, settings.workspace_pgid
    try:
        os.chown(root, uid, gid, follow_symlinks=False)
    except OSError as exc:
        logger.warning("import: could not chown %s (not root?): %s", root, exc)
        return
    for parent, dirs, files in os.walk(root):
        for name in dirs + files:
            try:
                os.chown(os.path.join(parent, name), uid, gid, follow_symlinks=False)
            except OSError:
                pass
