"""A small client for Cove's integration API (``/api/integration/v1``)."""

from __future__ import annotations

from typing import Any

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=30)


class CoveError(Exception):
    """Cove could not be reached, or answered with an error."""


class CoveAuthError(CoveError):
    """The token is unknown, revoked, or may not do this."""


class CoveApi:
    """Calls Cove with an integration token."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self._session = session
        self._base = url.rstrip("/") + "/api/integration/v1"
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, path: str, json: dict[str, Any] | None = None) -> aiohttp.ClientResponse:
        try:
            resp = await self._session.request(
                method, self._base + path, json=json, headers=self._headers, timeout=TIMEOUT
            )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CoveError(f"Cannot reach Cove: {err}") from err
        if resp.status in (401, 403):
            detail = await _detail(resp)
            raise CoveAuthError(detail or f"HTTP {resp.status}")
        return resp

    async def _call(self, method: str, path: str, json: dict[str, Any] | None = None) -> Any:
        resp = await self._request(method, path, json)
        if resp.status >= 400:
            raise CoveError(await _detail(resp) or f"HTTP {resp.status}")
        if resp.status == 204:
            return None
        try:
            return await resp.json()
        except (aiohttp.ContentTypeError, ValueError) as err:
            # Usually a reverse proxy's login or error page in front of Cove.
            raise CoveError("Cove did not answer with JSON; check the URL") from err

    async def status(self) -> dict[str, Any]:
        return await self._call("GET", "/status")

    async def screenshot(self, client_id: str) -> bytes | None:
        resp = await self._request("GET", f"/thin-clients/{client_id}/screenshot.png")
        if resp.status == 404:
            return None
        if resp.status >= 400:
            raise CoveError(await _detail(resp) or f"HTTP {resp.status}")
        return await resp.read()

    async def command(self, client_id: str, kind: str, args: dict[str, Any] | None = None) -> None:
        await self._call("POST", f"/thin-clients/{client_id}/commands", {"kind": kind, "args": args or {}})

    async def sign_out(self, client_id: str) -> None:
        await self._call("POST", f"/thin-clients/{client_id}/signout")

    async def keep_awake(self, client_id: str, minutes: int) -> None:
        await self._call("POST", f"/thin-clients/{client_id}/keep-awake", {"minutes": minutes})


async def _detail(resp: aiohttp.ClientResponse) -> str | None:
    try:
        body = await resp.json()
    except (aiohttp.ContentTypeError, ValueError):
        return None
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail if isinstance(detail, str) else None
