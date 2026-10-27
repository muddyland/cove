"""Switches on a thin client: its screen, its sound, and "keep awake"."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import KEEP_AWAKE_MINUTES
from .coordinator import CoveConfigEntry
from .entity import OptimisticMixin, add_device_entities

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class CoveSwitchDescription(SwitchEntityDescription):
    value_fn: Callable[[dict[str, Any]], bool | None]
    set_fn: Callable[[ThinClientSwitch, bool], Awaitable[None]]
    # Keep awake is held by Cove, so it can be set while the device is away.
    needs_online: bool = True


SWITCHES = [
    CoveSwitchDescription(
        key="screen",
        translation_key="screen",
        value_fn=lambda c: c["display_on"],
        set_fn=lambda e, on: e._command("screen_on" if on else "screen_off"),
    ),
    CoveSwitchDescription(
        key="mute",
        translation_key="mute",
        value_fn=lambda c: c["muted"],
        set_fn=lambda e, on: e._command("set_mute", {"muted": on}),
    ),
    CoveSwitchDescription(
        key="keep_awake",
        translation_key="keep_awake",
        value_fn=lambda c: c["keep_awake_until"] is not None,
        set_fn=lambda e, on: e._run(e.coordinator.api.keep_awake, KEEP_AWAKE_MINUTES if on else 0),
        needs_online=False,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: CoveConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    if not coordinator.can_control:
        return
    add_device_entities(
        coordinator,
        async_add_entities,
        thin_client=lambda cid: [ThinClientSwitch(coordinator, d, cid) for d in SWITCHES],
    )


class ThinClientSwitch(OptimisticMixin, SwitchEntity):
    entity_description: CoveSwitchDescription

    def __init__(self, coordinator, description: CoveSwitchDescription, client_id: str) -> None:
        super().__init__(coordinator, description.key, client_id)
        self.entity_description = description
        self._needs_online = description.needs_online

    def _actual(self) -> bool | None:
        return self.entity_description.value_fn(self.client)

    @property
    def is_on(self) -> bool | None:
        return self._current()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.entity_description.set_fn(self, True)
        self._set_pending(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.entity_description.set_fn(self, False)
        self._set_pending(False)
