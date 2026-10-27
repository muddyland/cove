"""Base entities: the Cove server, its zones and its thin clients are devices."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from homeassistant.const import CONF_URL
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import CoveError
from .const import COMMAND_REFRESH_DELAY, DOMAIN, OPTIMISTIC_SECONDS
from .coordinator import CoveCoordinator


def parse_time(value: str | None) -> datetime | None:
    return dt_util.parse_datetime(value) if value else None


class CoveEntity(CoordinatorEntity[CoveCoordinator]):
    """Names come from each entity description's translation key; ``key``
    only makes the unique id."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CoveCoordinator, key: str) -> None:
        super().__init__(coordinator)

    @property
    def _url(self) -> str:
        return self.coordinator.config_entry.data[CONF_URL].rstrip("/")

    @property
    def _instance(self) -> str:
        return self.coordinator.data["instance_id"]


class CoveServerEntity(CoveEntity):
    """Counters for the whole server."""

    def __init__(self, coordinator: CoveCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{self._instance}_{key}"
        # Registered with its details at setup; this only links the entity.
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, self._instance)})


class CoveZoneEntity(CoveEntity):
    """A zone: a Docker host that runs workspaces."""

    def __init__(self, coordinator: CoveCoordinator, key: str, zone_id: str) -> None:
        super().__init__(coordinator, key)
        self.zone_id = zone_id
        zone = coordinator.zone(zone_id) or {}
        self._attr_unique_id = f"{self._instance}_zone_{zone_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"zone_{zone_id}")},
            name=f"Cove zone {zone.get('name', zone_id)}",
            manufacturer="Cove",
            model="Local zone" if zone.get("local") else "Zone",
            via_device_id=coordinator.hub_device_id,
            configuration_url=self._url + "/app/admin/zones",
        )

    @property
    def zone(self) -> dict[str, Any]:
        return self.coordinator.zone(self.zone_id) or {}

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.zone(self.zone_id) is not None


class CoveThinClientEntity(CoveEntity):
    """A thin client. Readings it reports are null while it is offline, which
    shows as unknown; control entities are unavailable until it returns."""

    # Control entities set this: a command queued for an offline device would
    # run whenever it comes back, which is never what an automation meant.
    _needs_online = False
    _refresh_unsub: Callable[[], None] | None = None

    def __init__(self, coordinator: CoveCoordinator, key: str, client_id: str) -> None:
        super().__init__(coordinator, key)
        self.client_id = client_id
        client = coordinator.thin_client(client_id) or {}
        self._attr_unique_id = f"{self._instance}_thin_client_{client_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"thin_client_{client_id}")},
            name=client.get("name", client_id),
            manufacturer="Cove",
            model="Thin client",
            sw_version=client.get("agent_version"),
            suggested_area=client.get("location"),
            via_device_id=coordinator.hub_device_id,
            configuration_url=self._url + "/app/admin/thin-clients",
        )

    @property
    def client(self) -> dict[str, Any]:
        return self.coordinator.thin_client(self.client_id) or {}

    @property
    def available(self) -> bool:
        client = self.coordinator.thin_client(self.client_id)
        if not super().available or client is None:
            return False
        return client["online"] or not self._needs_online

    async def _run(self, action: Callable[..., Any], *args: Any) -> None:
        """Call Cove, then refresh soon so the device's new state shows. A
        revoked token fails here and starts re-authentication on the next poll."""
        try:
            await action(self.client_id, *args)
        except CoveError as err:
            raise HomeAssistantError(f"Cove: {err}") from err

        async def _refresh(_now: Any) -> None:
            self._refresh_unsub = None
            await self.coordinator.async_request_refresh()

        if self._refresh_unsub:
            self._refresh_unsub()
        self._refresh_unsub = async_call_later(self.hass, COMMAND_REFRESH_DELAY, _refresh)

    async def async_will_remove_from_hass(self) -> None:
        if self._refresh_unsub:
            self._refresh_unsub()
            self._refresh_unsub = None
        await super().async_will_remove_from_hass()

    async def _command(self, kind: str, args: dict[str, Any] | None = None) -> None:
        await self._run(self.coordinator.api.command, kind, args)


class OptimisticMixin(CoveThinClientEntity):
    """Show the value just set while the device catches up: the next poll may
    land before the device has acted on the command."""

    _pending: tuple[Any, float] | None = None

    def _actual(self) -> Any:
        raise NotImplementedError

    def _set_pending(self, value: Any) -> None:
        self._pending = (value, time.monotonic() + OPTIMISTIC_SECONDS)
        self.async_write_ha_state()

    def _current(self) -> Any:
        return self._pending[0] if self._pending else self._actual()

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._pending and (self._actual() == self._pending[0] or time.monotonic() > self._pending[1]):
            self._pending = None
        super()._handle_coordinator_update()


def add_device_entities(
    coordinator: CoveCoordinator,
    async_add_entities: Callable[[Iterable[Entity]], None],
    *,
    server: Callable[[], Iterable[Entity]] | None = None,
    zone: Callable[[str], Iterable[Entity]] | None = None,
    thin_client: Callable[[str], Iterable[Entity]] | None = None,
) -> None:
    """Add entities for the server once, and for each zone and thin client as
    it first appears in Cove (one that was deleted and comes back is re-added)."""
    seen: dict[str, set[str]] = {"zones": set(), "thin_clients": set()}
    if server:
        async_add_entities(server())

    @callback
    def _check() -> None:
        if not coordinator.last_update_success:
            return
        new: list[Entity] = []
        for key, make in (("zones", zone), ("thin_clients", thin_client)):
            ids = {item["id"] for item in coordinator.data.get(key, [])}
            if make:
                for item_id in sorted(ids - seen[key]):
                    new.extend(make(item_id))
            seen[key] = ids
        if new:
            async_add_entities(new)

    _check()
    coordinator.config_entry.async_on_unload(coordinator.async_add_listener(_check))
