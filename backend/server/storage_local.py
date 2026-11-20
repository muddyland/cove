"""Local-filesystem workspace storage operations.

Shared by the control plane (for zone-0 / local workspaces) and the zone agent
(for its own local workspaces). All operations are confined to a per-user base
directory.

The tree under the base is writable from inside the workspace containers, and
this code runs as root on the host. So no path is ever checked and then opened
again by name: a workspace process could swap a checked directory for a symlink
in between and point the next open at ``/app/data/secret.key`` or another user's
home. Instead every operation opens the base directory and walks the user's
path one component at a time with ``O_NOFOLLOW`` (``_walk``), then acts on the
open descriptors only (``*at`` calls: ``dir_fd=``, ``src_dir_fd=``…):

* a symlink is never followed, in any component (it is refused with 400 where
  the path names it, skipped inside a walked folder, and copied as a link);
* new files and folders are created with ``O_CREAT | O_EXCL | O_NOFOLLOW`` /
  ``mkdir`` relative to the open parent, so a planted (even dangling) symlink
  can't redirect the write, and their mode is set explicitly without the
  set-id bits;
* only regular files are ever read or written: FIFOs, sockets and device nodes
  (which a DinD sidecar can create in ``/config``) are refused, so a FIFO can't
  hang a worker and a device node can't reach the host's disks.
"""

import errno
import os
import shutil
import stat
import time
import zipfile
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException, status
from fastapi.responses import StreamingResponse

from server.schemas import FileEntry, FileListing

# Reserved bucket, at the user's storage root, that holds soft-deleted items:
# ``.trash/{token}/{name}``. Hidden from normal listings and blocked as an
# operand of file operations so the browser can't recurse into or trash it.
TRASH_DIR = ".trash"

_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _CLOEXEC
# O_NONBLOCK + O_NOCTTY: opening never blocks (FIFO) or grabs a terminal; the
# fstat right after the open rejects anything that isn't a regular file.
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY | _CLOEXEC
_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _CLOEXEC
_DIR_MODE = 0o755
_FILE_MODE = 0o644
# Permission bits kept on copies: rwx for user/group/other, never setuid,
# setgid or sticky (the copy is created by root).
_PERM_BITS = 0o777
_CHUNK = 1024 * 1024
# A folder copy holds two descriptors per level; deeper trees are refused
# rather than exhausting descriptors or the recursion limit.
_MAX_DEPTH = 256
_MAX_SUFFIX = 10000


def _bad(detail: str = "Invalid path") -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def _http_error(e: OSError, not_found: str = "Path not found") -> HTTPException:
    """Map an OSError from a walk or ``*at`` call to the client-facing error."""
    if e.errno == errno.ENOENT:
        return HTTPException(status_code=404, detail=not_found)
    if e.errno == errno.ELOOP:
        return _bad("Path is a symbolic link")
    if e.errno == errno.ENOTDIR:
        return _bad("Not a directory")
    if e.errno == errno.EISDIR:
        return _bad("A folder with that name already exists")
    if e.errno in (errno.ENAMETOOLONG, errno.EINVAL, errno.EEXIST, errno.ENOTEMPTY):
        return _bad()
    return HTTPException(status_code=500, detail="File operation failed")


def _parts(rel: str) -> list[str]:
    """Split a user-supplied relative path into components.

    ``""``, ``"/"`` and ``"."`` are the base itself. ``..`` is refused outright
    rather than resolved: resolving it would need the very path lookups this
    module avoids, and the browser never sends it.
    """
    rel = rel or ""
    if "\x00" in rel:
        raise _bad()
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise _bad()
    return parts


def _no_trash(parts: list[str], detail: str = "Invalid path") -> None:
    if parts and parts[0] == TRASH_DIR:
        raise _bad(detail)


def _open_base(base: Path) -> int:
    try:
        return os.open(base, _DIR_FLAGS)
    except OSError as e:
        raise _http_error(e) from e


def _walk(base_fd: int, parts: list[str], *, create: bool = False) -> int:
    """A new descriptor for the directory at ``parts`` under ``base_fd``.

    Each component is opened relative to the previous one with ``O_NOFOLLOW``,
    so the result is always inside the base whatever a workspace does to the
    tree meanwhile. With ``create`` missing components are made (``mkdir``
    never follows a symlink either: an existing link fails the next open).
    The caller closes the returned descriptor.
    """
    fd = os.dup(base_fd)
    try:
        for name in parts:
            try:
                nfd = os.open(name, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(name, _DIR_MODE, dir_fd=fd)
                except FileExistsError:
                    pass
                nfd = os.open(name, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nfd
    except OSError as e:
        os.close(fd)
        raise _http_error(e) from e
    except BaseException:
        os.close(fd)
        raise
    return fd


@contextmanager
def _dir(base: Path, parts: list[str], *, create: bool = False) -> Iterator[int]:
    """Context manager: the open directory at ``parts`` (see ``_walk``)."""
    base_fd = _open_base(base)
    try:
        fd = _walk(base_fd, parts, create=create)
    finally:
        os.close(base_fd)
    try:
        yield fd
    finally:
        os.close(fd)


def _lstat(dir_fd: int, name: str, not_found: str = "Path not found") -> os.stat_result:
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError as e:
        raise _http_error(e, not_found) from e


def _open_regular(dir_fd: int, name: str) -> tuple[int, os.stat_result]:
    """Open ``name`` (in ``dir_fd``) for reading only if it is a regular file.

    Raises 400 for a directory, a symlink or a special file, before reading a
    byte. The descriptor is the caller's to close.
    """
    pre = _lstat(dir_fd, name)
    if stat.S_ISDIR(pre.st_mode):
        raise _bad("Cannot download a directory")
    if not stat.S_ISREG(pre.st_mode):
        raise _bad("Not a regular file")
    try:
        fd = os.open(name, _READ_FLAGS, dir_fd=dir_fd)
    except OSError as e:
        raise _http_error(e) from e
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise _bad("Not a regular file")
    os.set_blocking(fd, True)
    return fd, st


def _tree_size(dir_fd: int, depth: int = 0) -> int:
    """Total size of the regular files under ``dir_fd``; symlinks are skipped."""
    total = 0
    with os.scandir(dir_fd) as it:
        entries = list(it)
    for child in entries:
        try:
            st = child.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            total += st.st_size
        elif stat.S_ISDIR(st.st_mode) and depth < _MAX_DEPTH:
            try:
                sub = os.open(child.name, _DIR_FLAGS, dir_fd=dir_fd)
            except OSError:
                continue
            try:
                total += _tree_size(sub, depth + 1)
            finally:
                os.close(sub)
    return total


def _dir_size(path: Path) -> int:
    """Size of the regular files under ``path`` (a trusted directory path)."""
    fd = os.open(path, _DIR_FLAGS)
    try:
        return _tree_size(fd)
    finally:
        os.close(fd)


def _candidates(name: str, is_dir: bool) -> Iterator[str]:
    """``name``, then ``name (2)``, ``name (3)`` … (before the suffix for files)."""
    yield name
    p = PurePosixPath(name)
    if is_dir or not p.suffix:
        stem, suffix = name, ""
    else:
        stem, suffix = p.stem, p.suffix
    for i in range(2, _MAX_SUFFIX):
        yield f"{stem} ({i}){suffix}"
    raise HTTPException(status_code=409, detail="Too many items with that name")


def list_dir(base: Path, path: str) -> FileListing:
    parts = _parts(path)
    entries: list[FileEntry] = []
    with _dir(base, parts) as fd, os.scandir(fd) as it:
        # (DirEntry.stat is relative to ``fd``, so it runs while that is open.)
        for child in it:
            # The trash bucket lives at the storage root; it's managed through
            # the Trash view, not shown as a normal folder.
            if not parts and child.name == TRASH_DIR:
                continue
            try:
                # lstat: a symlink is listed as itself, never as what it points
                # at (stat'ing the target as root would reveal host paths).
                st = child.stat(follow_symlinks=False)
            except OSError:
                continue
            is_dir = stat.S_ISDIR(st.st_mode)
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


def _content_disposition(filename: str) -> str:
    quoted = quote(filename)
    if quoted != filename:
        return f"attachment; filename*=utf-8''{quoted}"
    return f'attachment; filename="{filename}"'


def download_response(base: Path, path: str) -> StreamingResponse:
    """A download of one regular file, served from the descriptor opened here.

    The file is opened before the response is returned and the body is read from
    that descriptor, so nothing done to the tree afterwards changes what's sent.
    """
    parts = _parts(path)
    if not parts:
        raise _bad("Cannot download a directory")
    with _dir(base, parts[:-1]) as dfd:
        fd, st = _open_regular(dfd, parts[-1])
    fh = os.fdopen(fd, "rb")  # closed by the body iterator (or on collection)
    size = st.st_size

    def _body() -> Iterator[bytes]:
        # Exactly the size announced in Content-Length, even if the file
        # grows meanwhile.
        with fh:
            left = size
            while left > 0:
                chunk = fh.read(min(_CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    return StreamingResponse(
        _body(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": _content_disposition(parts[-1]),
            "Content-Length": str(size),
        },
    )


def save_upload(
    base: Path, path: str, reader: Callable[[int], bytes], filename: str, max_bytes: int
) -> dict:
    """Stream an upload to ``{base}/{path}/{filename}``, enforcing the size cap.

    ``reader(n)`` returns up to n bytes (e.g. ``UploadFile.file.read``). The bytes
    go to a new temporary file (``O_EXCL``, in the open target folder) that is
    renamed over the name at the end, so an existing file is replaced, never
    written through, and an upload over the cap leaves nothing behind.
    """
    parts = _parts(path)
    _no_trash(parts, "Invalid destination")
    filename = PurePosixPath(filename or "upload").name
    if not filename or filename in (".", "..") or "\x00" in filename:
        raise _bad("Invalid filename")

    with _dir(base, parts, create=True) as dfd:
        tmp = f".cove-upload-{uuid4().hex}"
        try:
            fd = os.open(tmp, _CREATE_FLAGS, _FILE_MODE, dir_fd=dfd)
        except OSError as e:
            raise _http_error(e) from e
        try:
            with os.fdopen(fd, "wb") as out:
                os.fchmod(out.fileno(), _FILE_MODE)  # not narrowed by the umask
                total = 0
                while True:
                    chunk = reader(_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise HTTPException(
                            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            detail=f"File exceeds maximum upload size of {max_bytes // (1024 * 1024)} MiB",
                        )
                    out.write(chunk)
            os.rename(tmp, filename, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException as e:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            if isinstance(e, OSError):
                raise _http_error(e) from e
            raise

    return {"name": filename, "path": "/".join([*parts, filename])}


def _remove(dir_fd: int, name: str) -> None:
    st = _lstat(dir_fd, name)
    try:
        if stat.S_ISDIR(st.st_mode):
            # The fd-based rmtree: never follows a symlink, and refuses a name
            # that was swapped for one after the lstat.
            shutil.rmtree(name, dir_fd=dir_fd)
        else:
            os.unlink(name, dir_fd=dir_fd)
    except OSError as e:
        raise _http_error(e) from e


def delete(base: Path, path: str) -> None:
    parts = _parts(path)
    if not parts:
        raise _bad("Cannot delete the root directory")
    _no_trash(parts)
    with _dir(base, parts[:-1]) as dfd:
        _remove(dfd, parts[-1])


# ── Copy / move ───────────────────────────────────────────────────────────────


def _copy_file_into(
    src_fd: int, st: os.stat_result, dst_dir_fd: int, names: Iterator[str]
) -> str | None:
    """Copy the open regular file ``src_fd`` to the first free name in ``names``."""
    for cand in names:
        try:
            out = os.open(cand, _CREATE_FLAGS, 0o600, dir_fd=dst_dir_fd)
        except FileExistsError:
            continue  # includes a (dangling) symlink of that name
        try:
            while True:
                chunk = os.read(src_fd, _CHUNK)
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    n = os.write(out, view)
                    view = view[n:]
            os.fchmod(out, st.st_mode & _PERM_BITS)
            os.utime(out, ns=(st.st_atime_ns, st.st_mtime_ns))
        except BaseException:
            os.close(out)
            try:
                os.unlink(cand, dir_fd=dst_dir_fd)
            except OSError:
                pass
            raise
        os.close(out)
        return cand
    return None  # (only a nested entry has a single candidate)


def _copy_tree(src_fd: int, dst_fd: int, created: set, depth: int) -> None:
    """Copy the contents of open folder ``src_fd`` into open folder ``dst_fd``."""
    if depth > _MAX_DEPTH:
        raise _bad("Folder is nested too deeply to copy")
    with os.scandir(src_fd) as it:
        children = sorted(it, key=lambda e: e.name)
    for child in children:
        try:
            st = child.stat(follow_symlinks=False)
        except OSError:
            continue
        _copy_one(src_fd, child.name, st, dst_fd, iter((child.name,)), created, depth, top=False)


def _copy_one(
    src_dir_fd: int,
    name: str,
    st: os.stat_result,
    dst_dir_fd: int,
    names: Iterator[str],
    created: set,
    depth: int,
    top: bool,
) -> str | None:
    """Copy entry ``name`` of ``src_dir_fd`` into ``dst_dir_fd`` under the first
    free name in ``names``. Returns the name used (None for a skipped entry)."""
    mode = st.st_mode
    if stat.S_ISDIR(mode):
        if (st.st_dev, st.st_ino) in created:
            return None  # a folder this copy made (dst moved inside src meanwhile)
        try:
            sfd = os.open(name, _DIR_FLAGS, dir_fd=src_dir_fd)
        except OSError as e:
            if top:
                raise _http_error(e) from e
            return None
        try:
            for cand in names:
                try:
                    os.mkdir(cand, 0o700, dir_fd=dst_dir_fd)
                except FileExistsError:
                    continue
                break
            else:
                return None  # (only a nested entry has a single candidate)
            dfd = os.open(cand, _DIR_FLAGS, dir_fd=dst_dir_fd)
            try:
                dst = os.fstat(dfd)
                created.add((dst.st_dev, dst.st_ino))
                _copy_tree(sfd, dfd, created, depth + 1)
                os.fchmod(dfd, mode & _PERM_BITS)
                os.utime(dfd, ns=(st.st_atime_ns, st.st_mtime_ns))
            finally:
                os.close(dfd)
            return cand
        finally:
            os.close(sfd)
    if stat.S_ISLNK(mode):
        # Copied as a link (what copytree(symlinks=True) did): creating one
        # never follows it.
        try:
            target = os.readlink(name, dir_fd=src_dir_fd)
        except OSError:
            return None
        for cand in names:
            try:
                os.symlink(target, cand, dir_fd=dst_dir_fd)
            except FileExistsError:
                continue
            return cand
        return None
    if stat.S_ISREG(mode):
        try:
            fd, fst = _open_regular(src_dir_fd, name)
        except HTTPException:
            if top:
                raise
            return None  # replaced by something else since the scan
        try:
            return _copy_file_into(fd, fst, dst_dir_fd, names)
        finally:
            os.close(fd)
    # FIFO, socket, device node: never opened.
    if top:
        raise _bad("Not a regular file")
    return None


def clone_tree(src: Path, dst: Path) -> None:
    """Copy folder ``src`` to a new folder ``dst`` with the same safeguards as
    the file browser's copy (workspace clone). Both paths are built by the
    caller from trusted parts; everything *inside* ``src`` is the workspace's
    and is never followed. Raises FileExistsError if ``dst`` exists."""
    src_fd = os.open(src, _DIR_FLAGS)
    try:
        parent_fd = os.open(dst.parent, _DIR_FLAGS)
        try:
            os.mkdir(dst.name, 0o700, dir_fd=parent_fd)
            dst_fd = os.open(dst.name, _DIR_FLAGS, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        try:
            st = os.fstat(src_fd)
            out = os.fstat(dst_fd)
            _copy_tree(src_fd, dst_fd, {(out.st_dev, out.st_ino)}, 0)
            os.fchmod(dst_fd, st.st_mode & _PERM_BITS)
        finally:
            os.close(dst_fd)
    finally:
        os.close(src_fd)


def _operands(base_fd: int, src: list[str], dst: list[str]):
    """Open the source's parent and lstat the source; check the destination."""
    _no_trash(src)
    _no_trash(dst, "Invalid destination")
    sdfd = _walk(base_fd, src[:-1])
    try:
        st = _lstat(sdfd, src[-1])
    except BaseException:
        os.close(sdfd)
        raise
    if stat.S_ISDIR(st.st_mode) and dst[: len(src)] == src:
        os.close(sdfd)
        raise _bad("Cannot move a folder into itself")
    return sdfd, st


def copy(base: Path, src: str, dst_dir: str) -> dict:
    """Copy ``src`` into directory ``dst_dir`` (name preserved, suffixed on collision)."""
    sp, dp = _parts(src), _parts(dst_dir)
    if not sp:
        raise _bad("Cannot move a folder into itself")
    base_fd = _open_base(base)
    try:
        sdfd, st = _operands(base_fd, sp, dp)
        try:
            ddfd = _walk(base_fd, dp, create=True)
            try:
                name = sp[-1]
                is_dir = stat.S_ISDIR(st.st_mode)
                try:
                    new = _copy_one(
                        sdfd, name, st, ddfd, _candidates(name, is_dir), set(), 0, top=True
                    )
                except OSError as e:
                    raise _http_error(e) from e
                if new is None:  # changed under us between the lstat and the copy
                    raise HTTPException(status_code=409, detail="Source changed, try again")
            finally:
                os.close(ddfd)
        finally:
            os.close(sdfd)
    finally:
        os.close(base_fd)
    return {"name": new, "path": "/".join([*dp, new])}


def _rename_unique(src_dir_fd: int, name: str, dst_dir_fd: int, is_dir: bool) -> str:
    """Rename ``name`` into ``dst_dir_fd`` under the first free name.

    Both ends are open descriptors inside the base, so a name created there by
    the workspace between the check and the rename can only make this replace
    the user's own entry, never reach outside the base. A move across
    filesystems falls back to copy + delete.
    """
    for cand in _candidates(name, is_dir):
        try:
            os.stat(cand, dir_fd=dst_dir_fd, follow_symlinks=False)
            continue
        except FileNotFoundError:
            pass
        try:
            os.rename(name, cand, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise _http_error(e) from e
            st = _lstat(src_dir_fd, name)
            try:
                new = _copy_one(src_dir_fd, name, st, dst_dir_fd, iter((cand,)), set(), 0, top=True)
            except OSError as e2:
                raise _http_error(e2) from e2
            if new is None:  # the name was taken meanwhile: keep the source
                raise HTTPException(status_code=409, detail="Destination changed, try again")
            _remove(src_dir_fd, name)
            return new
        return cand
    raise AssertionError("unreachable")


def move(base: Path, src: str, dst_dir: str) -> dict:
    """Move ``src`` into directory ``dst_dir`` (name preserved, suffixed on collision)."""
    sp, dp = _parts(src), _parts(dst_dir)
    if not sp:
        raise _bad("Cannot move the root directory")
    base_fd = _open_base(base)
    try:
        sdfd, st = _operands(base_fd, sp, dp)
        try:
            if dp == sp[:-1]:
                # Already in that folder — a no-op rather than a suffixed copy.
                return {"name": sp[-1], "path": "/".join(sp)}
            ddfd = _walk(base_fd, dp, create=True)
            try:
                new = _rename_unique(sdfd, sp[-1], ddfd, stat.S_ISDIR(st.st_mode))
            finally:
                os.close(ddfd)
        finally:
            os.close(sdfd)
    finally:
        os.close(base_fd)
    return {"name": new, "path": "/".join([*dp, new])}


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


def _zip_info(arcname: str, st: os.stat_result) -> zipfile.ZipInfo:
    date_time = time.localtime(st.st_mtime)[:6]
    if date_time[0] < 1980:
        date_time = (1980, 1, 1, 0, 0, 0)
    elif date_time[0] > 2107:
        date_time = (2107, 12, 31, 23, 59, 59)
    info = zipfile.ZipInfo(arcname, date_time)
    info.external_attr = (st.st_mode & 0xFFFF) << 16
    info.file_size = st.st_size
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def _zip_members(dir_fd: int, prefix: str, depth: int) -> Iterator[tuple[int, str, str]]:
    """(folder fd, name, archive name) for every regular file under ``dir_fd``.

    Symlinks and special files are skipped outright: following one would read a
    host path the user planted a link to (the archive is built here, as root).
    """
    if depth > _MAX_DEPTH:
        return
    with os.scandir(dir_fd) as it:
        children = sorted(it, key=lambda e: e.name)
    for child in children:
        try:
            st = child.stat(follow_symlinks=False)
        except OSError:
            continue
        arcname = f"{prefix}/{child.name}"
        if stat.S_ISREG(st.st_mode):
            yield dir_fd, child.name, arcname
        elif stat.S_ISDIR(st.st_mode):
            try:
                sub = os.open(child.name, _DIR_FLAGS, dir_fd=dir_fd)
            except OSError:
                continue
            try:
                yield from _zip_members(sub, arcname, depth + 1)
            finally:
                os.close(sub)


def iter_zip(base: Path, path: str) -> Iterator[bytes]:
    """Stream a zip of ``path`` (a file, or a directory walked recursively).

    Validation runs eagerly (this is a regular function returning a generator), so
    a bad path raises *before* the streaming response starts. The archive top-level
    entry is the item's own name, so unzipping recreates the folder."""
    parts = _parts(path)
    _no_trash(parts)
    if parts:
        with _dir(base, parts[:-1]) as dfd:
            st = _lstat(dfd, parts[-1])
        if stat.S_ISLNK(st.st_mode):
            raise _bad("Path is a symbolic link")
        if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
            raise _bad("Not a regular file")
        is_dir = stat.S_ISDIR(st.st_mode)
        root = parts[-1]
    else:
        is_dir, root = True, base.name or "archive"

    def _members(stack) -> Iterator[tuple[int, str, str]]:
        # The generator re-walks from the base: each walk is safe on its own, so
        # nothing checked above is trusted here.
        if is_dir:
            dfd = stack.enter_context(_dir(base, parts))
            yield from _zip_members(dfd, root, 0)
        else:
            dfd = stack.enter_context(_dir(base, parts[:-1]))
            yield dfd, parts[-1], root

    def _gen() -> Iterator[bytes]:
        stream = _ZipStream()
        with ExitStack() as stack, zipfile.ZipFile(
            stream, "w", zipfile.ZIP_DEFLATED, allowZip64=True
        ) as zf:
            for dfd, name, arcname in _members(stack):
                try:
                    fd, st = _open_regular(dfd, name)
                except HTTPException:
                    continue  # changed since the scan
                with os.fdopen(fd, "rb") as fh:
                    try:
                        with zf.open(_zip_info(arcname, st), "w") as entry:
                            while True:
                                chunk = fh.read(_CHUNK)
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


def _token(token: str) -> str:
    token = PurePosixPath(token or "").name  # defend against traversal via the token
    if not token or token in (".", "..") or "\x00" in token:
        raise _bad()
    return token


def trash_move(base: Path, path: str) -> dict:
    """Move ``path`` into ``.trash/{token}/{name}`` and return its metadata. The
    caller (control plane) records the DB row that tracks original path + expiry."""
    parts = _parts(path)
    if not parts:
        raise _bad("Cannot delete the root directory")
    _no_trash(parts)
    name = parts[-1]
    token = uuid4().hex
    base_fd = _open_base(base)
    try:
        sdfd = _walk(base_fd, parts[:-1])
        try:
            st = _lstat(sdfd, name)
            is_dir = stat.S_ISDIR(st.st_mode)
            if is_dir:
                try:
                    sub = os.open(name, _DIR_FLAGS, dir_fd=sdfd)
                except OSError as e:
                    raise _http_error(e) from e
                try:
                    size = _tree_size(sub)
                finally:
                    os.close(sub)
            else:
                size = st.st_size
            tdfd = _walk(base_fd, [TRASH_DIR, token], create=True)
            try:
                try:
                    _rename_unique(sdfd, name, tdfd, is_dir)
                except HTTPException as e:
                    # The item vanished between the check and the move — e.g. a
                    # duplicate request from an impatient double-click already
                    # trashed it. Drop the empty bucket; 404 rather than 500.
                    trash_fd = _walk(base_fd, [TRASH_DIR])
                    try:
                        shutil.rmtree(token, dir_fd=trash_fd, ignore_errors=True)
                    finally:
                        os.close(trash_fd)
                    raise e
            finally:
                os.close(tdfd)
        finally:
            os.close(sdfd)
    finally:
        os.close(base_fd)
    return {"token": token, "name": name, "is_dir": is_dir, "size": size}


def trash_restore(base: Path, token: str, original_path: str) -> dict:
    """Move a trashed item back to where it came from (suffixed on collision)."""
    token = _token(token)
    orig = _parts(original_path)
    if not orig:
        raise _bad()
    name, parent = orig[-1], orig[:-1]
    _no_trash(parent, "Invalid destination")
    base_fd = _open_base(base)
    try:
        tfd = _walk(base_fd, [TRASH_DIR, token])
        try:
            st = _lstat(tfd, name, not_found="Trashed item not found")
            dfd = _walk(base_fd, parent, create=True)
            try:
                new = _rename_unique(tfd, name, dfd, stat.S_ISDIR(st.st_mode))
            finally:
                os.close(dfd)
        finally:
            os.close(tfd)
        trash_fd = _walk(base_fd, [TRASH_DIR])
        try:
            shutil.rmtree(token, dir_fd=trash_fd, ignore_errors=True)
        finally:
            os.close(trash_fd)
    finally:
        os.close(base_fd)
    return {"name": new, "path": "/".join([*parent, new])}


def trash_purge(base: Path, token: str) -> None:
    """Permanently remove a trashed item's bytes (best-effort)."""
    token = _token(token)
    try:
        with _dir(base, [TRASH_DIR]) as trash_fd:
            shutil.rmtree(token, dir_fd=trash_fd, ignore_errors=True)
    except HTTPException:
        pass  # no trash bucket at all: nothing to purge
