"""A thin client's volume."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CoveConfigEntry
from .entity import OptimisticMixin, add_device_entities

PARALLEL_UPDATES = 1

VOLUME = NumberEntityDescription(
    key="volume_level",
    translation_key="volume_level",
    native_min_value=0,
    # Above 100 PipeWire amplifies, for quiet monitor speakers.
    native_max_value=150,
    native_step=5,
    native_unit_of_measurement=PERCENTAGE,
    mode=NumberMode.SLIDER,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: CoveConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    if not coordinator.can_control:
        return
    add_device_entities(
        coordinator,
        async_add_entities,
        thin_client=lambda cid: [ThinClientVolume(coordinator, cid)],
    )


class ThinClientVolume(OptimisticMixin, NumberEntity):
    entity_description = VOLUME
    _needs_online = True

    def __init__(self, coordinator, client_id: str) -> None:
        super().__init__(coordinator, VOLUME.key, client_id)

    def _actual(self) -> int | None:
        return self.client.get("volume")

    @property
    def native_value(self) -> float | None:
        return self._current()

    async def async_set_native_value(self, value: float) -> None:
        level = round(value)
        await self._command("set_volume", {"level": level})
        self._set_pending(level)
