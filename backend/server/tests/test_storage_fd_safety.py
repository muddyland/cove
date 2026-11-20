"""The file browser never follows a symlink and never reopens a checked path.

The tree under a user's base is writable from inside their workspaces while
``storage_local`` runs as root on the host, so each test plants what a workspace
process could plant (dangling links, links in place of folders, FIFOs, a folder
swapped for a link mid-operation) and checks nothing outside the base is read
or written.
"""

import asyncio
import io
import os
import stat
import threading
import time
import zipfile

import pytest
from fastapi import HTTPException

from server import storage_local


@pytest.fixture
def tree(tmp_path):
    base = tmp_path / "alice"
    outside = tmp_path / "outside"
    (base / "ws").mkdir(parents=True)
    outside.mkdir()
    return base.resolve(), outside


def _status(fn, *args):
    with pytest.raises(HTTPException) as ei:
        fn(*args)
    return ei.value.status_code


def _body(resp) -> bytes:
    async def collect():
        return b"".join([chunk async for chunk in resp.body_iterator])

    return asyncio.run(collect())


# ── copy: the reported critical (write through a dangling symlink) ────────────


def test_copy_never_writes_through_a_dangling_symlink(tree):
    base, outside = tree
    (base / "ws" / "x.py").write_text("payload")
    (base / "ws" / "out").mkdir()
    (base / "ws" / "out" / "x.py").symlink_to(outside / "planted.py")

    result = storage_local.copy(base, "ws/x.py", "ws/out")

    assert not (outside / "planted.py").exists()
    assert result == {"name": "x (2).py", "path": "ws/out/x (2).py"}
    assert (base / "ws" / "out" / "x (2).py").read_text() == "payload"
    assert (base / "ws" / "out" / "x.py").is_symlink()  # left alone


def test_copy_skips_dangling_symlinks_at_every_suffix(tree):
    base, outside = tree
    (base / "ws" / "x.py").write_text("payload")
    (base / "ws" / "out").mkdir()
    (base / "ws" / "out" / "x.py").symlink_to(outside / "a")
    (base / "ws" / "out" / "x (2).py").symlink_to(outside / "b")

    assert storage_local.copy(base, "ws/x.py", "ws/out")["name"] == "x (3).py"
    assert list(outside.iterdir()) == []


def test_folder_copy_never_creates_through_a_dangling_symlink(tree):
    base, outside = tree
    (base / "ws" / "src").mkdir()
    (base / "ws" / "src" / "f").write_text("in folder")
    (base / "ws" / "out").mkdir()
    (base / "ws" / "out" / "src").symlink_to(outside / "newdir")

    assert storage_local.copy(base, "ws/src", "ws/out")["name"] == "src (2)"
    assert not (outside / "newdir").exists()
    assert (base / "ws" / "out" / "src (2)" / "f").read_text() == "in folder"


def test_copy_drops_setid_bits(tree):
    base, _ = tree
    src = base / "ws" / "tool"
    src.write_bytes(b"\x7fELF")
    os.chmod(src, 0o6755)
    (base / "ws" / "out").mkdir()

    storage_local.copy(base, "ws/tool", "ws/out")

    mode = (base / "ws" / "out" / "tool").stat().st_mode
    assert stat.S_IMODE(mode) == 0o755


def test_copy_of_a_symlink_copies_the_link(tree):
    base, outside = tree
    (outside / "secret").write_text("SECRET")
    (base / "ws" / "k").symlink_to(outside / "secret")
    (base / "ws" / "out").mkdir()

    storage_local.copy(base, "ws/k", "ws/out")

    copied = base / "ws" / "out" / "k"
    assert copied.is_symlink()
    assert os.readlink(copied) == str(outside / "secret")


def test_folder_copy_keeps_links_and_skips_special_files(tree):
    base, outside = tree
    (outside / "secret").write_text("SECRET")
    src = base / "ws" / "src"
    src.mkdir()
    (src / "real").write_text("ok")
    (src / "link").symlink_to(outside / "secret")
    (src / "linkdir").symlink_to(outside)
    os.mkfifo(src / "fifo")

    storage_local.copy(base, "ws/src", "ws")

    dst = base / "ws" / "src (2)"
    assert (dst / "real").read_text() == "ok"
    assert (dst / "link").is_symlink() and (dst / "linkdir").is_symlink()
    assert not (dst / "fifo").exists()


# ── no component of a user path is ever followed ──────────────────────────────


def test_symlinked_folder_in_a_path_is_refused_everywhere(tree):
    base, _ = tree
    real = base / "ws" / "real"
    real.mkdir()
    (real / "f.txt").write_text("hi")
    # Points inside the base: allowed by the old resolve() check, but it is
    # exactly what a workspace swaps in mid-request, so it's never followed.
    (base / "ws" / "link").symlink_to(real)

    assert _status(storage_local.list_dir, base, "ws/link") == 400
    assert _status(storage_local.download_response, base, "ws/link/f.txt") == 400
    assert _status(storage_local.iter_zip, base, "ws/link") == 400
    assert _status(storage_local.copy, base, "ws/link/f.txt", "ws") == 400
    assert _status(storage_local.copy, base, "ws/real/f.txt", "ws/link") == 400
    assert _status(storage_local.move, base, "ws/real/f.txt", "ws/link") == 400
    assert _status(storage_local.trash_move, base, "ws/link/f.txt") == 400
    with pytest.raises(HTTPException) as ei:
        storage_local.save_upload(base, "ws/link", io.BytesIO(b"x").read, "new.txt", 1 << 20)
    assert ei.value.status_code == 400
    assert sorted(p.name for p in real.iterdir()) == ["f.txt"]


def test_dotdot_and_nul_are_refused(tree):
    base, _ = tree
    assert _status(storage_local.list_dir, base, "ws/../..") == 400
    assert _status(storage_local.list_dir, base, "a\x00b") == 400


def test_listing_reports_a_symlink_not_its_target(tree):
    base, outside = tree
    (outside / "big").write_bytes(b"x" * 5000)
    (base / "ws" / "l").symlink_to(outside / "big")
    (base / "ws" / "d").symlink_to(outside)

    entries = {e.name: e for e in storage_local.list_dir(base, "ws").entries}
    assert entries["l"].size != 5000
    assert entries["d"].type == "file"  # a link, not a folder to walk into


def test_upload_replaces_a_planted_symlink_instead_of_writing_through_it(tree):
    base, outside = tree
    (outside / "target").write_text("untouched")
    (base / "ws" / "f.txt").symlink_to(outside / "target")

    storage_local.save_upload(base, "ws", io.BytesIO(b"new").read, "f.txt", 1 << 20)

    assert (outside / "target").read_text() == "untouched"
    assert not (base / "ws" / "f.txt").is_symlink()
    assert (base / "ws" / "f.txt").read_text() == "new"


def test_upload_over_the_cap_leaves_nothing_behind(tree):
    base, _ = tree
    with pytest.raises(HTTPException) as ei:
        storage_local.save_upload(base, "ws", io.BytesIO(b"x" * 100).read, "big", 10)
    assert ei.value.status_code == 413
    assert list((base / "ws").iterdir()) == []


def test_upload_onto_a_folder_is_refused(tree):
    base, _ = tree
    (base / "ws" / "d").mkdir()
    with pytest.raises(HTTPException) as ei:
        storage_local.save_upload(base, "ws", io.BytesIO(b"x").read, "d", 1 << 20)
    assert ei.value.status_code == 400
    assert sorted(p.name for p in (base / "ws").iterdir()) == ["d"]


def test_move_never_lands_on_a_dangling_symlink(tree):
    base, outside = tree
    (base / "ws" / "a.txt").write_text("A")
    (base / "ws" / "out").mkdir()
    (base / "ws" / "out" / "a.txt").symlink_to(outside / "x")

    assert storage_local.move(base, "ws/a.txt", "ws/out")["name"] == "a (2).txt"
    assert list(outside.iterdir()) == []


def test_delete_of_a_symlinked_folder_removes_only_the_link(tree):
    base, outside = tree
    (outside / "keep").write_text("keep")
    (base / "ws" / "l").symlink_to(outside)

    storage_local.delete(base, "ws/l")

    assert (outside / "keep").exists()
    assert not (base / "ws" / "l").exists()


# ── special files are never opened for reading ────────────────────────────────


def test_fifo_is_refused_rather_than_hanging_a_worker(tree):
    base, _ = tree
    os.mkfifo(base / "ws" / "pipe")
    (base / "ws" / "d").mkdir()
    os.mkfifo(base / "ws" / "d" / "pipe")
    (base / "ws" / "d" / "ok").write_text("ok")

    done = {}

    def run():
        done["download"] = _status(storage_local.download_response, base, "ws/pipe")
        done["copy"] = _status(storage_local.copy, base, "ws/pipe", "ws/d")
        done["zip"] = zipfile.ZipFile(
            io.BytesIO(b"".join(storage_local.iter_zip(base, "ws/d")))
        ).namelist()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(10)
    assert not t.is_alive(), "a FIFO open blocked"
    assert done == {"download": 400, "copy": 400, "zip": ["d/ok"]}


# ── the race: a folder swapped for a symlink while the operation runs ────────


def test_download_is_served_from_the_file_that_was_checked(tree):
    base, outside = tree
    d = base / "ws" / "d"
    d.mkdir()
    (d / "f").write_text("mine")
    (outside / "f").write_text("SECRET")

    resp = storage_local.download_response(base, "ws/d/f")
    # Swap after the check, before the body is sent (when the old code
    # reopened the path by name).
    d.rename(base / "ws" / "d.old")
    d.symlink_to(outside)

    assert _body(resp) == b"mine"
    assert resp.headers["content-length"] == "4"


def _flipper(link: os.PathLike, real: os.PathLike, outside: os.PathLike, stop: threading.Event):
    """Keep swapping ``link`` between a real folder and a symlink to ``outside``."""
    tmp = str(link) + ".swap"
    while not stop.is_set():
        try:
            os.symlink(outside, tmp)
            os.rename(tmp, link)  # link -> outside
            os.unlink(link)
            os.rename(real, link)  # link is the real folder again
            os.rename(link, real)
        except OSError:
            pass


@pytest.mark.parametrize("op", ["upload", "copy", "download", "zip"])
def test_swapping_a_folder_for_a_symlink_never_escapes_the_base(tree, op):
    base, outside = tree
    real = base / "ws" / "real"
    real.mkdir()
    (real / "f").write_text("mine")
    (outside / "f").write_text("SECRET")
    (base / "ws" / "src").write_text("copied")
    link = base / "ws" / "d"

    stop = threading.Event()
    t = threading.Thread(target=_flipper, args=(link, real, outside, stop), daemon=True)
    t.start()
    leaked = []
    deadline = time.monotonic() + 1.5
    try:
        while time.monotonic() < deadline:
            try:
                if op == "upload":
                    storage_local.save_upload(base, "ws/d", io.BytesIO(b"x").read, "up", 1 << 20)
                elif op == "copy":
                    storage_local.copy(base, "ws/src", "ws/d")
                elif op == "download":
                    data = _body(storage_local.download_response(base, "ws/d/f"))
                    if b"SECRET" in data:
                        leaked.append(data)
                else:
                    data = b"".join(storage_local.iter_zip(base, "ws/d"))
                    if b"SECRET" in data:
                        leaked.append(data)
            except HTTPException:
                pass
    finally:
        stop.set()
        t.join()
    assert not leaked
    assert sorted(p.name for p in outside.iterdir()) == ["f"]
    assert (outside / "f").read_text() == "SECRET"


# ── workspace clone uses the same copy ────────────────────────────────────────


def test_clone_tree_copies_links_as_links_and_skips_special_files(tree):
    base, outside = tree
    (outside / "secret").write_text("SECRET")
    src = base / "ws"
    (src / "a").mkdir()
    (src / "a" / "f").write_text("mine")
    (src / "link").symlink_to(outside / "secret")
    (src / "linkdir").symlink_to(outside)
    os.mkfifo(src / "fifo")

    storage_local.clone_tree(src, base / "clone")

    clone = base / "clone"
    assert (clone / "a" / "f").read_text() == "mine"
    assert (clone / "link").is_symlink() and (clone / "linkdir").is_symlink()
    assert not (clone / "fifo").exists()
    with pytest.raises(FileExistsError):
        storage_local.clone_tree(src, clone)


def test_clone_tree_never_reads_through_a_swapped_folder(tree):
    base, outside = tree
    real = base / "ws" / "real"
    real.mkdir()
    (real / "f").write_text("mine")
    (outside / "f").write_text("SECRET")
    for i in range(50):
        (base / "ws" / f"pad{i}").write_text("x" * 100)
    link = base / "ws" / "d"

    stop = threading.Event()
    t = threading.Thread(target=_flipper, args=(link, real, outside, stop), daemon=True)
    t.start()
    try:
        deadline = time.monotonic() + 1.5
        n = 0
        while time.monotonic() < deadline:
            dst = base / f"clone{n}"
            n += 1
            storage_local.clone_tree(base / "ws", dst)
    finally:
        stop.set()
        t.join()
    leaked = [p for p in base.rglob("f") if not p.is_symlink() and p.read_text() == "SECRET"]
    assert not leaked
