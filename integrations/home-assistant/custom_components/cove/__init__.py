"""The Cove integration: thin clients, zones and workspace counters from a Cove server."""

from __future__ import annotations

from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CoveApi
from .const import DOMAIN
from .coordinator import CoveConfigEntry, CoveCoordinator

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: CoveConfigEntry) -> bool:
    session = async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True))
    api = CoveApi(session, entry.data[CONF_URL], entry.data[CONF_API_TOKEN])
    coordinator = CoveCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    # The server is the parent device that zones and thin clients hang off.
    url = entry.data[CONF_URL].rstrip("/")
    hub = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.data["instance_id"])},
        name="Cove",
        manufacturer="Cove",
        model="Control plane",
        configuration_url=url + "/app",
    )
    coordinator.hub_device_id = hub.id

    @callback
    def _prune_devices() -> None:
        """Remove devices for thin clients and zones deleted in Cove."""
        if not coordinator.last_update_success:
            return
        live = _device_identifiers(coordinator)
        registry = dr.async_get(hass)
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            ours = {i for i in device.identifiers if i[0] == DOMAIN}
            if ours and not ours & live:
                registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)

    _prune_devices()
    entry.async_on_unload(coordinator.async_add_listener(_prune_devices))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CoveConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: CoveConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting a device by hand only once Cove no longer reports it."""
    return not {i for i in device.identifiers if i[0] == DOMAIN} & _device_identifiers(entry.runtime_data)


def _device_identifiers(coordinator: CoveCoordinator) -> set[tuple[str, str]]:
    data = coordinator.data or {}
    ids = {(DOMAIN, data.get("instance_id", ""))}
    ids |= {(DOMAIN, f"zone_{z['id']}") for z in data.get("zones", [])}
    ids |= {(DOMAIN, f"thin_client_{c['id']}") for c in data.get("thin_clients", [])}
    return ids
