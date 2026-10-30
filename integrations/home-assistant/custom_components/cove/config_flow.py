"""Config flow: a Cove URL and an integration token from Admin → Integrations."""

from __future__ import annotations

from collections.abc import Mapping
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CoveApi, CoveAuthError, CoveError
from .const import DOMAIN

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): str,
        vol.Required(CONF_API_TOKEN): str,
        vol.Required(CONF_VERIFY_SSL, default=True): bool,
    }
)
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_API_TOKEN): str})


def _clean_url(url: str) -> str | None:
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return url


def _plain_http_ok(url: str) -> bool:
    """Plain http:// only to this machine: anywhere else the token, which can
    reboot and sign out every thin client, would cross the network in clear."""
    parts = urlsplit(url)
    if parts.scheme == "https":
        return True
    host = parts.hostname or ""
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


class CoveConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _check(self, url: str, token: str, verify_ssl: bool) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """``(status, errors)``: the snapshot when the token works."""
        api = CoveApi(async_get_clientsession(self.hass, verify_ssl=verify_ssl), url, token.strip())
        try:
            return await api.status(), {}
        except CoveAuthError:
            return None, {"base": "invalid_auth"}
        except CoveError:
            return None, {"base": "cannot_connect"}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = _clean_url(user_input[CONF_URL])
            if url is None:
                errors[CONF_URL] = "invalid_url"
            elif not _plain_http_ok(url):
                errors[CONF_URL] = "insecure_url"
            else:
                status, errors = await self._check(url, user_input[CONF_API_TOKEN], user_input[CONF_VERIFY_SSL])
                if status:
                    data = {
                        CONF_URL: url,
                        CONF_API_TOKEN: user_input[CONF_API_TOKEN].strip(),
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    }
                    await self.async_set_unique_id(status["instance_id"])
                    self._abort_if_unique_id_configured(updates=data)
                    return self.async_create_entry(title=f"Cove ({urlsplit(url).hostname})", data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            url = _clean_url(user_input[CONF_URL])
            if url is None:
                errors[CONF_URL] = "invalid_url"
            elif not _plain_http_ok(url):
                errors[CONF_URL] = "insecure_url"
            else:
                status, errors = await self._check(url, user_input[CONF_API_TOKEN], user_input[CONF_VERIFY_SSL])
                if status:
                    await self.async_set_unique_id(status["instance_id"])
                    self._abort_if_unique_id_mismatch(reason="wrong_server")
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_URL: url,
                            CONF_API_TOKEN: user_input[CONF_API_TOKEN].strip(),
                            CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                        },
                    )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input or entry.data),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            status, errors = await self._check(
                entry.data[CONF_URL], user_input[CONF_API_TOKEN], entry.data.get(CONF_VERIFY_SSL, True)
            )
            if status:
                await self.async_set_unique_id(status["instance_id"])
                self._abort_if_unique_id_mismatch(reason="wrong_server")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_TOKEN: user_input[CONF_API_TOKEN].strip()}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"url": entry.data[CONF_URL]},
        )
