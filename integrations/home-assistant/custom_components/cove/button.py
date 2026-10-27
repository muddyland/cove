"""One-shot thin client actions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CoveConfigEntry
from .entity import CoveThinClientEntity, add_device_entities

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class CoveButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[ThinClientButton], Awaitable[None]]
    needs_online: bool = True


BUTTONS = [
    CoveButtonDescription(
        key="identify",
        translation_key="identify",
        device_class=ButtonDeviceClass.IDENTIFY,
        press_fn=lambda e: e._command("identify", {"seconds": 10}),
    ),
    CoveButtonDescription(
        key="screenshot",
        translation_key="take_screenshot",
        press_fn=lambda e: e._command("screenshot"),
    ),
    CoveButtonDescription(
        key="restart_browser",
        translation_key="restart_browser",
        press_fn=lambda e: e._command("restart_browser", {"reason": "home-assistant"}),
    ),
    CoveButtonDescription(
        key="sign_out",
        translation_key="sign_out",
        # Revokes the device's sessions in Cove even while it is offline.
        press_fn=lambda e: e._run(e.coordinator.api.sign_out),
        needs_online=False,
    ),
    CoveButtonDescription(
        key="reboot",
        translation_key="reboot",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda e: e._command("reboot"),
    ),
    CoveButtonDescription(
        key="power_off",
        translation_key="power_off",
        entity_category=EntityCategory.CONFIG,
        # Nothing in Home Assistant can turn it back on; enable it deliberately.
        entity_registry_enabled_default=False,
        press_fn=lambda e: e._command("poweroff"),
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
        thin_client=lambda cid: [ThinClientButton(coordinator, d, cid) for d in BUTTONS],
    )


class ThinClientButton(CoveThinClientEntity, ButtonEntity):
    entity_description: CoveButtonDescription

    def __init__(self, coordinator, description: CoveButtonDescription, client_id: str) -> None:
        super().__init__(coordinator, description.key, client_id)
        self.entity_description = description
        self._needs_online = description.needs_online

    async def async_press(self) -> None:
        await self.entity_description.press_fn(self)
