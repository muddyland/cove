from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.catalog import fetch_catalog
from server.deps import AdminUser, CurrentUser, DbSession
from server.models import WorkspaceImage
from server.schemas import ImageCreate, ImageOut, ImageUpdate

router = APIRouter(prefix="/api/images", tags=["images"])


def upsert_catalog(db: Session, specs: list[dict]) -> int:
    """Insert any catalog specs whose docker_image isn't already present.

    Matches on docker_image so admins' manual edits and disabled flags are
    preserved across re-syncs. Returns the number of new rows added.
    """
    existing = {row for row in db.scalars(select(WorkspaceImage.docker_image)).all()}
    added = 0
    for spec in specs:
        if spec["docker_image"] in existing:
            continue
        db.add(WorkspaceImage(**spec))
        existing.add(spec["docker_image"])
        added += 1
    if added:
        db.commit()
    return added


@router.get("", response_model=list[ImageOut])
def list_images(user: CurrentUser, db: DbSession):
    images = db.scalars(select(WorkspaceImage).where(WorkspaceImage.enabled == True).order_by(WorkspaceImage.name)).all()
    return images


@router.post("/sync")
async def sync_images(user: AdminUser, db: DbSession):
    """Auto-populate the catalog from the LinuxServer.io API (admin only)."""
    try:
        specs = await fetch_catalog()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach LinuxServer API: {exc}")
    added = upsert_catalog(db, specs)
    total = db.scalar(select(func.count()).select_from(WorkspaceImage))
    return {"added": added, "total": total}


@router.post("", response_model=ImageOut, status_code=status.HTTP_201_CREATED)
def create_image(body: ImageCreate, user: AdminUser, db: DbSession):
    if body.image_type not in ("desktop", "link"):
        raise HTTPException(status_code=400, detail="image_type must be 'desktop' or 'link'")
    image = WorkspaceImage(**body.model_dump())
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
