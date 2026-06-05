"""Discover the available LinuxServer proot-apps.

The catalog is the set of app directories under ``apps/`` in the
linuxserver/proot-apps repo. We fetch it once and cache it for the process
lifetime (it changes rarely); failures degrade to an empty list.
See https://github.com/linuxserver/proot-apps
"""

import logging

import httpx

logger = logging.getLogger(__name__)

_CONTENTS_URL = "https://api.github.com/repos/linuxserver/proot-apps/contents/apps"
_cache: list[str] | None = None


async def list_proot_apps() -> list[str]:
    global _cache
    if _cache is not None:
        return _cache
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _CONTENTS_URL,
            headers={"Accept": "application/vnd.github+json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    apps = sorted(item["name"] for item in data if item.get("type") == "dir")
    _cache = apps
    logger.info("Loaded %d proot-apps from LinuxServer", len(apps))
    return apps
