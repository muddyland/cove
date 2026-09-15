import logging
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from types import SimpleNamespace

import docker.errors
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from server.deps import CurrentUser, DbSession
from server.models import Workspace
from server.proot import (
    APP_NAME_RE,
    TASK_ID_RE,
    app_metadata,
    clean_log,
    latest_digests,
    list_proot_apps,
    parse_listing,
    parse_tasks,
    split_apps,
)
from server.routers.workspaces import _audit, _get_workspace_or_404
from server.schemas import (
    ProotAppOut,
    ProotAppsOut,
    ProotTaskCreate,
    ProotTaskLogOut,
    ProotTaskOut,
    WorkspaceProotTasksOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["proot"])

# Driver exit codes (see scripts/install-proot-apps.sh).
_EXIT_INVALID = 2
_EXIT_BUSY = 3
_EXIT_NO_LOG = 4

# The navbar polls every workspace's task list, so each workspace is asked at
# most once per window and never by two requests at a time: a slow or
# unreachable zone then answers from cache instead of stacking up blocked
# threads.
_TASKS_TTL = 3.0
# Longest the overview waits on all workspaces before answering from cache.
_OVERVIEW_WAIT = 5.0
# In-workspace driver calls may take seconds (and a hostile workspace can make
# every one run to its deadline), so each workspace and each caller gets only a
# few at a time; the rest are refused rather than queued on server threads.
_MAX_CALLS_PER_WORKSPACE = 2
_MAX_CALLS_PER_USER = 4
_calls: dict[str, int] = {}
_calls_lock = threading.Lock()
# Serializes edits to saved proot_apps lists (read-modify-write).
_config_lock = threading.Lock()
_tasks_cache: dict[int, tuple[float, list[dict]]] = {}
_tasks_locks: dict[int, threading.Lock] = {}
_tasks_guard = threading.Lock()


@router.get("/proot-apps")
async def proot_apps(user: CurrentUser):
    """List the proot-app names available to install in desktop workspaces."""
    try:
        apps = await list_proot_apps()
    except Exception:
        apps = []
    meta = await app_metadata()
    return {"apps": apps, "meta": {a: meta[a] for a in apps if a in meta}}


@contextmanager
def _driver_slot(ws_id: int, user_id: int):
    keys = (f"ws:{ws_id}", f"user:{user_id}")
    limits = (_MAX_CALLS_PER_WORKSPACE, _MAX_CALLS_PER_USER)
    with _calls_lock:
        if any(_calls.get(k, 0) >= n for k, n in zip(keys, limits)):
            raise HTTPException(status_code=429, detail="The workspace is busy — try again in a moment")
        for k in keys:
            _calls[k] = _calls.get(k, 0) + 1
    try:
        yield
    finally:
        with _calls_lock:
            for k in keys:
                _calls[k] -= 1
                if not _calls[k]:
                    del _calls[k]


def _target(ws: Workspace, db) -> SimpleNamespace:
    """What a driver call needs, detached from the DB. Ends the request's read
    transaction first so a slow exec doesn't hold a pooled connection."""
    target = SimpleNamespace(id=ws.id, zone_id=ws.zone_id, container_id=ws.container_id)
    db.rollback()
    return target


def _manager(ws):
    from server.docker_manager import get_docker_manager

    return get_docker_manager(ws.zone_id)


def _require_desktop(ws: Workspace, *, allow_creating: bool = False) -> None:
    if ws.kind != "desktop":
        raise HTTPException(status_code=400, detail="proot-apps are only available on desktop workspaces")
    states = ("running", "creating") if allow_creating else ("running",)
    if ws.status not in states or not ws.container_id:
        raise HTTPException(status_code=409, detail="Start the workspace to manage its apps")


def _run(ws, args: list[str], *, detach: bool = False) -> tuple[int | None, bytes | None]:
    """Run a driver command, mapping an unreachable container to a 409/502."""
    from server.docker_manager import ProotUnavailable

    try:
        return _manager(ws).proot_command(ws, args, detach=detach)
    except ProotUnavailable as exc:
        raise HTTPException(status_code=409, detail="Workspace container is not available") from exc
    except (docker.errors.APIError, docker.errors.DockerException, OSError) as exc:
        logger.warning("proot-apps driver failed for workspace %s: %s", ws.id, exc)
        raise HTTPException(status_code=502, detail="Could not reach the workspace") from exc


def _fetch_tasks(ws) -> list[dict]:
    code, out = _run(ws, ["tasks"])
    if code != 0 or out is None:
        raise HTTPException(status_code=502, detail="The workspace returned an unreadable task list")
    return parse_tasks(out)


def _cached_tasks(ws) -> list[dict]:
    """Tasks for the navbar: fresh within _TASKS_TTL, else the last good answer
    while another request is already asking, or on failure."""
    with _tasks_guard:
        lock = _tasks_locks.setdefault(ws.id, threading.Lock())
    hit = _tasks_cache.get(ws.id)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    if not lock.acquire(blocking=False):
        return hit[1] if hit else []
    try:
        tasks = _fetch_tasks(ws)
        _tasks_cache[ws.id] = (time.monotonic() + _TASKS_TTL, tasks)
        return tasks
    except HTTPException:
        return hit[1] if hit else []
    finally:
        lock.release()


def _forget_tasks(ws_id: int) -> None:
    _tasks_cache.pop(ws_id, None)


@router.get("/workspaces/{ws_id}/proot-apps", response_model=ProotAppsOut)
async def workspace_proot_apps(ws_id: int, user: CurrentUser, db: DbSession, check: bool = True):
    """Installed proot-apps in a running desktop workspace, with whether each has
    a newer build on the registry (``check=false`` skips the registry)."""
    ws = _get_workspace_or_404(ws_id, user, db)
    _require_desktop(ws)
    configured = [a for a in split_apps(ws.proot_apps) if APP_NAME_RE.match(a)]
    target = _target(ws, db)
    with _driver_slot(target.id, user.id):
        code, out = await run_in_threadpool(_run, target, ["list"])
    if code != 0 or out is None:
        raise HTTPException(status_code=502, detail="The workspace returned an unreadable app list")
    listing = parse_listing(out)

    installed = {a["name"]: a for a in listing["apps"] if a["name"]}
    latest: dict[str, str | None] = {}
    checked = False
    check_failed = False
    if check and listing["arch"] and installed:
        # Only catalog apps are looked up. Folder names come from the workspace,
        # so without this a user could make the control plane query ghcr.io for
        # hundreds of made-up names — and anonymous pulls are rate-limited per
        # IP, which every workspace shares.
        try:
            catalog = set(await list_proot_apps())
        except Exception:
            catalog = set()
        if catalog:
            wanted = [n for n, a in installed.items() if n in catalog and a["digest"] and not a["downloading"]]
            try:
                latest = await latest_digests(wanted, listing["arch"])
                checked = True
                check_failed = any(latest.get(n) is None for n in wanted)
            except Exception:
                logger.exception("proot-apps update check failed for workspace %s", ws_id)

    meta = await app_metadata()
    apps: list[ProotAppOut] = []
    for a in listing["apps"]:
        name = a["name"]
        remote = latest.get(name) if name else None
        update = None
        if remote and a["digest"] and not a["downloading"]:
            update = remote != a["digest"]
        apps.append(
            ProotAppOut(
                name=name,
                folder=a["folder"],
                installed=not a["downloading"],
                downloading=a["downloading"],
                in_config=name in configured,
                installed_digest=a["digest"],
                latest_digest=remote,
                update_available=update,
                **meta.get(name or "", {}),
            )
        )
    # Saved in the workspace but not on disk (failed or pending install).
    for name in configured:
        if name not in installed:
            apps.append(
                ProotAppOut(
                    name=name, folder="", installed=False, downloading=False, in_config=True,
                    installed_digest=None, latest_digest=None, update_available=None,
                    **meta.get(name, {}),
                )
            )
    apps.sort(key=lambda a: (a.name or a.folder).lower())
    return ProotAppsOut(
        available=listing["available"], arch=listing["arch"], checked=checked, check_failed=check_failed, apps=apps
    )


@router.post("/workspaces/{ws_id}/proot-apps/tasks", response_model=ProotTaskOut, status_code=202)
def start_proot_task(ws_id: int, body: ProotTaskCreate, user: CurrentUser, db: DbSession, request: Request):
    """Queue an install/update/remove of proot-apps in a running workspace. It
    runs in the background inside the workspace, one task at a time."""
    ws = _get_workspace_or_404(ws_id, user, db)
    _require_desktop(ws)
    apps = list(dict.fromkeys(body.apps))
    for app in apps:
        if not APP_NAME_RE.match(app):
            raise HTTPException(status_code=400, detail=f"Invalid proot-app name: {app!r}")

    task_id = f"{int(time.time()):012d}-{secrets.token_hex(4)}"
    target = _target(ws, db)
    with _driver_slot(target.id, user.id):
        code, _ = _run(target, ["start", task_id, body.op, *apps])
        if code == _EXIT_BUSY:
            raise HTTPException(status_code=429, detail="Too many app tasks are already running in this workspace")
        if code == _EXIT_INVALID:
            raise HTTPException(status_code=400, detail="The workspace rejected the task")
        if code != 0:
            raise HTTPException(status_code=502, detail="Could not queue the task in the workspace")
        # A run that never starts shows as interrupted after a minute (see the driver).
        _run(target, ["run", task_id], detach=True)
    _forget_tasks(target.id)

    # Keep the saved list in step, so boots and migrations (which reinstall from
    # it rather than copying app trees) agree with what the user just did. Re-read
    # under the lock so two concurrent tasks can't drop each other's change.
    with _config_lock:
        db.refresh(ws)
        configured = split_apps(ws.proot_apps)
        if body.op == "install":
            configured += [a for a in apps if a not in configured]
        elif body.op == "remove":
            configured = [a for a in configured if a not in apps]
        ws.proot_apps = " ".join(configured) or None
        db.commit()
    _audit(
        db, f"workspace.proot_apps.{body.op}", detail=f"{ws.public_id}: {' '.join(apps)}", user=user, request=request
    )

    return ProotTaskOut(
        id=task_id, op=body.op, state="queued", exit_code=None, apps=apps, failed_apps=[],
        current_app=None, done_count=0, created_at=int(time.time()), started_at=None, finished_at=None,
    )


@router.get("/workspaces/{ws_id}/proot-apps/tasks", response_model=list[ProotTaskOut])
def workspace_proot_tasks(ws_id: int, user: CurrentUser, db: DbSession):
    ws = _get_workspace_or_404(ws_id, user, db)
    _require_desktop(ws, allow_creating=True)
    target = _target(ws, db)
    with _driver_slot(target.id, user.id):
        tasks = _fetch_tasks(target)
    _tasks_cache[target.id] = (time.monotonic() + _TASKS_TTL, tasks)
    return tasks


@router.get("/workspaces/{ws_id}/proot-apps/tasks/{task_id}/log", response_model=ProotTaskLogOut)
def proot_task_log(ws_id: int, task_id: str, user: CurrentUser, db: DbSession):
    ws = _get_workspace_or_404(ws_id, user, db)
    _require_desktop(ws, allow_creating=True)
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="Invalid task id")
    target = _target(ws, db)
    with _driver_slot(target.id, user.id):
        code, out = _run(target, ["log", task_id])
    if code == _EXIT_NO_LOG:
        raise HTTPException(status_code=404, detail="Task not found")
    if code != 0 or out is None:
        raise HTTPException(status_code=502, detail="Could not read the task log")
    return ProotTaskLogOut(output=clean_log(out))


@router.post("/workspaces/{ws_id}/proot-apps/tasks/clear", status_code=204)
def clear_proot_tasks(ws_id: int, user: CurrentUser, db: DbSession):
    """Forget finished tasks (running and queued ones are kept)."""
    ws = _get_workspace_or_404(ws_id, user, db)
    _require_desktop(ws, allow_creating=True)
    target = _target(ws, db)
    with _driver_slot(target.id, user.id):
        code, _ = _run(target, ["clear"])
    if code != 0:
        raise HTTPException(status_code=502, detail="Could not clear tasks")
    _forget_tasks(target.id)


@router.get("/proot-tasks", response_model=list[WorkspaceProotTasksOut])
def my_proot_tasks(user: CurrentUser, db: DbSession):
    """Background app tasks across the caller's own live desktop workspaces."""
    rows = db.scalars(
        select(Workspace).where(
            Workspace.user_id == user.id,
            Workspace.status.in_(("running", "creating")),
            Workspace.container_id.is_not(None),
        )
    ).all()
    rows = [ws for ws in rows if ws.kind == "desktop"]
    if not rows:
        return []
    targets = [(SimpleNamespace(id=ws.id, zone_id=ws.zone_id, container_id=ws.container_id), ws.name) for ws in rows]
    db.rollback()
    pool = ThreadPoolExecutor(max_workers=min(8, len(rows)))
    try:
        # Threads may outlive this request's DB session, so they get plain values
        # rather than ORM rows that could try to lazy-load after it closes.
        futures = {target.id: pool.submit(_cached_tasks, target) for target, _ in targets}
        wait(futures.values(), timeout=_OVERVIEW_WAIT)
    finally:
        # Don't hold the response for a workspace that's slow to answer: its
        # thread finishes in the background and fills the cache for next time.
        pool.shutdown(wait=False)
    out = []
    for ws, name in targets:
        future = futures[ws.id]
        if future.done() and not future.exception():
            tasks = future.result()
        else:
            hit = _tasks_cache.get(ws.id)
            tasks = hit[1] if hit else []
        if tasks:
            out.append(WorkspaceProotTasksOut(workspace_id=ws.id, workspace_name=name, tasks=tasks))
    return out
