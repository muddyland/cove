"""Local-filesystem workspace storage operations.

Shared by the control plane (for zone-0 / local workspaces) and the zone agent
(for its own local workspaces). All operations are confined to a per-user base
directory with the same anti-traversal guard the file browser has always used.
"""

import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator
from uuid import uuid4

from fastapi import HTTPException, status

from server.schemas import FileEntry, FileListing

# Reserved bucket, at the user's storage root, that holds soft-deleted items:
# ``.trash/{token}/{name}``. Hidden from normal listings and blocked as an
# operand of file operations so the browser can't recurse into or trash it.
TRASH_DIR = ".trash"


def resolve(base: Path, rel: str) -> Path:
    """Resolve a user-supplied relative path against base, rejecting traversal."""
    rel = (rel or "").lstrip("/")
    candidate = (base / rel).resolve()
    if candidate != base and base not in candidate.parents:
        raise HTTPException(status_code=400, detail="Invalid path")
    return candidate


def _in_trash(base: Path, target: Path) -> bool:
    trash = (base / TRASH_DIR).resolve()
    return target == trash or trash in target.parents


def _unique_dest(dest: Path) -> Path:
    """A non-colliding path next to ``dest``: appends `` (2)``, `` (3)`` … to the
    name (before the suffix for files) rather than overwriting an existing item."""
    if not dest.exists():
        return dest
    parent = dest.parent
    # Keep the extension only for regular files; dirs/extensionless keep the whole name.
    if dest.is_dir() or not dest.suffix:
        stem, suffix = dest.name, ""
    else:
        stem, suffix = dest.stem, dest.suffix
    i = 2
    while True:
        cand = parent / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
        i += 1


def _dir_size(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def list_dir(base: Path, path: str) -> FileListing:
    target = resolve(base, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")

    at_root = target == base
    entries: list[FileEntry] = []
    for child in target.iterdir():
        # The trash bucket lives at the storage root; it's managed through the
        # Trash view, not shown as a normal folder.
        if at_root and child.name == TRASH_DIR:
            continue
        try:
            st = child.stat()
        except OSError:
            continue
        is_dir = child.is_dir()
        entries.append(
            FileEntry(
                name=child.name,
                type="dir" if is_dir else "file",
                size=0 if is_dir else st.st_size,
                modified=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
            )
        )
    entries.sort(key=lambda e: (e.type != "dir", e.name.lower()))
    return FileListing(path=path, entries=entries)


def resolve_download(base: Path, path: str) -> Path:
    target = resolve(base, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="Cannot download a directory")
    return target


def save_upload(
    base: Path, path: str, reader: Callable[[int], bytes], filename: str, max_bytes: int
) -> dict:
    """Stream an upload to ``{base}/{path}/{filename}``, enforcing the size cap.

    ``reader(n)`` returns up to n bytes (e.g. ``UploadFile.file.read``); the
    partial file is removed if the cap is exceeded.
    """
    target_dir = resolve(base, path)
    target_dir.mkdir(parents=True, exist_ok=True)
    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="Destination is not a directory")

    filename = Path(filename or "upload").name
    if not filename or filename in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    dest = resolve(base, str(Path(path or "") / filename))

    total = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = reader(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=f"File exceeds maximum upload size of {max_bytes // (1024 * 1024)} MiB",
                    )
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise

    return {"name": filename, "path": str(dest.relative_to(base))}


def delete(base: Path, path: str) -> None:
    target = resolve(base, path)
    if target == base:
        raise HTTPException(status_code=400, detail="Cannot delete the root directory")
    if _in_trash(base, target):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


# ── Copy / move ───────────────────────────────────────────────────────────────

def _prepare_dst_dir(base: Path, src: Path, dst_dir: str) -> Path:
    """Resolve+create the destination directory, rejecting trash operands and a
    folder being placed inside itself. Returns the target directory Path."""
    if _in_trash(base, src):
        raise HTTPException(status_code=400, detail="Invalid path")
    d = resolve(base, dst_dir)
    if _in_trash(base, d):
        raise HTTPException(status_code=400, detail="Invalid destination")
    d.mkdir(parents=True, exist_ok=True)
    if not d.is_dir():
        raise HTTPException(status_code=400, detail="Destination is not a directory")
    if src.is_dir() and (d == src or src in d.parents):
        raise HTTPException(status_code=400, detail="Cannot move a folder into itself")
    return d


def copy(base: Path, src: str, dst_dir: str) -> dict:
    """Copy ``src`` into directory ``dst_dir`` (name preserved, suffixed on collision)."""
    s = resolve(base, src)
    if not s.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    d = _prepare_dst_dir(base, s, dst_dir)
    dest = _unique_dest(d / s.name)
    if s.is_dir():
        shutil.copytree(s, dest, symlinks=True, ignore_dangling_symlinks=True)
    else:
        shutil.copy2(s, dest)
    return {"name": dest.name, "path": str(dest.relative_to(base))}


def move(base: Path, src: str, dst_dir: str) -> dict:
    """Move ``src`` into directory ``dst_dir`` (name preserved, suffixed on collision)."""
    s = resolve(base, src)
    if s == base:
        raise HTTPException(status_code=400, detail="Cannot move the root directory")
    if not s.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    d = _prepare_dst_dir(base, s, dst_dir)
    if d == s.parent:
        # Already in that folder — treat as a no-op rather than making a suffixed copy.
        return {"name": s.name, "path": str(s.relative_to(base))}
    dest = _unique_dest(d / s.name)
    try:
        shutil.move(str(s), str(dest))
    except (FileNotFoundError, shutil.Error) as e:
        # Source moved/removed by a concurrent request between the check and here.
        raise HTTPException(status_code=404, detail="Path not found") from e
    return {"name": dest.name, "path": str(dest.relative_to(base))}


# ── Archive (folder / file download as a streamed zip) ─────────────────────────

class _ZipStream:
    """A minimal write-only sink. ``zipfile`` wraps it (it exposes no ``seek``),
    so the archive is written with per-entry data descriptors — i.e. streamable
    without ever seeking back to patch headers. ``take`` drains what's buffered."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def write(self, data: bytes) -> int:
        self._buf.extend(data)
        return len(data)

    def flush(self) -> None:  # pragma: no cover - zipfile calls it
        pass

    def take(self) -> bytes:
        data = bytes(self._buf)
        self._buf.clear()
        return data


def iter_zip(base: Path, path: str) -> Iterator[bytes]:
    """Stream a zip of ``path`` (a file, or a directory walked recursively).

    Validation runs eagerly (this is a regular function returning a generator), so
    a bad path raises *before* the streaming response starts. The archive top-level
    entry is the item's own name, so unzipping recreates the folder."""
    target = resolve(base, path)
    if _in_trash(base, target):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    if target.is_dir():
        root = target.name or "archive"
        members: list[tuple[Path, str]] = []
        for child in sorted(target.rglob("*")):
            try:
                if not child.is_file():  # skip dirs (recreated implicitly) + specials
                    continue
            except OSError:
                continue
            members.append((child, str(Path(root) / child.relative_to(target))))
    else:
        members = [(target, target.name)]

    def _gen() -> Iterator[bytes]:
        stream = _ZipStream()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for src, arcname in members:
                try:
                    info = zipfile.ZipInfo.from_file(src, arcname)
                except OSError:
                    continue
                info.compress_type = zipfile.ZIP_DEFLATED
                try:
                    with zf.open(info, "w") as entry, open(src, "rb") as fh:
                        while True:
                            chunk = fh.read(1024 * 1024)
                            if not chunk:
                                break
                            entry.write(chunk)
                            data = stream.take()
                            if data:
                                yield data
                except OSError:
                    continue
                data = stream.take()
                if data:
                    yield data
        tail = stream.take()
        if tail:
            yield tail

    return _gen()


# ── Trash (soft delete) ────────────────────────────────────────────────────────

def trash_move(base: Path, path: str) -> dict:
    """Move ``path`` into ``.trash/{token}/{name}`` and return its metadata. The
    caller (control plane) records the DB row that tracks original path + expiry."""
    target = resolve(base, path)
    if target == base:
        raise HTTPException(status_code=400, detail="Cannot delete the root directory")
    if _in_trash(base, target):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    is_dir = target.is_dir()
    token = uuid4().hex
    token_dir = base / TRASH_DIR / token
    token_dir.mkdir(parents=True, exist_ok=True)
    try:
        size = _dir_size(target) if is_dir else target.stat().st_size
        shutil.move(str(target), str(token_dir / target.name))
    except (FileNotFoundError, shutil.Error) as e:
        # The item vanished between the check and the move — e.g. a duplicate
        # request from an impatient double-click already trashed it. Return a
        # clean 404 rather than a 500.
        shutil.rmtree(token_dir, ignore_errors=True)
        raise HTTPException(status_code=404, detail="Path not found") from e
    return {"token": token, "name": target.name, "is_dir": is_dir, "size": size}


def trash_restore(base: Path, token: str, original_path: str) -> dict:
    """Move a trashed item back to where it came from (suffixed on collision)."""
    token = Path(token).name  # defend against traversal via the token
    name = Path(original_path).name
    src = resolve(base, f"{TRASH_DIR}/{token}/{name}")
    if not src.exists():
        raise HTTPException(status_code=404, detail="Trashed item not found")
    parent = str(Path(original_path).parent) if str(Path(original_path).parent) != "." else ""
    dest_dir = resolve(base, parent)
    if _in_trash(base, dest_dir):
        raise HTTPException(status_code=400, detail="Invalid destination")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique_dest(dest_dir / name)
    shutil.move(str(src), str(dest))
    shutil.rmtree(base / TRASH_DIR / token, ignore_errors=True)
    return {"name": dest.name, "path": str(dest.relative_to(base))}


def trash_purge(base: Path, token: str) -> None:
    """Permanently remove a trashed item's bytes (best-effort)."""
    token = Path(token).name
    token_dir = resolve(base, f"{TRASH_DIR}/{token}")
    if token_dir.is_dir():
        shutil.rmtree(token_dir, ignore_errors=True)
