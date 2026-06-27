"""Stream a workspace's persistent home (/config) as a tar.gz for migration.

The migration payload is exactly one directory — ``workspace-{sanitized name}``
under a user's storage base — matching the clone boundary in
``copy_workspace_storage``. The ``.cove-*`` sidecar dirs are siblings and are
re-staged per launch on the destination, so they are never part of the payload.
"""

import logging
import queue
import tarfile
import threading
from pathlib import Path
from typing import Iterator

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


def import_tar(dst_dir: Path, fileobj) -> None:
    """Stream-extract a tar.gz from ``fileobj`` into ``dst_dir`` (created if absent).

    ``fileobj`` only needs a blocking ``read(n)`` — extraction is sequential, so a
    multi-GB home streams straight in without buffering to a temp file (the agent's
    ``/tmp`` is a small RAM-backed tmpfs that a 7GB tar overflows). Two safeguards
    run per member:

    * the secure ``data`` filter (no writes outside ``dst_dir``: absolute paths,
      ``..`` and absolute symlinks are rejected) — but a rejected member is SKIPPED,
      not fatal, since webtop homes carry disposable junk like Chromium's
      ``SingletonSocket`` (an absolute symlink);
    * ownership is forced to the workspace user (PUID/PGID) during extraction, so a
      migrated home behaves like a fresh one. The source tar preserves root-owned
      dirs (e.g. ``.config/pulse``) that break the destination webtop running as
      PUID — pulseaudio's secure-dir check fails on a root-owned ``.config/pulse``,
      so it never starts and the Selkies stream hangs. Only takes effect when
      extracting as root.
    """
    from server.config import get_settings

    settings = get_settings()
    uid, gid = settings.workspace_puid, settings.workspace_pgid

    def _filter(member: tarfile.TarInfo, dest_path: str):
        try:
            member = tarfile.data_filter(member, dest_path)
        except tarfile.FilterError as exc:
            logger.warning("migration import: skipping unsafe tar member %r (%s)", member.name, exc)
            return None
        if member is not None:
            member.uid, member.gid = uid, gid
            member.uname = member.gname = ""  # force numeric ownership
        return member

    dst_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=fileobj, mode="r|gz") as tar:
        tar.extractall(dst_dir, filter=_filter)


class IteratorReader:
    """A blocking ``read(n)`` file-object backed by an iterator of byte chunks, so
    ``import_tar`` can stream-extract a sync byte stream (e.g. httpx ``iter_bytes``)
    without buffering it to a temp file."""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._it = iter(chunks)
        self._buf = b""
        self._eof = False

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            out = b"".join([self._buf, *self._it])
            self._buf, self._eof = b"", True
            return out
        while len(self._buf) < size and not self._eof:
            try:
                self._buf += next(self._it)
            except StopIteration:
                self._eof = True
        out, self._buf = self._buf[:size], self._buf[size:]
        return out


class QueueReader:
    """A blocking ``read(n)`` file-object fed chunk-by-chunk from another thread via
    ``push`` — bridges an async request body into the sync (threaded) ``import_tar``
    extraction. ``push(None)`` signals EOF. The bounded queue applies backpressure
    so a fast uploader can't outrun the extractor. ``abort()`` (called when the
    extractor exits, success or failure) drains the queue and makes ``push`` stop
    blocking, so a producer parked on a full queue can't deadlock the request."""

    def __init__(self, maxsize: int = 32) -> None:
        self._q: queue.Queue = queue.Queue(maxsize=maxsize)
        self._buf = b""
        self._eof = False
        self._aborted = threading.Event()

    def push(self, data) -> None:  # producer (async side, via a worker thread)
        while not self._aborted.is_set():
            try:
                self._q.put(data, timeout=0.25)
                return
            except queue.Full:
                continue

    def abort(self) -> None:  # extractor done: unblock a producer on a full queue
        self._aborted.set()
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def read(self, size: int = -1) -> bytes:  # consumer (extractor thread)
        if size is None or size < 0:
            while not self._eof:
                item = self._q.get()
                if item is None:
                    self._eof = True
                    break
                self._buf += item
            out, self._buf = self._buf, b""
            return out
        while len(self._buf) < size and not self._eof:
            item = self._q.get()
            if item is None:
                self._eof = True
                break
            self._buf += item
        out, self._buf = self._buf[:size], self._buf[size:]
        return out
