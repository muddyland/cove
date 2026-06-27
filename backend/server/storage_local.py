"""Local-filesystem workspace storage operations.

Shared by the control plane (for zone-0 / local workspaces) and the zone agent
(for its own local workspaces). All operations are confined to a per-user base
directory with the same anti-traversal guard the file browser has always used.
"""

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import HTTPException, status

from server.schemas import FileEntry, FileListing


def resolve(base: Path, rel: str) -> Path:
    """Resolve a user-supplied relative path against base, rejecting traversal."""
    rel = (rel or "").lstrip("/")
    candidate = (base / rel).resolve()
    if candidate != base and base not in candidate.parents:
        raise HTTPException(status_code=400, detail="Invalid path")
    return candidate


def list_dir(base: Path, path: str) -> FileListing:
    target = resolve(base, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")

    entries: list[FileEntry] = []
    for child in target.iterdir():
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
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
