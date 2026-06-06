import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from server import oidc as oidc_module
from server.config import get_settings
from server.migrations import run_migrations
from server.models import AuditLog
from server.routers import admin, auth, files, images, proot, users, workspaces

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent.parent / "static"

# Served (via Traefik's errors middleware) in place of the default Bad Gateway
# page when a workspace stream is unreachable. Self-contained (no external assets)
# and auto-refreshes, since it renders inside the workspace iframe.
_STREAM_ERROR_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta http-equiv="refresh" content="6" />
<title>Workspace unavailable</title>
<style>
  html,body{height:100%;margin:0}
  body{display:flex;align-items:center;justify-content:center;background:#0a0e14;
       color:#c8d3e0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  .box{text-align:center;padding:32px;max-width:420px}
  .code{font-size:13px;letter-spacing:3px;color:#ff4d6d;text-transform:uppercase}
  h1{font-size:18px;letter-spacing:2px;margin:14px 0 6px;color:#36e0c8;
     text-shadow:0 0 12px rgba(54,224,200,.5)}
  p{font-size:13px;line-height:1.6;color:#7f8ea3;margin:8px 0}
  .dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#36e0c8;
       margin-right:7px;animation:pulse 1.4s ease-in-out infinite;vertical-align:middle}
  @keyframes pulse{0%,100%{opacity:.3}50%{opacity:1}}
  a{color:#36e0c8;text-decoration:none}
</style></head>
<body><div class="box">
  <div class="code">Stream {{CODE}}</div>
  <h1>Workspace unavailable</h1>
  <p><span class="dot"></span>The desktop isn't responding yet — it may still be
     starting up or briefly restarting.</p>
  <p>This page retries automatically. <a href="">Retry now</a></p>
</div></body></html>"""


def record_audit(
    db: Session,
    action: str,
    *,
    detail: Optional[str] = None,
    user=None,
    ip: Optional[str] = None,
) -> None:
    """Persist an audit log entry. Best-effort: never raises into callers."""
    try:
        entry = AuditLog(
            ts=datetime.now(timezone.utc),
            user_id=user.id if user is not None else None,
            username=user.username if user is not None else None,
            action=action,
            detail=detail,
            ip=ip,
        )
        db.add(entry)
        db.commit()
    except Exception as exc:  # pragma: no cover - audit must not break requests
        logger.warning("Failed to record audit (%s): %s", action, exc)
        try:
            db.rollback()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()
    settings = get_settings()
    if not settings.cookie_secure:
        logger.warning(
            "COVE_COOKIE_SECURE is false: session cookies are sent over plaintext "
            "HTTP and can be captured on the network. This is only safe for local "
            "(localhost) use — for any networked or public deployment serve Cove "
            "over HTTPS (docker-compose.prod.yml) and set COVE_COOKIE_SECURE=true."
        )
    if settings.oidc_enabled:
        try:
            await oidc_module.fetch_discovery()
            logger.info("OIDC discovery loaded from %s", settings.oidc_issuer)
        except Exception as exc:
            logger.warning("OIDC discovery failed: %s — OIDC may not work correctly", exc)

    seed_task = asyncio.create_task(_seed_catalog_if_empty())

    from server.docker_manager import get_docker_manager
    monitor_task = asyncio.create_task(_status_monitor(get_docker_manager()))
    try:
        yield
    finally:
        monitor_task.cancel()
        seed_task.cancel()


async def _seed_catalog_if_empty():
    """On first run with an empty catalog, auto-populate from LinuxServer.

    Best-effort: runs as a background task so a slow/unreachable API never
    blocks startup, and failures are logged rather than fatal.
    """
    from sqlalchemy import func, select

    from server.catalog import fetch_catalog
    from server.db import SessionLocal
    from server.models import WorkspaceImage
    from server.routers.images import upsert_catalog

    db = SessionLocal()
    try:
        count = db.scalar(select(func.count()).select_from(WorkspaceImage))
        if count and count > 0:
            return
        specs = await fetch_catalog()
        result = upsert_catalog(db, specs)
        logger.info(
            "Seeded %d workspace images from LinuxServer on first run", result["added"]
        )
    except Exception as exc:
        logger.warning("Catalog auto-seed skipped: %s", exc)
    finally:
        db.close()


async def _status_monitor(dm):
    while True:
        try:
            await asyncio.sleep(10)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, dm.sync_workspace_statuses)
            await loop.run_in_executor(None, dm.enforce_runtime_limits)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("Status monitor error: %s", exc)


def create_app() -> FastAPI:
    app = FastAPI(title="Cove", lifespan=lifespan)

    app.include_router(auth.router)
    app.include_router(images.router)
    app.include_router(workspaces.router)
    app.include_router(admin.router)
    app.include_router(users.router)
    app.include_router(files.router)
    app.include_router(proot.router)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/__cove_error/{status}", include_in_schema=False)
    def stream_error(status: str):
        """Custom page served (via Traefik's errors middleware) when a workspace
        stream is unreachable — replaces Traefik's default Bad Gateway page.

        Shown inside the workspace iframe, so it auto-retries: a 5xx here usually
        means the desktop is still starting or briefly restarting.
        """
        code = status if status.isdigit() else "Error"  # avoid reflected XSS
        return HTMLResponse(_STREAM_ERROR_PAGE.replace("{{CODE}}", code))

    # Serve built frontend
    if STATIC_DIR.exists():
        app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            # Serve real root-level static files (favicon.svg, manifest, etc.)
            # when they exist; otherwise fall back to the SPA entrypoint.
            if full_path:
                candidate = (STATIC_DIR / full_path).resolve()
                if candidate.is_file() and STATIC_DIR.resolve() in candidate.parents:
                    return FileResponse(str(candidate))
            return FileResponse(str(STATIC_DIR / "index.html"))

    return app


app = create_app()
