from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from server.config import get_settings
from server.deps import CurrentUser, DbSession
from server.models import User
from server.schemas import FileEntry, FileListing

router = APIRouter(prefix="/api/files", tags=["files"])


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        first = fwd.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _audit(db, action, *, detail=None, user=None, request=None):
    from server.main import record_audit

    ip = _client_ip(request) if request is not None else None
    record_audit(db, action, detail=detail, user=user, ip=ip)


def _user_base(user: User) -> Path:
    settings = get_settings()
    root = settings.storage_path or (settings.data_dir / "workspaces")
    base = (root / user.username).resolve()
    base.mkdir(parents=True, exist_ok=True)
    return base


def _resolve(base: Path, rel: str) -> Path:
    """Resolve a user-supplied relative path against base, rejecting traversal."""
    rel = (rel or "").lstrip("/")
    candidate = (base / rel).resolve()
    if candidate != base and base not in candidate.parents:
        raise HTTPException(status_code=400, detail="Invalid path")
    return candidate


@router.get("", response_model=FileListing)
def list_files(user: CurrentUser, db: DbSession, path: str = ""):
    base = _user_base(user)
    target = _resolve(base, path)
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


@router.get("/download")
def download_file(user: CurrentUser, db: DbSession, path: str):
    base = _user_base(user)
    target = _resolve(base, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="Cannot download a directory")
    return FileResponse(
        str(target),
        filename=target.name,
        content_disposition_type="attachment",
    )


@router.post("/upload", status_code=status.HTTP_201_CREATED)
def upload_file(
    user: CurrentUser,
    db: DbSession,
    request: Request,
    path: str = Form(""),
    file: UploadFile = File(...),
):
    base = _user_base(user)
    target_dir = _resolve(base, path)
    target_dir.mkdir(parents=True, exist_ok=True)
    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="Destination is not a directory")

    filename = Path(file.filename or "upload").name
    if not filename or filename in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    dest = _resolve(base, str(Path(path or "") / filename))

    with open(dest, "wb") as out:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)

    _audit(db, "files.upload", detail=str(dest.relative_to(base)), user=user, request=request)
    return {"name": filename, "path": str(dest.relative_to(base))}


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_path(user: CurrentUser, db: DbSession, request: Request, path: str):
    base = _user_base(user)
    target = _resolve(base, path)
    if target == base:
        raise HTTPException(status_code=400, detail="Cannot delete the root directory")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    import shutil

    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()

    _audit(db, "files.delete", detail=path, user=user, request=request)
