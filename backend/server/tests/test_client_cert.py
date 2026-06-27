"""Per-zone client-cert CN pinning: header parsing + agent enforcement."""

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import server.main as main
from server.client_cert import extract_client_cn
from server.config import get_settings

HEADER = "X-Forwarded-Tls-Client-Cert-Info"


# ── CN extraction ──────────────────────────────────────────────────────────

def test_extract_simple():
    assert extract_client_cn('Subject="CN=cove-cp-abc"') == "cove-cp-abc"


def test_extract_url_encoded_with_issuer():
    raw = quote('Subject="CN=cove-cp-abc";Issuer="CN=Cove Zone CA"')
    assert extract_client_cn(raw) == "cove-cp-abc"


def test_extract_multi_attr_subject():
    assert extract_client_cn('Subject="C=US,CN=cove-cp-xyz,O=Cove"') == "cove-cp-xyz"


def test_extract_none_and_empty():
    assert extract_client_cn(None) is None
    assert extract_client_cn("") is None
    assert extract_client_cn('Subject="O=Cove"') is None


# ── agent enforcement middleware ───────────────────────────────────────────

@pytest.fixture
def pinned_agent(monkeypatch):
    monkeypatch.setenv("COVE_AGENT_MODE", "1")
    monkeypatch.setenv("COVE_AGENT_EXPECTED_CLIENT_CN", "cove-cp-zone1")
    get_settings.cache_clear()
    yield TestClient(main.create_app())
    get_settings.cache_clear()


# /agent/files is CN-checked (reached by the control plane over mTLS); /agent/health
# is exempt, so the CN tests use /agent/files.
_CHECKED = {"params": {"username": "alice", "path": ""}}


def test_accepts_matching_cn(pinned_agent):
    r = pinned_agent.get("/agent/files", headers={HEADER: 'Subject="CN=cove-cp-zone1"'}, **_CHECKED)
    assert r.status_code != 403, r.text  # CN passed -> route runs


def test_rejects_wrong_cn(pinned_agent):
    r = pinned_agent.get("/agent/files", headers={HEADER: 'Subject="CN=cove-cp-other"'}, **_CHECKED)
    assert r.status_code == 403


def test_rejects_missing_header_when_pinned(pinned_agent):
    # Fail closed: enforcement on but no forwarded cert info -> reject.
    assert pinned_agent.get("/agent/files", **_CHECKED).status_code == 403


def test_internal_forward_auth_exempt_from_cn(pinned_agent):
    # The agent's own ForwardAuth callback has no cert header (internal request)
    # and must NOT be CN-blocked, or every workspace stream's auth would 403.
    r = pinned_agent.get("/agent/auth/forward", headers={"X-Forwarded-Host": "x.ws.example.com"})
    assert r.status_code != 403


def test_no_enforcement_without_expected_cn(monkeypatch):
    monkeypatch.setenv("COVE_AGENT_MODE", "1")
    get_settings.cache_clear()
    try:
        c = TestClient(main.create_app())
        assert c.get("/agent/files", params={"username": "alice"}).status_code != 403
    finally:
        get_settings.cache_clear()
