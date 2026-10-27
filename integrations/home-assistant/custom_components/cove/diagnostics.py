"""Diagnostics download, with the token and network details redacted."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant

from .coordinator import CoveConfigEntry

REDACT = {CONF_API_TOKEN, CONF_URL, "ips", "hostname", "instance_id"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: CoveConfigEntry) -> dict[str, Any]:
    return {
        "entry": async_redact_data(dict(entry.data), REDACT),
        "status": async_redact_data(entry.runtime_data.data, REDACT),
    }
