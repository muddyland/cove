"""Stream a workspace's persistent home (/config) as a tar.gz for migration.

The migration payload is exactly one directory — ``workspace-{sanitized name}``
under a user's storage base — matching the clone boundary in
``copy_workspace_storage``. The ``.cove-*`` sidecar dirs are siblings and are
re-staged per launch on the destination, so they are never part of the payload.
"""

import tarfile
from pathlib import Path
from typing import BinaryIO, Iterator


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


def export_tar_stream(src_dir: Path) -> Iterator[bytes]:
    """Yield gzip-compressed tar chunks of ``src_dir`` (symlinks preserved)."""
    sink = _ChunkSink()
    tar = tarfile.open(fileobj=sink, mode="w|gz")
    try:
        for item in sorted(src_dir.rglob("*")):
            tar.add(str(item), arcname=str(item.relative_to(src_dir)), recursive=False)
            yield from sink.drain()
    finally:
        tar.close()
        yield from sink.drain()


def import_tar(dst_dir: Path, fileobj: BinaryIO) -> None:
    """Extract a tar.gz stream into ``dst_dir`` (created if absent).

    Uses the ``data`` extraction filter so a crafted archive cannot write outside
    ``dst_dir`` (absolute paths / ``..`` are rejected)."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=fileobj, mode="r|gz") as tar:
        tar.extractall(dst_dir, filter="data")
