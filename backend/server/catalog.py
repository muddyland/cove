"""Auto-populate the workspace image catalog from the LinuxServer.io API.

LinuxServer publishes metadata for all their images at
https://api.linuxserver.io/api/v1/images . We curate the subset that follows
the "webtop" model — KasmVNC desktops that listen on port 3000 and persist to
/config — since those are the images Cove can launch as workspaces. The other
"Remote Desktop" category entries (remote-desktop *clients*, the full Kasm
platform, etc.) do not fit that model and are intentionally excluded.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

LINUXSERVER_API = "https://api.linuxserver.io/api/v1/images"

# Base images known to be webtop-compatible (KasmVNC desktop, port 3000, /config).
WEBTOP_COMPATIBLE: dict[str, str] = {
    "webtop": "Webtop",
    "kali-linux": "Kali Linux",
}

# Single-app browser images (KasmVNC, port 3000). Each accepts a startup URL via
# the given env var, enabling the "open a website in a browser" web-app flow.
BROWSERS: dict[str, tuple[str, str]] = {
    "chromium": ("Chromium", "CHROME_CLI"),
    "brave": ("Brave", "BRAVE_CLI"),
    "firefox": ("Firefox", "FIREFOX_CLI"),
}

WEBTOP_PORT = 3000


def _build_specs(linuxserver_images: list[dict]) -> list[dict]:
    """Transform the LinuxServer image list into Cove image specs."""
    specs: list[dict] = []
    by_name = {img["name"]: img for img in linuxserver_images}

    seen_names: set[str] = set()
    for base_name, display in WEBTOP_COMPATIBLE.items():
        img = by_name.get(base_name)
        if not img or img.get("deprecated"):
            continue

        # Keep every tag — for webtop the "latest" alias is the only Alpine-XFCE
        # variant, so dropping it would lose a desktop. Guard against duplicate
        # display names instead (name is unique in the DB).
        for tag in img.get("tags") or []:
            tag_name = tag.get("tag")
            if not tag_name:
                continue
            desc = (tag.get("desc") or "").strip()
            name = f"{display} — {desc}" if desc else f"{display} ({tag_name})"
            if name in seen_names:
                name = f"{display} ({tag_name})"
            if name in seen_names:
                continue
            seen_names.add(name)
            specs.append(
                {
                    "name": name,
                    "docker_image": f"lscr.io/linuxserver/{base_name}:{tag_name}",
                    "image_type": "desktop",
                    "internal_port": WEBTOP_PORT,
                    "url_env": None,
                    "description": img.get("description"),
                }
            )

    # Browser images — one entry each, latest tag, with a startup-URL env var.
    for base_name, (display, url_env) in BROWSERS.items():
        img = by_name.get(base_name)
        if not img or img.get("deprecated"):
            continue
        specs.append(
            {
                "name": display,
                "docker_image": f"lscr.io/linuxserver/{base_name}:latest",
                "image_type": "browser",
                "internal_port": WEBTOP_PORT,
                "url_env": url_env,
                "description": img.get("description"),
            }
        )

    return specs


async def fetch_catalog() -> list[dict]:
    """Fetch and curate the LinuxServer catalog. Raises on network/parse error."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(LINUXSERVER_API, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
    images = payload.get("data", {}).get("repositories", {}).get("linuxserver", [])
    specs = _build_specs(images)
    logger.info("Fetched %d workspace image specs from LinuxServer", len(specs))
    return specs
