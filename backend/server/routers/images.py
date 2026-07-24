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
from server.icons import refresh_image_icons
from server.models import WorkspaceImage
from server.schemas import ImageCreate, ImageOut, ImageUpdate

router = APIRouter(prefix="/api/images", tags=["images"])


def upsert_catalog(db: Session, specs: list[dict]) -> dict:
    """Insert new catalog images and backfill upstream metadata on existing ones.

    Matched on docker_image, then falling back to name (which is UNIQUE): if the
    upstream ref/tag for a curated image changed, the existing row is found by
    name and its docker_image is updated in place — without the name fallback,
    the insert would hit "UNIQUE constraint failed: workspace_image.name". New
    rows are inserted. For existing rows only upstream-sourced metadata
    (docker_image, logo_url, description) is refreshed — admin edits like name,
    enabled, internal_port and url_env are preserved. A changed logo clears the
    baked icon so the next icon refresh re-bakes it. Returns {"added", "updated"}.
    """
    rows = db.scalars(select(WorkspaceImage)).all()
    by_docker = {row.docker_image: row for row in rows}
    by_name = {row.name: row for row in rows}
    added = 0
    updated = 0
    for spec in specs:
        row = by_docker.get(spec["docker_image"]) or by_name.get(spec["name"])
        if row is None:
            row = WorkspaceImage(**spec)
            db.add(row)
            by_docker[spec["docker_image"]] = row
            by_name[spec["name"]] = row
            added += 1
            continue
        changed = False
        # Matched by name with a stale ref -> adopt the catalog's current ref.
        if row.docker_image != spec["docker_image"]:
            row.docker_image = spec["docker_image"]
            changed = True
        if spec.get("logo_url") and row.logo_url != spec["logo_url"]:
            row.logo_url = spec["logo_url"]
            row.icon_png = None  # stale watermark — re-baked by refresh_image_icons
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
def images_pull_status(user: AdminUser, db: DbSession, zone_id: int = 0):
    """Local availability per image on a given zone: 'present', 'absent', or
    'pulling'. ``zone_id`` selects the node (0 = local control plane); each
    zone's daemon stores images independently.

    Checks run concurrently since each is a Docker daemon round-trip.
    """
    images = db.scalars(select(WorkspaceImage)).all()
    if not images:
        return {}

    from server.docker_manager import get_docker_manager
    dm = get_docker_manager(zone_id)

    results: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(images))) as ex:
        futures = {ex.submit(dm.pull_status, img.docker_image): img for img in images}
        for fut, img in futures.items():
            results[img.id] = fut.result()
    return results


@router.post("/{image_id}/pull")
def pull_image(image_id: int, user: AdminUser, db: DbSession, bg: BackgroundTasks, zone_id: int = 0):
    """Manually pull an image onto a zone in the background (zone_id selects the
    node, 0 = local). Poll /pull-status?zone_id=N for progress."""
    image = db.get(WorkspaceImage, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    from server.docker_manager import get_docker_manager
    dm = get_docker_manager(zone_id)
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
    # Bake the watermarked PWA icon for any image missing one (new rows, plus
    # rows whose logo just changed — upsert cleared their stale icon).
    icons_baked = await refresh_image_icons(db, only_missing=True)
    total = db.scalar(select(func.count()).select_from(WorkspaceImage))
    return {
        "added": result["added"],
        "updated": result["updated"] + logos_added,
        "icons_baked": icons_baked,
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
    if body.image_type not in ("desktop", "app", "browser"):
        raise HTTPException(status_code=400, detail="image_type must be 'desktop', 'app', or 'browser'")
    image = WorkspaceImage(**body.model_dump())
    # Auto-fetch the project logo for LinuxServer images when none was provided.
    if not image.logo_url:
        image.logo_url = await fetch_logo(image.docker_image)
    db.add(image)
    db.commit()
    db.refresh(image)
    # Bake the watermarked PWA icon from the logo (best-effort, off the launch path).
    if image.logo_url:
        await refresh_image_icons(db, only_missing=True)
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


def _remove_docker_image(docker_image: str, zone_id: int = 0) -> None:
    """Best-effort delete of an image on a zone's daemon, raising HTTP errors on
    conflict. Shared by the catalog delete (remove_image=true) and the image-only
    delete. 'absent' is treated as success (idempotent — nothing to remove).
    """
    from server.docker_manager import get_docker_manager

    result = get_docker_manager(zone_id).remove_image(docker_image)
    if result == "in_use":
        raise HTTPException(
            status_code=409,
            detail="That image is in use by a container and can't be removed. "
            "Stop/purge the workspaces using it first.",
        )
    if result == "error":
        raise HTTPException(status_code=502, detail="Failed to remove the Docker image")


@router.delete("/{image_id}/image")
def remove_image_layers(image_id: int, user: AdminUser, db: DbSession, zone_id: int = 0):
    """Delete the Docker image from a zone's daemon but keep the catalog entry
    (zone_id selects the node, 0 = local).

    The entry stays so the admin can re-pull it later; its pull-status flips
    back to 'absent' for that zone.
    """
    image = db.get(WorkspaceImage, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    _remove_docker_image(image.docker_image, zone_id)
    return {"status": "removed"}


@router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_image(
    image_id: int, user: AdminUser, db: DbSession, remove_image: bool = False, zone_id: int = 0
):
    """Delete a catalog entry. With remove_image=true, also docker-rm the image
    from the given zone's daemon (zone_id, 0 = local).

    When remove_image is set, the image is removed first so an in-use conflict
    aborts the whole operation (the catalog entry is left intact rather than
    leaving a dangling entry whose image couldn't be deleted).
    """
    image = db.get(WorkspaceImage, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    if remove_image:
        _remove_docker_image(image.docker_image, zone_id)
    db.delete(image)
    db.commit()
