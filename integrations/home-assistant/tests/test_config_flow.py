"""Config flow: setup, duplicate servers, bad tokens, re-authentication."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.cove.const import DOMAIN

from .conftest import API, URL


async def _start(hass: HomeAssistant, **data):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: URL + "/", CONF_API_TOKEN: " cit_secret ", CONF_VERIFY_SSL: True, **data},
    )


async def test_user_flow_creates_entry(hass: HomeAssistant, aioclient_mock, status) -> None:
    aioclient_mock.get(f"{API}/status", json=status)
    result = await _start(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Cove (cove.example.com)"
    assert result["data"] == {CONF_URL: URL, CONF_API_TOKEN: "cit_secret", CONF_VERIFY_SSL: True}
    assert result["result"].unique_id == "inst0001"
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer cit_secret"


async def test_user_flow_errors(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(f"{API}/status", status=401, json={"detail": "Unknown or revoked integration token"})
    result = await _start(hass)
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/status", text="<html>proxy login</html>", headers={"Content-Type": "text/html"})
    result = await _start(hass)
    assert result["errors"] == {"base": "cannot_connect"}

    result = await _start(hass, **{CONF_URL: "cove.example.com"})
    assert result["errors"] == {CONF_URL: "invalid_url"}


async def test_same_server_twice_aborts(hass: HomeAssistant, aioclient_mock, status, entry) -> None:
    entry.add_to_hass(hass)
    aioclient_mock.get(f"{API}/status", json=status)
    result = await _start(hass)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_reauth_replaces_the_token(hass: HomeAssistant, aioclient_mock, status, entry) -> None:
    entry.add_to_hass(hass)
    aioclient_mock.get(f"{API}/status", json=status)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_TOKEN: "cit_new"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_TOKEN] == "cit_new"


async def test_reauth_refuses_another_server(hass: HomeAssistant, aioclient_mock, status, entry) -> None:
    entry.add_to_hass(hass)
    aioclient_mock.get(f"{API}/status", json={**status, "instance_id": "someone-else"})
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_TOKEN: "cit_new"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "wrong_server"
    assert entry.data[CONF_API_TOKEN] == "cit_secret"
