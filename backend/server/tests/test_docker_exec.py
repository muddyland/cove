"""Execs through a proxy that can't hijack connections (the zone agent)."""

import json
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import docker
import pytest

from server.docker_exec import ProxySafeAPIClient, _frames, proxy_safe
from server.docker_manager import _read_exec_stdout


def _frame(data: bytes, stream_id: int = 1) -> bytes:
    return struct.pack(">BxxxL", stream_id, len(data)) + data


class _FakeAgent(BaseHTTPRequestHandler):
    """Answers like a daemon behind the cove-agent proxy: the exec body comes
    back as an HTTP/1.1 chunked response, and a request asking to upgrade the
    connection is what docker-py used to send (recorded so tests can reject it)."""

    protocol_version = "HTTP/1.1"
    body = b""
    stall = 0.0
    seen_upgrade: list = []

    def log_message(self, *args):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.endswith("/exec/e1/json"):
            return self._json({"ExitCode": 3, "Running": False})
        if self.path.endswith("/containers/c1/json"):
            return self._json({"Id": "c1", "Name": "/cove-ws-1"})
        if self.path.endswith("/version"):
            return self._json({"ApiVersion": "1.41"})
        self._json({"message": "nope"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path.endswith("/containers/c1/exec"):
            return self._json({"Id": "e1"}, 201)
        if self.path.endswith("/exec/e1/start"):
            type(self).seen_upgrade.append(self.headers.get("Upgrade"))
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.docker.raw-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            body = type(self).body
            # Odd chunk sizes so frames straddle chunk boundaries.
            for i in range(0, len(body), 5):
                part = body[i:i + 5]
                self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
                self.wfile.flush()
            if type(self).stall:
                time.sleep(type(self).stall)
            self.wfile.write(b"0\r\n\r\n")
            return None
        self._json({"message": "nope"}, 404)


@pytest.fixture
def agent():
    _FakeAgent.body = _frame(b"hello ") + _frame(b"oops\n", 2) + _frame(b"world\n")
    _FakeAgent.stall = 0.0
    _FakeAgent.seen_upgrade = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAgent)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    client = proxy_safe(
        docker.DockerClient(base_url=f"tcp://127.0.0.1:{srv.server_address[1]}", version="1.41", timeout=5)
    )
    yield client
    srv.shutdown()
    srv.server_close()


def test_proxy_safe_swaps_the_api_class(agent):
    assert isinstance(agent.api, ProxySafeAPIClient)


def test_exec_run_reads_a_chunked_body_without_upgrading(agent):
    container = agent.containers.get("c1")
    code, out = container.exec_run(["true"])
    assert (code, out) == (3, b"hello oops\nworld\n")
    assert _FakeAgent.seen_upgrade == [None]


def test_exec_run_demux_splits_stdout_and_stderr(agent):
    code, (out, err) = agent.containers.get("c1").exec_run(["true"], demux=True)
    assert (code, out, err) == (3, b"hello world\n", b"oops\n")


def test_exec_start_stream_yields_payloads(agent):
    api = agent.api
    eid = api.exec_create("c1", ["true"])["Id"]
    assert list(api.exec_start(eid, stream=True)) == [b"hello ", b"oops\n", b"world\n"]


def test_exec_socket_view_feeds_the_bounded_reader(agent):
    api = agent.api
    eid = api.exec_create("c1", ["true"])["Id"]
    sock = api.exec_start(eid, socket=True)
    assert _read_exec_stdout(sock, time.monotonic() + 5, 1024) == b"hello world\n"


def test_exec_socket_view_honours_the_deadline(agent):
    _FakeAgent.stall = 3.0
    api = agent.api
    eid = api.exec_create("c1", ["true"])["Id"]
    sock = api.exec_start(eid, socket=True)
    started = time.monotonic()
    assert _read_exec_stdout(sock, time.monotonic() + 0.5, 1024) is None
    assert time.monotonic() - started < 2


def test_frames_reassemble_across_arbitrary_chunks():
    data = _frame(b"abc") + _frame(b"", 2) + _frame(b"defgh")
    chunks = [data[i:i + 3] for i in range(0, len(data), 3)]
    assert list(_frames(chunks)) == [(1, b"abc"), (2, b""), (1, b"defgh")]
