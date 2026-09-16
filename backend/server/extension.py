"""The "Open in Cove" browser extension, offered to users as a zip.

The extension lives in its own repo and is vendored into ``extension/`` (see
scripts/vendor-extension.sh), so the running container can hand it out without
reaching the internet — the same self-contained-deployment rule the rest of Cove
follows.

The zip is built in memory on first request and kept until the files change.
Entries are written with a fixed timestamp so the same source always produces
byte-identical output, which keeps the ETag stable across restarts and replicas.
"""

import hashlib
import io
import logging
import zipfile
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)

# Where the vendored copy lives inside the container (and in a source checkout).
_DIRS = (Path("/app/extension"), Path(__file__).resolve().parents[2] / "extension")
# Everything inside the zip sits under this folder, so unzipping gives one tidy
# directory to point "Load unpacked" at rather than loose files.
ROOT_NAME = "open-in-cove"
# Fixed DOS timestamp (1980-01-01) for reproducible archives.
_FIXED_DATE = (1980, 1, 1, 0, 0, 0)
_SKIP = {".gitignore", ".DS_Store"}

_cache: "tuple[str, bytes, str] | None" = None  # (signature, zip bytes, etag)
_lock = Lock()


def extension_dir() -> "Path | None":
    for path in _DIRS:
        if (path / "manifest.json").is_file():
            return path
    return None


def _metadata(root: Path) -> dict:
    version = ""
    try:
        import json

        version = str(json.loads((root / "manifest.json").read_text()).get("version", ""))
    except Exception:  # noqa: BLE001 - a malformed manifest shouldn't break the page
        logger.warning("Could not read the extension manifest version")
    return {"version": version}


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.name not in _SKIP)


def _signature(files: list[Path]) -> str:
    """Identity of the vendored tree: path, size and mtime of every file."""
    parts = [f"{p}:{p.stat().st_size}:{p.stat().st_mtime_ns}" for p in files]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def build() -> "tuple[bytes, str, dict] | None":
    """The extension as ``(zip bytes, etag, metadata)``, or None when the copy is
    missing (a deployment built without it)."""
    root = extension_dir()
    if root is None:
        return None
    files = _files(root)
    if not files:
        return None
    signature = _signature(files)

    global _cache
    with _lock:
        if _cache and _cache[0] == signature:
            return _cache[1], _cache[2], _metadata(root)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in files:
                rel = path.relative_to(root).as_posix()
                info = zipfile.ZipInfo(f"{ROOT_NAME}/{rel}", date_time=_FIXED_DATE)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, path.read_bytes())
        data = buf.getvalue()
        etag = hashlib.sha256(data).hexdigest()[:32]
        _cache = (signature, data, etag)
        return data, etag, _metadata(root)
