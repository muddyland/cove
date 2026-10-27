"""Sensors: server-wide counters, per-zone load, and thin client readings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CoveConfigEntry
from .entity import (
    CoveServerEntity,
    CoveThinClientEntity,
    CoveZoneEntity,
    add_device_entities,
    parse_time,
)

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class CoveSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]


def _count(key: str) -> CoveSensorDescription:
    section, field = key.split(".")
    return CoveSensorDescription(
        key=f"{section}_{field}",
        translation_key=f"{section}_{field}",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d[section][field],
    )


SERVER_SENSORS = [
    _count("users.total"),
    _count("users.active"),
    _count("users.on_thin_clients"),
    _count("workspaces.total"),
    _count("workspaces.running"),
    _count("workspaces.starting"),
    _count("workspaces.stopped"),
    _count("workspaces.error"),
    _count("thin_clients_summary.online"),
    _count("thin_clients_summary.in_use"),
]

ZONE_SENSORS = [
    CoveSensorDescription(
        key="workspaces_running",
        translation_key="zone_workspaces_running",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda z: z["workspaces_running"],
    ),
    CoveSensorDescription(
        key="workspaces_total",
        translation_key="zone_workspaces_total",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda z: z["workspaces_total"],
    ),
    CoveSensorDescription(
        key="users_active",
        translation_key="zone_users_active",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda z: z["users_active"],
    ),
    CoveSensorDescription(
        key="cpu",
        translation_key="zone_cpu",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda z: z["cpu_percent"],
    ),
    CoveSensorDescription(
        key="memory",
        translation_key="zone_memory",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda z: z["memory_used_bytes"],
    ),
]

THIN_CLIENT_SENSORS = [
    CoveSensorDescription(
        key="user",
        translation_key="signed_in_user",
        value_fn=lambda c: c["session_username"],
    ),
    CoveSensorDescription(
        key="workspace",
        translation_key="workspace",
        value_fn=lambda c: c["workspace_name"],
    ),
    CoveSensorDescription(
        key="volume",
        translation_key="volume",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c["volume"],
    ),
    CoveSensorDescription(
        key="idle",
        translation_key="idle",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        # Changes on every poll; on for those who want "idle for 10 minutes".
        entity_registry_enabled_default=False,
        value_fn=lambda c: c["idle_seconds"],
    ),
    CoveSensorDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: parse_time(c["last_seen_at"]),
    ),
    CoveSensorDescription(
        key="booted",
        translation_key="booted",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: parse_time(c["booted_at"]),
    ),
    CoveSensorDescription(
        key="updates",
        translation_key="updates_available",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c["updates_available"],
    ),
    CoveSensorDescription(
        key="agent_version",
        translation_key="agent_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c["agent_version"],
    ),
    CoveSensorDescription(
        key="ip",
        translation_key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: (c["ips"] or [None])[0],
    ),
    CoveSensorDescription(
        key="last_error",
        translation_key="last_error",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: (c["last_error"] or "")[:255] or None,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: CoveConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    add_device_entities(
        coordinator,
        async_add_entities,
        server=lambda: [ServerSensor(coordinator, d) for d in SERVER_SENSORS],
        zone=lambda zid: [ZoneSensor(coordinator, d, zid) for d in ZONE_SENSORS],
        thin_client=lambda cid: [ThinClientSensor(coordinator, d, cid) for d in THIN_CLIENT_SENSORS],
    )


class ServerSensor(CoveServerEntity, SensorEntity):
    entity_description: CoveSensorDescription

    def __init__(self, coordinator, description: CoveSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)


class ZoneSensor(CoveZoneEntity, SensorEntity):
    entity_description: CoveSensorDescription

    def __init__(self, coordinator, description: CoveSensorDescription, zone_id: str) -> None:
        super().__init__(coordinator, description.key, zone_id)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.zone)


class ThinClientSensor(CoveThinClientEntity, SensorEntity):
    entity_description: CoveSensorDescription

    def __init__(self, coordinator, description: CoveSensorDescription, client_id: str) -> None:
        super().__init__(coordinator, description.key, client_id)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.client)
