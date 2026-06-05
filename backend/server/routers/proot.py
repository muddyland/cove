from fastapi import APIRouter

from server.deps import CurrentUser
from server.proot import list_proot_apps

router = APIRouter(prefix="/api", tags=["proot"])


@router.get("/proot-apps")
async def proot_apps(user: CurrentUser):
    """List the proot-app names available to install in desktop workspaces."""
    try:
        apps = await list_proot_apps()
    except Exception:
        apps = []
    return {"apps": apps}
