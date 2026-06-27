"""Serve the bundled product documentation (the repo's ``docs/*.md``) to the
SPA's in-app reader. Any authenticated user may read it; the content is
first-party and shipped inside the image."""

import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

from server.deps import CurrentUser

router = APIRouter(prefix="/api/docs", tags=["docs"])

# Slugs are filename stems — letters/digits/_/- only, no dots or slashes, so the
# `{slug}.md` join can't escape the docs directory.
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

# README first, then a reading order, then anything else alphabetically.
_ORDER = [
    "README", "installation", "configuration", "deployment", "user-guide",
    "workspaces", "networking", "zones", "authentication", "security",
    "administration", "api-reference", "troubleshooting",
]


def _docs_dir() -> Path:
    env = os.environ.get("COVE_DOCS_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    # Container flattens backend/ to /app -> /app/docs (parents[2]); in a dev
    # checkout the docs live at the repo root (parents[3]).
    for cand in (here.parents[2] / "docs", here.parents[3] / "docs"):
        if cand.is_dir():
            return cand
    return here.parents[2] / "docs"


def _title(path: Path) -> str:
    """First Markdown H1, else a prettified filename."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("# "):
                return s[2:].strip()
    except OSError:
        pass
    return path.stem.replace("-", " ").replace("_", " ").title()


def _entries() -> list[dict]:
    d = _docs_dir()
    files = {p.stem: p for p in d.glob("*.md")} if d.is_dir() else {}
    ordered = [s for s in _ORDER if s in files] + sorted(s for s in files if s not in _ORDER)
    return [{"slug": s, "title": _title(files[s])} for s in ordered]


@router.get("")
def list_docs(user: CurrentUser) -> list[dict]:
    return _entries()


@router.get("/{slug}")
def get_doc(slug: str, user: CurrentUser) -> dict:
    docs_dir = _docs_dir()
    path = (docs_dir / f"{slug}.md").resolve()
    if not _SLUG_RE.match(slug) or path.parent != docs_dir.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="doc not found")
    return {"slug": slug, "title": _title(path), "content": path.read_text(encoding="utf-8")}
