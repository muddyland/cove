"""Docker exec that works through HTTP proxies.

docker-py starts an exec by asking the daemon to *upgrade* the connection
(``Connection: Upgrade`` / ``Upgrade: tcp``), then reads the output straight off
the raw socket. That only works when nothing sits between the client and the
daemon. A remote zone's Docker API is served by the cove-agent reverse proxy (an
ASGI app, behind Traefik), which can't hijack a connection: the upgrade is lost,
the output arrives re-framed as an HTTP chunked body, and docker-py reads the
chunk framing as output (``b'0\\r\\n\\r\\n'``) after the payload has already been
swallowed by the HTTP parser. Every exec on a zone (preview capture, the Files
browser, app installs, Tailscale status...) came back empty or garbled.

Without the ``Upgrade`` header the daemon answers ``200 OK`` and streams the same
multiplexed output as an ordinary HTTP body until the exec ends, which every
proxy relays correctly. ``ProxySafeAPIClient.exec_start`` does exactly that and
keeps docker-py's return shapes, so ``container.exec_run`` and friends work
unchanged on local and remote daemons alike.
"""

import struct

import docker
from docker.api.client import APIClient
from docker.types.daemon import CancellableStream
from urllib3.exceptions import ReadTimeoutError

_HEADER = struct.Struct(">BxxxL")  # stream id, 3 padding bytes, payload length


def _frames(chunks):
    """Split a non-TTY exec body into ``(stream_id, payload)`` frames, however
    the transport chunked it."""
    buf = bytearray()
    for chunk in chunks:
        buf += chunk
        while len(buf) >= _HEADER.size:
            stream_id, length = _HEADER.unpack_from(buf)
            end = _HEADER.size + length
            if len(buf) < end:
                break
            yield stream_id, bytes(buf[_HEADER.size:end])
            del buf[:end]


class _ExecBodySocket:
    """Socket-shaped view of an exec's HTTP body for callers that read it with
    ``settimeout``/``recv`` to bound every wait (``apps_command``). Yields the
    same multiplexed bytes a hijacked socket would, minus any HTTP framing."""

    def __init__(self, res):
        self._res = res

    def settimeout(self, value: float) -> None:
        # Same lookup docker-py uses for its raw exec socket: the response's
        # buffered reader wraps a SocketIO whose _sock is the connection.
        fp = getattr(getattr(self._res.raw, "_fp", None), "fp", None)
        sock = getattr(getattr(fp, "raw", None), "_sock", None)
        if sock is None:
            sock = getattr(getattr(self._res.raw, "_connection", None), "sock", None)
        if sock is not None:
            sock.settimeout(value)

    def recv(self, n: int) -> bytes:
        try:
            return self._res.raw.read1(n)
        except (ReadTimeoutError, OSError) as exc:
            if isinstance(exc, ReadTimeoutError) or "timed out" in str(exc):
                raise TimeoutError(str(exc)) from exc
            raise

    def close(self) -> None:
        self._res.close()


class ProxySafeAPIClient(APIClient):
    """docker-py ``APIClient`` whose attached execs are plain HTTP requests."""

    def exec_start(self, exec_id, detach=False, tty=False, stream=False, socket=False, demux=False):
        if detach:
            return super().exec_start(exec_id, detach=True, tty=tty)
        if isinstance(exec_id, dict):
            exec_id = exec_id.get("Id")
        res = self._post_json(
            self._url("/exec/{0}/start", exec_id),
            data={"Tty": tty, "Detach": False},
            stream=True,
        )
        self._raise_for_status(res)
        if socket:
            return _ExecBodySocket(res)

        chunks = res.iter_content(chunk_size=None)
        if tty:
            parts = ((c, None) for c in chunks) if demux else chunks
        elif demux:
            parts = (
                (payload, None) if sid != 2 else (None, payload)
                for sid, payload in _frames(chunks)
            )
        else:
            parts = (payload for _sid, payload in _frames(chunks))

        if stream:
            return CancellableStream(parts, res)
        try:
            if not demux:
                return b"".join(parts)
            out: list[bytes | None] = [None, None]
            for pair in parts:
                for i, data in enumerate(pair):
                    if data is not None:
                        out[i] = (out[i] or b"") + data
            return tuple(out)
        finally:
            res.close()


def proxy_safe(client: docker.DockerClient) -> docker.DockerClient:
    """Switch an existing client to proxy-safe execs (no extra state needed)."""
    client.api.__class__ = ProxySafeAPIClient
    return client
