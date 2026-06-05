"""Unit tests for server.net.client_ip (no DB, no network)."""

from types import SimpleNamespace

from server.net import client_ip


def _request(xff=None, peer="9.9.9.9"):
    headers = {}
    if xff is not None:
        headers["x-forwarded-for"] = xff
    client = SimpleNamespace(host=peer) if peer is not None else None
    return SimpleNamespace(headers=headers, client=client)


def test_returns_rightmost_xff_hop():
    # client, proxy1, proxy2(trusted appended) -> rightmost is the real client.
    req = _request(xff="1.1.1.1, 2.2.2.2, 3.3.3.3")
    assert client_ip(req) == "3.3.3.3"


def test_single_xff_entry():
    req = _request(xff="8.8.8.8")
    assert client_ip(req) == "8.8.8.8"


def test_ignores_spoofed_leftmost_entry():
    # Attacker sets a fake leftmost value; we must not trust it.
    req = _request(xff="evil-spoof, 5.5.5.5")
    assert client_ip(req) == "5.5.5.5"


def test_falls_back_to_request_client():
    req = _request(xff=None, peer="9.9.9.9")
    assert client_ip(req) == "9.9.9.9"


def test_empty_xff_falls_back_to_client():
    req = _request(xff="   ", peer="9.9.9.9")
    assert client_ip(req) == "9.9.9.9"


def test_unknown_when_no_client():
    req = _request(xff=None, peer=None)
    assert client_ip(req) == "unknown"
