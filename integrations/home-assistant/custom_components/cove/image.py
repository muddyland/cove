"""The latest screenshot of a thin client's screen (taken with its button)."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.image import ImageEntity, ImageEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import CoveError
from .coordinator import CoveConfigEntry
from .entity import CoveThinClientEntity, add_device_entities, parse_time

PARALLEL_UPDATES = 1

SCREENSHOT = ImageEntityDescription(key="screenshot", translation_key="screenshot")


async def async_setup_entry(
    hass: HomeAssistant, entry: CoveConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    # Screenshots need a Control token: they show what someone has on screen.
    if (coordinator.data or {}).get("token", {}).get("scope") != "control":
        return
    add_device_entities(
        coordinator,
        async_add_entities,
        thin_client=lambda cid: [ThinClientScreenshot(coordinator, cid)],
    )


class ThinClientScreenshot(CoveThinClientEntity, ImageEntity):
    entity_description = SCREENSHOT
    _attr_content_type = "image/png"

    def __init__(self, coordinator, client_id: str) -> None:
        super().__init__(coordinator, SCREENSHOT.key, client_id)
        ImageEntity.__init__(self, coordinator.hass)
        self._cached: tuple[str, bytes] | None = None

    @property
    def image_last_updated(self) -> datetime | None:
        return parse_time(self.client.get("screenshot_at"))

    async def async_image(self) -> bytes | None:
        taken = self.client.get("screenshot_at")
        if not taken:
            return None
        if self._cached and self._cached[0] == taken:
            return self._cached[1]
        try:
            data = await self.coordinator.api.screenshot(self.client_id)
        except CoveError:
            return self._cached[1] if self._cached else None
        if data:
            self._cached = (taken, data)
        return data
