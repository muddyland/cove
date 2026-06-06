from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.catalog import (
    _build_specs,
    fetch_linuxserver_images,
    fetch_logo,
    linuxserver_base_name,
)
from server.deps import AdminUser, CurrentUser, DbSession
from server.models import WorkspaceImage
from server.schemas import ImageCreate, ImageOut, ImageUpdate

router = APIRouter(prefix="/api/images", tags=["images"])


def upsert_catalog(db: Session, specs: list[dict]) -> dict:
    """Insert new catalog images and backfill upstream metadata on existing ones.

    Matched on docker_image. New rows are inserted. For existing rows, only
    upstream-sourced metadata (logo_url, description) is refreshed — admin edits
    like name, enabled, internal_port and url_env are preserved. Returns
    {"added": n, "updated": m}.
    """
    existing = {row.docker_image: row for row in db.scalars(select(WorkspaceImage)).all()}
    added = 0
    updated = 0
    for spec in specs:
        row = existing.get(spec["docker_image"])
        if row is None:
            row = WorkspaceImage(**spec)
            db.add(row)
            existing[spec["docker_image"]] = row
            added += 1
            continue
        changed = False
        if spec.get("logo_url") and row.logo_url != spec["logo_url"]:
            row.logo_url = spec["logo_url"]
            changed = True
        if spec.get("description") and row.description != spec["description"]:
            row.description = spec["description"]
            changed = True
        if changed:
            updated += 1
    if added or updated:
        db.commit()
    return {"added": added, "updated": updated}


@router.get("", response_model=list[ImageOut])
def list_images(user: CurrentUser, db: DbSession):
    images = db.scalars(
        select(WorkspaceImage).where(WorkspaceImage.enabled.is_(True)).order_by(WorkspaceImage.name)
    ).all()
    return images


@router.get("/pull-status", response_model=dict[int, str])
def images_pull_status(user: AdminUser, db: DbSession):
    """Local availability per image: 'present', 'absent', or 'pulling'.

    Checks run concurrently since each is a Docker daemon round-trip.
    """
    images = db.scalars(select(WorkspaceImage)).all()
    if not images:
        return {}

    from server.docker_manager import get_docker_manager
    dm = get_docker_manager()

    results: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(images))) as ex:
        futures = {ex.submit(dm.pull_status, img.docker_image): img for img in images}
        for fut, img in futures.items():
            results[img.id] = fut.result()
    return results


@router.post("/{image_id}/pull")
def pull_image(image_id: int, user: AdminUser, db: DbSession, bg: BackgroundTasks):
    """Manually pull an image in the background. Poll /pull-status for progress."""
    image = db.get(WorkspaceImage, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    from server.docker_manager import get_docker_manager
    dm = get_docker_manager()
    if dm.is_pulling(image.docker_image):
        return {"status": "pulling"}
    bg.add_task(dm.pull_image_blocking, image.docker_image)
    return {"status": "pulling"}


@router.post("/sync")
async def sync_images(user: AdminUser, db: DbSession):
    """Auto-populate the catalog from the LinuxServer.io API (admin only).

    Also backfills missing logos for any LinuxServer image already in the DB
    (including manually-added ones outside the curated catalog).
    """
    try:
        images_raw = await fetch_linuxserver_images()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach LinuxServer API: {exc}")
    specs = _build_specs(images_raw)
    result = upsert_catalog(db, specs)
    logos_added = _backfill_logos(db, images_raw)
    total = db.scalar(select(func.count()).select_from(WorkspaceImage))
    return {
        "added": result["added"],
        "updated": result["updated"] + logos_added,
        "total": total,
    }


def _backfill_logos(db: Session, images_raw: list[dict]) -> int:
    """Fill in logos for LinuxServer images that don't have one yet.

    Covers images added manually (or outside the curated catalog) — matched to
    the upstream project logo by their lsio base name. Returns the count updated.
    """
    logos = {i.get("name"): i.get("project_logo") for i in images_raw}
    updated = 0
    for row in db.scalars(select(WorkspaceImage)).all():
        if row.logo_url:
            continue
        base = linuxserver_base_name(row.docker_image)
        if base and logos.get(base):
            row.logo_url = logos[base]
            updated += 1
    if updated:
        db.commit()
    return updated


@router.post("", response_model=ImageOut, status_code=status.HTTP_201_CREATED)
async def create_image(body: ImageCreate, user: AdminUser, db: DbSession):
    if body.image_type not in ("desktop", "link"):
        raise HTTPException(status_code=400, detail="image_type must be 'desktop' or 'link'")
    image = WorkspaceImage(**body.model_dump())
    # Auto-fetch the project logo for LinuxServer images when none was provided.
    if not image.logo_url:
        image.logo_url = await fetch_logo(image.docker_image)
    db.add(image)
    db.commit()
    db.refresh(image)
    return image


@router.patch("/{image_id}", response_model=ImageOut)
def update_image(image_id: int, body: ImageUpdate, user: AdminUser, db: DbSession):
    image = db.get(WorkspaceImage, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(image, field, value)
    db.commit()
    db.refresh(image)
    return image


@router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_image(image_id: int, user: AdminUser, db: DbSession):
    image = db.get(WorkspaceImage, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    db.delete(image)
    db.commit()
