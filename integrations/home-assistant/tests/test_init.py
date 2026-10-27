"""Setup, entities, control and device lifecycle."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.cove.const import DOMAIN

from .conftest import API, DESK


async def _setup(hass: HomeAssistant, entry, aioclient_mock, status) -> None:
    aioclient_mock.get(f"{API}/status", json=status)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _eid(hass: HomeAssistant, platform: str, key: str) -> str:
    unique = f"inst0001_thin_client_{DESK}_{key}" if not key.startswith("@") else f"inst0001_{key[1:]}"
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique)
    assert entity_id, f"no {platform} {unique}"
    return entity_id


def _mock_controls(aioclient_mock) -> None:
    base = f"{API}/thin-clients/{DESK}"
    aioclient_mock.post(f"{base}/commands", status=202, json={})
    aioclient_mock.post(f"{base}/keep-awake", status=204)
    aioclient_mock.post(f"{base}/signout", status=204)


async def _poll(hass: HomeAssistant, aioclient_mock, status) -> None:
    """Serve a new status and let the coordinator fetch it. (Clearing the mock
    also drops recorded calls, so callers inspect calls before polling.)"""
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/status", json=status)
    _mock_controls(aioclient_mock)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()


async def test_entities_show_cove_state(hass: HomeAssistant, aioclient_mock, status, entry) -> None:
    await _setup(hass, entry, aioclient_mock, status)
    assert entry.state is ConfigEntryState.LOADED

    assert hass.states.get(_eid(hass, "sensor", "@workspaces_running")).state == "3"
    assert hass.states.get(_eid(hass, "sensor", "@users_active")).state == "2"
    assert hass.states.get(_eid(hass, "sensor", "user")).state == "bob"
    assert hass.states.get(_eid(hass, "sensor", "workspace")).state == "lab"
    assert hass.states.get(_eid(hass, "binary_sensor", "online")).state == "on"
    assert hass.states.get(_eid(hass, "binary_sensor", "in_use")).state == "on"
    assert hass.states.get(_eid(hass, "switch", "screen")).state == "on"
    assert hass.states.get(_eid(hass, "number", "volume_level")).state == "80"

    zone_cpu = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"inst0001_zone_{'z' * 32}_cpu")
    assert hass.states.get(zone_cpu).state == "42.5"

    devices = dr.async_get(hass)
    desk = devices.async_get_device_by_identifier((DOMAIN, f"thin_client_{DESK}"), entry.entry_id)
    assert desk.name == "Desk" and desk.model == "Thin client" and desk.sw_version == "1.1.0"
    hub = devices.async_get_device_by_identifier((DOMAIN, "inst0001"), entry.entry_id)
    assert desk.via_device_id == hub.id and hub.model == "Control plane"


async def test_read_only_token_has_no_controls(hass: HomeAssistant, aioclient_mock, status, entry) -> None:
    status["token"]["scope"] = "read"
    await _setup(hass, entry, aioclient_mock, status)
    registry = er.async_get(hass)
    platforms = {e.domain for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert platforms == {"sensor", "binary_sensor", "image"}


async def test_controls_queue_commands(hass: HomeAssistant, aioclient_mock, status, entry) -> None:
    await _setup(hass, entry, aioclient_mock, status)
    base = f"{API}/thin-clients/{DESK}"
    _mock_controls(aioclient_mock)

    screen = _eid(hass, "switch", "screen")
    await hass.services.async_call("switch", "turn_off", {"entity_id": screen}, blocking=True)
    # Shown off straight away, though the device hasn't reported yet...
    assert hass.states.get(screen).state == "off"
    posts = [(str(url), body) for method, url, body, _ in aioclient_mock.mock_calls if method == "POST"]
    assert posts == [(f"{base}/commands", {"kind": "screen_off", "args": {}})]
    await _poll(hass, aioclient_mock, status)
    # ...and a poll that predates the device acting doesn't flip it back.
    assert hass.states.get(screen).state == "off"

    await hass.services.async_call(
        "number", "set_value", {"entity_id": _eid(hass, "number", "volume_level"), "value": 55}, blocking=True
    )
    await hass.services.async_call("button", "press", {"entity_id": _eid(hass, "button", "reboot")}, blocking=True)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": _eid(hass, "switch", "keep_awake")}, blocking=True
    )
    await hass.services.async_call("button", "press", {"entity_id": _eid(hass, "button", "sign_out")}, blocking=True)

    sent = [(str(url), body) for method, url, body, _ in aioclient_mock.mock_calls if method == "POST"]
    assert sent == [
        (f"{base}/commands", {"kind": "set_volume", "args": {"level": 55}}),
        (f"{base}/commands", {"kind": "reboot", "args": {}}),
        (f"{base}/keep-awake", {"minutes": 1440}),
        (f"{base}/signout", None),
    ]


async def test_controls_unavailable_while_offline(hass: HomeAssistant, aioclient_mock, status, entry) -> None:
    await _setup(hass, entry, aioclient_mock, status)
    status["thin_clients"][0].update(online=False, display_on=None, volume=None)
    await _poll(hass, aioclient_mock, status)
    assert hass.states.get(_eid(hass, "switch", "screen")).state == "unavailable"
    assert hass.states.get(_eid(hass, "button", "reboot")).state == "unavailable"
    # Readings go unknown; Cove-side controls stay usable.
    assert hass.states.get(_eid(hass, "sensor", "volume")).state == "unknown"
    assert hass.states.get(_eid(hass, "binary_sensor", "online")).state == "off"
    assert hass.states.get(_eid(hass, "switch", "keep_awake")).state == "off"
    assert hass.states.get(_eid(hass, "button", "sign_out")).state != "unavailable"


async def test_devices_follow_cove(hass: HomeAssistant, aioclient_mock, status, entry) -> None:
    await _setup(hass, entry, aioclient_mock, status)
    devices = dr.async_get(hass)
    rack = dict(status["thin_clients"][0], id="b" * 32, name="Rack")
    status["thin_clients"].append(rack)
    await _poll(hass, aioclient_mock, status)
    assert devices.async_get_device_by_identifier((DOMAIN, f"thin_client_{'b' * 32}"), entry.entry_id)
    assert hass.states.get(_eid(hass, "switch", "screen")) is not None

    status["thin_clients"] = [rack]
    await _poll(hass, aioclient_mock, status)
    assert devices.async_get_device_by_identifier((DOMAIN, f"thin_client_{DESK}"), entry.entry_id) is None
    assert er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"inst0001_thin_client_{DESK}_screen") is None


async def test_revoked_token_starts_reauth(hass: HomeAssistant, aioclient_mock, status, entry) -> None:
    await _setup(hass, entry, aioclient_mock, status)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/status", status=401, json={"detail": "Unknown or revoked integration token"})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_screenshot_image(hass: HomeAssistant, aioclient_mock, status, entry, hass_client) -> None:
    status["thin_clients"][0]["screenshot_at"] = "2026-10-27T09:58:00+00:00"
    await _setup(hass, entry, aioclient_mock, status)
    png = b"\x89PNG\r\n\x1a\nfake"
    aioclient_mock.get(f"{API}/thin-clients/{DESK}/screenshot.png", content=png)
    image = _eid(hass, "image", "screenshot")
    state = hass.states.get(image)
    assert state.state == "2026-10-27T09:58:00+00:00"
    client = await hass_client()
    resp = await client.get(state.attributes["entity_picture"])
    assert resp.status == 200 and await resp.read() == png
