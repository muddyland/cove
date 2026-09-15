"""LinuxServer proot-apps: the catalog, installed-app state, and update checks.

The catalog is the set of app directories under ``apps/`` in the
linuxserver/proot-apps repo. We fetch it once and cache it for the process
lifetime (it changes rarely); failures degrade to an empty list.

Installed apps and background tasks are reported by the driver script running
inside a workspace (``scripts/install-proot-apps.sh``). That output comes from a
container its user controls, so every parser here is strict: fields are
validated against tight patterns and anything malformed is dropped.

An installed app is up to date when the layer digest proot-apps recorded at
install time (its ``SHALAYER`` file) matches the one ghcr.io currently serves
for the app's tag — the same comparison ``proot-apps update`` makes. The lookup
runs here rather than in the workspace so results are shared and cached across
workspaces. Registry URLs are built only from validated app names against a
fixed repository, so a workspace can't point the control plane anywhere else.
See https://github.com/linuxserver/proot-apps
"""

import asyncio
import logging
import re
import time

import httpx

logger = logging.getLogger(__name__)

_CONTENTS_URL = "https://api.github.com/repos/linuxserver/proot-apps/contents/apps"
_cache: list[str] | None = None
_CATALOG_RETRY = 60
_catalog_retry_at = 0.0

APP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
TASK_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
TASK_OPS = ("install", "update", "remove")
TASK_STATES = ("queued", "running", "done", "failed", "interrupted")

# Folder proot-apps extracts a default-repository app into.
_FOLDER_PREFIX = "ghcr.io_linuxserver_proot-apps_"
_FOLDER_RE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_EPOCH_RE = re.compile(r"^[0-9]{1,12}$")
_MAX_APPS = 500
_MAX_TASKS = 50
_MAX_TASK_APPS = 50

_REGISTRY = "https://ghcr.io"
_REPOSITORY = "linuxserver/proot-apps"
_ARCHES = {"x86_64": "amd64", "aarch64": "arm64"}
_MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
_MAX_MANIFEST_BYTES = 1024 * 1024
_DIGEST_TTL = 30 * 60
# A failed lookup is retried sooner, but not on every poll: anonymous ghcr.io
# pulls are rate-limited and a registry outage shouldn't be hammered.
_DIGEST_FAIL_TTL = 5 * 60
_MAX_CHECKS = 100
_CHECK_CONCURRENCY = 4
# (app, arch) -> (expires_at, digest or None)
_digest_cache: dict[tuple[str, str], tuple[float, str | None]] = {}
_token_cache: tuple[float, str] | None = None
# (app, arch) -> the lookup already running for it
_inflight: dict[tuple[str, str], asyncio.Future] = {}


async def list_proot_apps() -> list[str]:
    global _cache, _catalog_retry_at
    if _cache is not None:
        return _cache
    # After a failure, don't make every caller wait on GitHub again at once.
    if time.monotonic() < _catalog_retry_at:
        raise RuntimeError("proot-apps catalog recently unavailable")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                _CONTENTS_URL,
                headers={"Accept": "application/vnd.github+json"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        _catalog_retry_at = time.monotonic() + _CATALOG_RETRY
        raise
    apps = sorted(item["name"] for item in data if item.get("type") == "dir")
    _cache = apps
    logger.info("Loaded %d proot-apps from LinuxServer", len(apps))
    return apps


# Upstream's per-app metadata (display name, icon file). Icons are served
# straight from GitHub, like image logos: an <img> can't run an SVG's scripts, and
# GitHub sends them with a sandboxing CSP and nosniff.
_METADATA_URL = "https://raw.githubusercontent.com/linuxserver/proot-apps/master/metadata/metadata.yml"
_ICON_BASE = "https://raw.githubusercontent.com/linuxserver/proot-apps/master/metadata/img/"
_ICON_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.(svg|png)$")
_MAX_METADATA_BYTES = 1024 * 1024
_meta_cache: dict[str, dict] | None = None
_meta_retry_at = 0.0


def parse_metadata(text: str) -> dict[str, dict]:
    """``{app: {"icon_url", "full_name"}}`` from upstream's metadata.yml.

    A line parser for its fixed two-level layout rather than a YAML dependency;
    values are validated, so a malformed or unexpected entry just has no icon.
    """
    meta: dict[str, dict] = {}
    current: dict | None = None
    for line in text.splitlines():
        # Any new entry ends the previous one, even one whose name is rejected.
        m = re.match(r"^  - name:\s*(.*?)\s*$", line)
        if m:
            name = m.group(1)
            current = meta.setdefault(name, {"icon_url": None, "full_name": None}) if APP_NAME_RE.match(name) else None
            continue
        if current is None:
            continue
        m = re.match(r"^    (icon|full_name):\s*(.*?)\s*$", line)
        if not m:
            continue
        value = m.group(2).strip("\"'").strip()
        if m.group(1) == "icon" and _ICON_RE.match(value):
            current["icon_url"] = _ICON_BASE + value
        elif m.group(1) == "full_name" and value and len(value) <= 80 and value.isprintable():
            current["full_name"] = value
    return meta


async def app_metadata() -> dict[str, dict]:
    """Upstream app metadata, cached for the process; ``{}`` when unavailable
    (retried after a minute)."""
    global _meta_cache, _meta_retry_at
    if _meta_cache is not None:
        return _meta_cache
    if time.monotonic() < _meta_retry_at:
        return {}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            resp = await client.get(_METADATA_URL)
        resp.raise_for_status()
        if len(resp.content) > _MAX_METADATA_BYTES:
            raise ValueError("metadata too large")
        _meta_cache = parse_metadata(resp.text)
        logger.info("Loaded proot-apps metadata for %d apps", len(_meta_cache))
        return _meta_cache
    except Exception as exc:
        logger.info("proot-apps metadata unavailable: %s", exc)
        _meta_retry_at = time.monotonic() + _CATALOG_RETRY
        return {}


def split_apps(text: str | None) -> list[str]:
    """A stored proot_apps value as a de-duplicated list, order kept."""
    seen: list[str] = []
    for tok in re.split(r"[,\s]+", (text or "").strip()):
        if tok and tok not in seen:
            seen.append(tok)
    return seen


def _lines(raw: bytes) -> list[list[str]]:
    return [line.split("\t") for line in raw.decode("utf-8", errors="replace").splitlines()]


def parse_listing(raw: bytes) -> dict:
    """Parse the driver's ``list`` output into ``{arch, available, apps}``.

    ``apps`` holds ``{folder, name, digest, downloading}``; ``name`` is None for
    an app installed from some other image, which Cove can't check or manage.
    """
    arch: str | None = None
    available = False
    apps: list[dict] = []
    for fields in _lines(raw):
        if fields[0] == "ARCH" and len(fields) == 2:
            arch = _ARCHES.get(fields[1])
        elif fields[0] == "PROOT" and len(fields) == 2:
            available = fields[1] == "1"
        elif fields[0] == "APP" and len(fields) == 4 and len(apps) < _MAX_APPS:
            folder, digest, downloading = fields[1], fields[2], fields[3]
            if not _FOLDER_RE.match(folder):
                continue
            name = folder[len(_FOLDER_PREFIX):] if folder.startswith(_FOLDER_PREFIX) else None
            if name is not None and not APP_NAME_RE.match(name):
                name = None
            apps.append(
                {
                    "folder": folder,
                    "name": name,
                    "digest": digest if _DIGEST_RE.match(digest) else None,
                    "downloading": downloading == "1",
                }
            )
    return {"arch": arch, "available": available, "apps": apps}


def _epoch(value: str) -> int | None:
    return int(value) if _EPOCH_RE.match(value) else None


def _app_list(value: str) -> list[str] | None:
    apps = value.split(" ") if value else []
    if len(apps) > _MAX_TASK_APPS or not all(APP_NAME_RE.match(a) for a in apps):
        return None
    return apps


def parse_tasks(raw: bytes) -> list[dict]:
    """Parse the driver's ``tasks`` output. Malformed rows are dropped whole."""
    tasks: list[dict] = []
    for fields in _lines(raw):
        if fields[0] != "TASK" or len(fields) != 12 or len(tasks) >= _MAX_TASKS:
            continue
        (_, task_id, op, state, exit_code, created, started, finished, done, current, apps, failed) = fields
        app_list = _app_list(apps)
        failed_list = _app_list(failed)
        if (
            not TASK_ID_RE.match(task_id)
            or op not in TASK_OPS
            or state not in TASK_STATES
            or not app_list
            or failed_list is None
            or (current and not APP_NAME_RE.match(current))
        ):
            continue
        done_count = _epoch(done) or 0
        tasks.append(
            {
                "id": task_id,
                "op": op,
                "state": state,
                "exit_code": _epoch(exit_code),
                "apps": app_list,
                "failed_apps": failed_list,
                "current_app": current or None,
                "done_count": min(done_count, len(app_list)),
                "created_at": _epoch(created),
                "started_at": _epoch(started),
                "finished_at": _epoch(finished),
            }
        )
    return tasks


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean_log(raw: bytes) -> str:
    """Task output as plain text: curl's carriage-return progress meter keeps
    only its last frame per line, and ANSI/control characters are stripped."""
    text = _ANSI_RE.sub("", raw.decode("utf-8", errors="replace"))
    lines = [line.rstrip("\r").rsplit("\r", 1)[-1] for line in text.split("\n")]
    return _CONTROL_RE.sub("", "\n".join(lines))


# ── Update checks ─────────────────────────────────────────────────────────────

def _json_body(resp: httpx.Response) -> dict | None:
    if resp.status_code != 200 or len(resp.content) > _MAX_MANIFEST_BYTES:
        return None
    try:
        body = resp.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


async def _registry_token(client: httpx.AsyncClient) -> str | None:
    global _token_cache
    now = time.monotonic()
    if _token_cache and _token_cache[0] > now:
        return _token_cache[1]
    resp = await client.get(
        f"{_REGISTRY}/token", params={"scope": f"repository:{_REPOSITORY}:pull"}
    )
    body = _json_body(resp)
    token = body.get("token") if body else None
    if not isinstance(token, str) or not token:
        return None
    expires_in = body.get("expires_in")
    ttl = expires_in if isinstance(expires_in, int) and expires_in > 0 else 300
    # Refresh a minute early so a token never expires mid-lookup.
    _token_cache = (now + max(ttl - 60, 30), token)
    return token


def _first_layer(manifest: dict) -> str | None:
    layers = manifest.get("layers")
    if isinstance(layers, list) and layers and isinstance(layers[0], dict):
        digest = layers[0].get("digest")
        if isinstance(digest, str) and _DIGEST_RE.match(digest):
            return digest
    return None


async def _fetch_manifest(client: httpx.AsyncClient, token: str, reference: str) -> dict | None:
    resp = await client.get(
        f"{_REGISTRY}/v2/{_REPOSITORY}/manifests/{reference}",
        headers={"Authorization": f"Bearer {token}", "Accept": _MANIFEST_ACCEPT},
    )
    return _json_body(resp)


async def _latest_digest(client: httpx.AsyncClient, token: str, app: str, arch: str) -> str | None:
    """The layer digest ``proot-apps`` would install for ``app`` on ``arch``,
    resolved the way its ``get_blob_sha`` does."""
    manifest = await _fetch_manifest(client, token, app)
    if manifest is None:
        return None
    if "layers" in manifest:
        return _first_layer(manifest)
    entries = manifest.get("manifests")
    if not isinstance(entries, list):
        return None
    # Annotated entries are attestations, not images.
    images = [m for m in entries if isinstance(m, dict) and not m.get("annotations")]
    if len(images) > 1:
        images = [
            m for m in images
            if isinstance(m.get("platform"), dict) and m["platform"].get("architecture") == arch
        ]
    if len(images) != 1:
        return None
    digest = images[0].get("digest")
    if not isinstance(digest, str) or not _DIGEST_RE.match(digest):
        return None
    child = await _fetch_manifest(client, token, digest)
    return _first_layer(child) if child else None


async def latest_digests(apps: list[str], arch: str) -> dict[str, str | None]:
    """Current registry layer digest per app (None where it couldn't be
    resolved). Cached per app and architecture; concurrent callers asking for
    the same app share one lookup, so parallel requests can't multiply the
    registry traffic."""
    if arch not in _ARCHES.values():
        return {app: None for app in apps}
    now = time.monotonic()
    loop = asyncio.get_running_loop()
    result: dict[str, str | None] = {}
    waiting: dict[str, asyncio.Future] = {}
    owned: dict[str, asyncio.Future] = {}
    for app in dict.fromkeys(apps):
        key = (app, arch)
        hit = _digest_cache.get(key)
        if not APP_NAME_RE.match(app):
            result[app] = None
        elif hit and hit[0] > now:
            result[app] = hit[1]
        elif key in _inflight:
            waiting[app] = _inflight[key]
        elif len(owned) < _MAX_CHECKS:
            owned[app] = _inflight[key] = loop.create_future()
        else:
            result[app] = None

    try:
        if owned:
            await _lookup(owned, arch, result)
    finally:
        # Whatever happened, release the lookups this call claimed so no other
        # caller waits on them forever.
        for app, future in owned.items():
            _inflight.pop((app, arch), None)
            if not future.done():
                future.set_result(result.get(app))
    for app, future in waiting.items():
        result[app] = await future
    return result


async def _lookup(owned: dict[str, asyncio.Future], arch: str, result: dict[str, str | None]) -> None:
    sem = asyncio.Semaphore(_CHECK_CONCURRENCY)

    async def check(client: httpx.AsyncClient, token: str, app: str) -> None:
        async with sem:
            try:
                digest = await _latest_digest(client, token, app, arch)
            except httpx.HTTPError as exc:
                logger.info("proot-apps update check for %s failed: %s", app, exc)
                digest = None
        ttl = _DIGEST_TTL if digest else _DIGEST_FAIL_TTL
        _digest_cache[(app, arch)] = (time.monotonic() + ttl, digest)
        result[app] = digest
        owned[app].set_result(digest)

    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        try:
            token = await _registry_token(client)
        except httpx.HTTPError as exc:
            logger.info("proot-apps update check: registry token failed: %s", exc)
            token = None
        if not token:
            # Unknown, not "up to date" — and not cached, so the next open retries.
            for app in owned:
                result[app] = None
            return
        await asyncio.gather(*(check(client, token, app) for app in owned))
