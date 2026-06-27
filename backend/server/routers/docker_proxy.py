"""Docker API reverse-proxy served by the zone agent (agent mode only).

The control plane's per-zone Docker client connects to the agent's single mTLS
port; the agent's Traefik routes the Docker API paths here. This proxy forwards
them to the agent's *local* docker-socket-proxy — so the Docker daemon is never
exposed on a network port — and applies the create policy
(``server.docker_policy``) before forwarding ``containers/create``.

Registered as a catch-all *after* the agent's own routes so it only handles the
Docker API surface; anything else returns 404.
"""

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from server.config import get_settings
from server.docker_policy import check_create_policy

# Root path prefixes of the Docker Remote API (with and without a version prefix).
_DOCKER_PREFIXES = (
    "/v1.",
    "/_ping",
    "/version",
    "/info",
    "/containers",
    "/images",
    "/networks",
    "/volumes",
    "/exec",
)

_HOP_BY_HOP = {"host", "content-length", "connection", "keep-alive", "transfer-encoding"}


def _is_docker_path(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in _DOCKER_PREFIXES)


def register_docker_proxy(app: FastAPI) -> None:
    @app.api_route(
        "/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD"], include_in_schema=False
    )
    async def docker_proxy(full_path: str, request: Request):
        path = "/" + full_path
        if not _is_docker_path(path):
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        body = await request.body()
        if request.method == "POST" and path.endswith("/containers/create"):
            reason = check_create_policy(body)
            if reason:
                return JSONResponse(
                    {"message": f"blocked by Cove agent policy: {reason}"}, status_code=403
                )

        upstream = get_settings().agent_docker_socket_url.rstrip("/") + path
        fwd_headers = {
            k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP
        }

        client = httpx.AsyncClient(timeout=None)
        req = client.build_request(
            request.method,
            upstream,
            params=request.query_params,
            content=body or None,
            headers=fwd_headers,
        )
        resp = await client.send(req, stream=True)

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
