"""Polls Cove's status snapshot."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CoveApi, CoveAuthError, CoveError
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

type CoveConfigEntry = ConfigEntry[CoveCoordinator]


class CoveCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """One read of ``/status`` per interval feeds every entity."""

    config_entry: CoveConfigEntry
    # Device registry id of the server device, set during setup.
    hub_device_id: str

    def __init__(self, hass: HomeAssistant, entry: CoveConfigEntry, api: CoveApi) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.api = api

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.api.status()
        except CoveAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CoveError as err:
            raise UpdateFailed(str(err)) from err

    @property
    def can_control(self) -> bool:
        return (self.data or {}).get("token", {}).get("scope") == "control"

    def thin_client(self, client_id: str) -> dict[str, Any] | None:
        return next((c for c in (self.data or {}).get("thin_clients", []) if c["id"] == client_id), None)

    def zone(self, zone_id: str) -> dict[str, Any] | None:
        return next((z for z in (self.data or {}).get("zones", []) if z["id"] == zone_id), None)
