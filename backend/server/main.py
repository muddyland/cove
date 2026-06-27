import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from server import oidc as oidc_module
from server.config import get_settings
from server.migrations import run_migrations
from server.models import AuditLog
from server.routers import (
    admin,
    agent,
    auth,
    docs,
    enroll,
    files,
    images,
    internal,
    proot,
    users,
    workspaces,
    zones,
)

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
    if settings.agent_mode:
        # Agent mode: no catalog seeding, no multi-zone status monitor (the agent
        # has no workspace DB of its own — the control plane reconciles state).
        logger.info("Starting in agent mode — control-plane features disabled.")
        # The control plane builds workspace bind-mount sources at
        # <storage>/.cove-scripts/<name> (the webtop custom-init scripts), which the
        # AGENT's Docker daemon resolves on the AGENT's filesystem. Stage them here
        # so they exist locally — otherwise the daemon creates empty directories and
        # the webtop's custom-init fails (breaking the stream). Same Cove image as
        # the control plane, so the staged scripts match.
        try:
            from server.docker_manager import _stage_helper_scripts

            _stage_helper_scripts()
        except Exception as exc:
            logger.warning("Agent: failed to stage helper scripts: %s", exc)
        yield
        return
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

    monitor_task = asyncio.create_task(_status_monitor())
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


async def _status_monitor():
    while True:
        try:
            await asyncio.sleep(10)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _sync_all_zones)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("Status monitor error: %s", exc)


def _sync_all_zones():
    """Reconcile every zone that has workspaces, plus the local zone, each
    against its own Docker daemon. One unreachable zone never blocks the rest."""
    from sqlalchemy import select

    from server.db import SessionLocal
    from server.docker_manager import get_docker_manager
    from server.models import Workspace

    db = SessionLocal()
    try:
        zone_ids = set(db.scalars(select(Workspace.zone_id).distinct()).all())
    finally:
        db.close()
    zone_ids.add(0)  # always reconcile the local zone
    for zid in zone_ids:
        try:
            dm = get_docker_manager(zid)
            dm.ping()  # raises if the zone's daemon is unreachable
        except Exception as exc:
            logger.warning("Zone %s unreachable: %s", zid, exc)
            _mark_zone_reachable(zid, False)
            continue
        _mark_zone_reachable(zid, True)
        try:
            dm.sync_workspace_statuses(zid)
            dm.enforce_runtime_limits(zid)
        except Exception as exc:
            logger.warning("Status monitor (zone %s): %s", zid, exc)


def _mark_zone_reachable(zone_id: int, ok: bool):
    """Track zone liveness without false-failing its workspaces: an unreachable
    remote zone flips to 'offline' (workspaces left as-is, not errored); it
    returns to 'enrolled' once it answers again."""
    if zone_id == 0:
        return
    from datetime import datetime, timezone

    from server.db import SessionLocal
    from server.models import Zone

    db = SessionLocal()
    try:
        zone = db.get(Zone, zone_id)
        if zone is None:
            return
        if ok:
            zone.last_seen_at = datetime.now(timezone.utc)
            if zone.status == "offline":
                zone.status = "enrolled"
        elif zone.status == "enrolled":
            zone.status = "offline"
        db.commit()
    finally:
        db.close()


_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def _allowed_origins(request: Request, settings) -> set[str]:
    """Origins permitted to make cookie-authenticated, state-changing API calls.

    The app's own origin (derived from the forwarded proto + Host that Traefik
    passes through) plus an optional explicitly-configured SPA origin.
    """
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    if not proto:
        proto = "https" if settings.cookie_secure else "http"
    host = request.headers.get("host", "")
    allowed = {f"{proto}://{host}"} if host else set()
    if settings.app_origin:
        allowed.add(settings.app_origin.rstrip("/"))
    return allowed


def _csrf_origin_ok(request: Request, settings) -> bool:
    """Verify the request's Origin/Referer against the allowed set.

    Browsers always attach Origin to credentialed cross-origin mutations, so a
    workspace origin ({id}.{domain}) — which is *same-site* to the SPA and would
    otherwise carry the host-only session cookie — is rejected here. When neither
    Origin nor Referer is present (a non-browser client, never a CSRF vector) the
    request is allowed; bearer-token requests skip this check entirely.
    """
    allowed = _allowed_origins(request, settings)
    origin = request.headers.get("origin")
    if origin is not None:
        return origin.rstrip("/") in allowed
    referer = request.headers.get("referer")
    if referer:
        parts = urlsplit(referer)
        return f"{parts.scheme}://{parts.netloc}" in allowed
    return True


def create_app() -> FastAPI:
    app = FastAPI(title="Cove", lifespan=lifespan)

    @app.middleware("http")
    async def csrf_protect(request: Request, call_next):
        """Reject cross-origin, cookie-authenticated state-changing API requests.

        Only applies to mutating methods on /api/** that rely on an ambient auth
        cookie (no Authorization header). This is the defense against a hostile
        workspace origin acting as a confused deputy with the victim's session.
        """
        settings = get_settings()
        if (
            request.method.upper() not in _CSRF_SAFE_METHODS
            and request.url.path.startswith("/api/")
            and (
                request.cookies.get(settings.cookie_session_name)
                or request.cookies.get(settings.cookie_refresh_name)
            )
            and not request.headers.get("authorization")
            and not _csrf_origin_ok(request, settings)
        ):
            return JSONResponse(status_code=403, content={"detail": "Cross-origin request blocked"})
        return await call_next(request)

    if get_settings().agent_mode:
        # Agent mode exposes only the mTLS agent API + Docker proxy on one port.
        # No SPA, login, or control-plane routers.

        # Internal Traefik callbacks reach cove-agent over the cluster network
        # (plain HTTP, no client cert), NOT through the mTLS entrypoint — so they
        # carry no cert-info header. They expose nothing sensitive (the ForwardAuth
        # validates a stream token; the error page is static), so they must bypass
        # the CN pin or every workspace stream's ForwardAuth would 403.
        _cn_exempt = ("/agent/auth/forward", "/__cove_error", "/api/health", "/agent/health")

        @app.middleware("http")
        async def verify_client_cn(request: Request, call_next):
            """Pin the agent to its control plane's client cert: reject any request
            whose forwarded client-cert CN isn't the expected cove-cp-<zone> CN.
            Skipped when no expected CN is configured (dev/tests) or for internal
            Traefik callbacks (which have no client cert)."""
            from server.client_cert import CLIENT_CERT_INFO_HEADER, extract_client_cn

            expected = get_settings().agent_expected_client_cn
            if expected and not request.url.path.startswith(_cn_exempt):
                raw = request.headers.get(CLIENT_CERT_INFO_HEADER)
                cn = extract_client_cn(raw)
                if cn != expected:
                    logger.warning(
                        "Rejecting %s %s: client-cert CN %r != expected %r (cert-info header=%r)",
                        request.method, request.url.path, cn, expected, raw,
                    )
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "client certificate not authorized for this zone"},
                    )
            return await call_next(request)

        app.include_router(agent.router)

        @app.get("/api/health")
        def agent_health_root():
            return {"status": "ok", "role": "agent"}

        # The agent's Traefik attaches the same per-workspace cove-errors
        # middleware (by label), so the agent must serve the error page too.
        @app.get("/__cove_error/{status}", include_in_schema=False)
        def agent_stream_error(status: str):
            code = status if status.isdigit() else "Error"
            return HTMLResponse(_STREAM_ERROR_PAGE.replace("{{CODE}}", code))

        # Registered LAST: a catch-all that proxies the Docker API to the local
        # socket-proxy (policy-filtered). Specific routes above take precedence.
        from server.routers.docker_proxy import register_docker_proxy

        register_docker_proxy(app)
        return app

    app.include_router(auth.router)
    app.include_router(images.router)
    app.include_router(workspaces.router)
    app.include_router(admin.router)
    app.include_router(zones.router)
    app.include_router(zones.user_router)
    app.include_router(enroll.router)
    app.include_router(internal.router)
    app.include_router(users.router)
    app.include_router(files.router)
    app.include_router(proot.router)
    app.include_router(docs.router)

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
