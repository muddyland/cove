"""Fixtures: a canned Cove status and a config entry pointing at it."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cove.const import DOMAIN

URL = "https://cove.example.com"
API = URL + "/api/integration/v1"
DESK = "a" * 32
ZONE = "z" * 32

STATUS: dict[str, Any] = {
    "instance_id": "inst0001",
    "generated_at": "2026-10-27T10:00:00+00:00",
    "users": {"total": 4, "admins": 1, "active": 2, "on_thin_clients": 1},
    "workspaces": {"total": 7, "running": 3, "starting": 1, "stopping": 0, "stopped": 2, "error": 1, "migrating": 0},
    "thin_clients_summary": {"total": 1, "online": 1, "in_use": 1},
    "zones": [
        {
            "id": ZONE, "zone_id": 0, "name": "Local", "local": True, "online": True, "last_seen_at": None,
            "workspaces_total": 7, "workspaces_running": 3, "users_active": 2,
            "cpu_percent": 42.5, "memory_used_bytes": 3 * 1024**3,
        }
    ],
    "thin_clients": [
        {
            "id": DESK, "name": "Desk", "location": "Office", "status": "enrolled", "online": True,
            "last_seen_at": "2026-10-27T09:59:50+00:00", "session_username": "bob",
            "session_started_at": "2026-10-27T09:00:00+00:00", "workspace_name": "lab", "workspace_owner": "bob",
            "workspace_since": "2026-10-27T09:01:00+00:00", "keep_awake_until": None,
            "screenshot_at": None, "pending_commands": 0, "display_on": True, "idle_seconds": 5,
            "media_playing": False, "volume": 80, "muted": False, "audio_output": "Speakers",
            "browser_running": True, "booted_at": "2026-10-27T08:00:00+00:00", "updates_available": 2,
            "reboot_required": False, "last_update_at": None, "last_error": None, "hostname": "desk",
            "ips": ["10.0.0.5"], "os": "Debian GNU/Linux 13", "agent_version": "1.1.0",
        }
    ],
    "token": {"name": "Home Assistant", "scope": "control"},
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def status() -> dict[str, Any]:
    return copy.deepcopy(STATUS)


@pytest.fixture
def entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Cove (cove.example.com)",
        unique_id=STATUS["instance_id"],
        data={CONF_URL: URL, CONF_API_TOKEN: "cit_secret", CONF_VERIFY_SSL: True},
    )
