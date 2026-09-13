"""Docker API reverse-proxy served by the zone agent (agent mode only).

The control plane's per-zone Docker client connects to the agent's single mTLS
port; the agent's Traefik routes the Docker API paths here. This proxy forwards
them to the agent's *local* docker-socket-proxy — so the Docker daemon is never
exposed on a network port — and applies ``server.docker_policy`` to every request
that could otherwise hand the caller the host: container/volume/network create,
network connect, exec-create, archive and rename.

Registered as a catch-all *after* the agent's own routes so it only handles the
Docker API surface; anything else returns 404.
"""

import re

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from server import docker_policy
from server.config import get_settings

# Root path prefixes of the Docker Remote API the control plane uses. The
# optional ``/v1.NN`` version prefix is stripped before matching, so a versioned
# path cannot reach an endpoint family that is not listed here (``/v1.41/build``,
# ``/v1.41/commit``, ``/v1.41/plugins``…).
_DOCKER_PREFIXES = (
    "/_ping",
    "/version",
    "/info",
    "/containers",
    "/images",
    "/networks",
    "/volumes",
    "/exec",
    "/system/df",
)
_VERSION_RE = re.compile(r"^/v1\.\d+(?=/|$)")

_HOP_BY_HOP = {"host", "content-length", "connection", "keep-alive", "transfer-encoding"}

# Endpoints whose body is streamed straight through (never buffered): an image
# load is hundreds of MB and carries nothing the policy inspects.
_STREAM_BODY_SUFFIXES = ("/images/load",)

_CONTAINER_OP_RE = re.compile(r"^/containers/([^/]+)/([^/]+)$")
_NETWORK_OP_RE = re.compile(r"^/networks/([^/]+)/([^/]+)$")


def _unversioned(path: str) -> str:
    return _VERSION_RE.sub("", path) or "/"


def _is_docker_path(path: str) -> bool:
    p = _unversioned(path)
    return any(p == x or p.startswith(x + "/") for x in _DOCKER_PREFIXES)


def _recreate_cmd() -> list[str]:
    from server.agent_update import _RECREATE_CMD

    return list(_RECREATE_CMD)


async def _container_name(
    client: httpx.AsyncClient, upstream_base: str, version: str, ref: str
) -> str | None:
    """Resolve a container id-or-name to its name (without the leading slash) by
    asking the local daemon, so name-based policy can't be dodged with an id."""
    try:
        resp = await client.get(f"{upstream_base}{version}/containers/{ref}/json", timeout=10)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        name = resp.json().get("Name") or ""
    except ValueError:
        return None
    return name.lstrip("/") or None


async def _policy_reason(
    client: httpx.AsyncClient, upstream_base: str, method: str, path: str, body: bytes
) -> str | None:
    """Apply the agent policy to one request; return a rejection reason or None."""
    p = _unversioned(path)
    m = _VERSION_RE.match(path)
    version = m.group(0) if m else ""
    if method == "POST" and p == "/containers/create":
        return docker_policy.check_create_policy(body)
    if method == "POST" and p == "/volumes/create":
        return docker_policy.check_volume_create_policy(body)
    if method == "POST" and p == "/networks/create":
        return docker_policy.check_network_create_policy(body)
    m = _NETWORK_OP_RE.match(p)
    if m and method == "POST" and m.group(2) == "connect":
        return docker_policy.check_network_connect_policy(body)
    m = _CONTAINER_OP_RE.match(p)
    if m:
        ref, op = m.group(1), m.group(2)
        if op == "rename":
            return "renaming containers is not allowed"
        if op in ("exec", "archive"):
            name = await _container_name(client, upstream_base, version, ref)
            if not name:
                return f"container {ref!r} not found"
            if op == "exec" and method == "POST":
                return docker_policy.check_exec_policy(name, body, _recreate_cmd())
            if op == "archive":
                return docker_policy.check_archive_policy(name)
    return None


def register_docker_proxy(app: FastAPI) -> None:
    @app.api_route(
        "/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD"], include_in_schema=False
    )
    async def docker_proxy(full_path: str, request: Request):
        path = "/" + full_path
        if not _is_docker_path(path):
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        upstream_base = get_settings().agent_docker_socket_url.rstrip("/")
        upstream = upstream_base + path
        fwd_headers = {
            k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP
        }

        client = httpx.AsyncClient(timeout=None)
        stream_body = request.method in ("POST", "PUT") and path.endswith(_STREAM_BODY_SUFFIXES)
        if stream_body:
            content = request.stream()
        else:
            body = await request.body()
            reason = await _policy_reason(client, upstream_base, request.method, path, body)
            if reason:
                await client.aclose()
                return JSONResponse(
                    {"message": f"blocked by Cove agent policy: {reason}"}, status_code=403
                )
            content = body or None

        req = client.build_request(
            request.method,
            upstream,
            params=request.query_params,
            content=content,
            headers=fwd_headers,
        )
        try:
            resp = await client.send(req, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            return JSONResponse({"message": f"agent docker proxy: {exc}"}, status_code=502)

        async def _body():
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()

        resp_headers = {
            k: v for k, v in resp.headers.items() if k.lower() not in _HOP_BY_HOP
        }
        return StreamingResponse(
            _body(),
            status_code=resp.status_code,
            headers=resp_headers,
            media_type=resp.headers.get("content-type"),
        )
