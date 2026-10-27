"""Binary sensors: zone and thin client connectivity, and thin client state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CoveConfigEntry
from .entity import CoveThinClientEntity, CoveZoneEntity, add_device_entities

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class CoveBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], bool | None]


ZONE_ONLINE = CoveBinaryDescription(
    key="online",
    translation_key="online",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    value_fn=lambda z: z["online"],
)

THIN_CLIENT_BINARY = [
    CoveBinaryDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda c: c["online"],
    ),
    CoveBinaryDescription(
        key="in_use",
        translation_key="in_use",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        value_fn=lambda c: c["session_username"] is not None,
    ),
    CoveBinaryDescription(
        key="media",
        translation_key="media_playing",
        device_class=BinarySensorDeviceClass.SOUND,
        value_fn=lambda c: c["media_playing"],
    ),
    CoveBinaryDescription(
        key="browser",
        translation_key="browser_running",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c["browser_running"],
    ),
    CoveBinaryDescription(
        key="reboot_required",
        translation_key="reboot_required",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c["reboot_required"],
    ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: CoveConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    add_device_entities(
        coordinator,
        async_add_entities,
        zone=lambda zid: [ZoneBinarySensor(coordinator, ZONE_ONLINE, zid)],
        thin_client=lambda cid: [ThinClientBinarySensor(coordinator, d, cid) for d in THIN_CLIENT_BINARY],
    )


class ZoneBinarySensor(CoveZoneEntity, BinarySensorEntity):
    entity_description: CoveBinaryDescription

    def __init__(self, coordinator, description: CoveBinaryDescription, zone_id: str) -> None:
        super().__init__(coordinator, description.key, zone_id)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.zone)


class ThinClientBinarySensor(CoveThinClientEntity, BinarySensorEntity):
    entity_description: CoveBinaryDescription

    def __init__(self, coordinator, description: CoveBinaryDescription, client_id: str) -> None:
        super().__init__(coordinator, description.key, client_id)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.client)
