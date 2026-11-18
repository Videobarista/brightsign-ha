"""Config flow for BrightSign players."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.util.ssl import get_default_no_verify_context

from .api import BrightSignClient, ProbeResult
from .const import (
    CONF_FINGERPRINT,
    CONF_HAS_LWS,
    CONF_MAC,
    CONF_REST_SCHEME,
    CONF_SERIAL,
    DEFAULT_PASSWORD,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PASSWORD_SCHEMA = vol.Schema({vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): str})


class BrightSignConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BrightSign."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._mac: str | None = None
        self._serial: str | None = None
        self._name: str | None = None

    async def _async_probe(self, host: str, password: str) -> ProbeResult:
        client = BrightSignClient(
            async_get_clientsession(self.hass, verify_ssl=False),
            host,
            password,
            no_verify_context=get_default_no_verify_context(),
        )
        return await client.async_probe()

    def _entry_data(self, host: str, password: str, result: ProbeResult) -> dict[str, Any]:
        return {
            CONF_HOST: host,
            CONF_PASSWORD: password,
            CONF_REST_SCHEME: result.rest_scheme,
            CONF_HAS_LWS: result.lws,
            CONF_FINGERPRINT: result.fingerprint,
            CONF_SERIAL: result.serial or self._serial,
            CONF_MAC: self._mac,
        }

    def _title(self, host: str, result: ProbeResult) -> str:
        if result.name or self._name:
            return result.name or self._name or host
        return f"BrightSign {result.model}" if result.model else f"BrightSign {host}"

    async def _async_validate(
        self, host: str, password: str, errors: dict[str, str]
    ) -> ProbeResult | None:
        result = await self._async_probe(host, password)
        if not result.reachable:
            errors["base"] = "cannot_connect"
            return None
        if result.auth_failed:
            errors[CONF_PASSWORD] = "invalid_auth"
            return None
        return result

    # ----- manual ----------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manual setup by IP address or hostname."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            password = user_input.get(CONF_PASSWORD, DEFAULT_PASSWORD)
            self._async_abort_entries_match({CONF_HOST: host})
            result = await self._async_validate(host, password, errors)
            if result is not None:
                await self.async_set_unique_id(result.serial or host)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return self.async_create_entry(
                    title=self._title(host, result), data=self._entry_data(host, password, result)
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_HOST): str,
                        vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
                    }
                ),
                user_input,
            ),
            errors=errors,
        )

    # ----- discovery -------------------------------------------------------

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        """Player found by its BrightSign MAC address or hostname."""
        self._host = discovery_info.ip
        self._mac = format_mac(discovery_info.macaddress)
        for entry in self._async_current_entries(include_ignore=False):
            if entry.data.get(CONF_MAC) == self._mac and entry.data.get(CONF_HOST) != self._host:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_HOST: self._host}
                )
                return self.async_abort(reason="already_configured")
        self._async_abort_entries_match({CONF_HOST: self._host})
        await self.async_set_unique_id(self._mac)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})
        return await self._async_discovered()

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Player found by the mDNS advertisement of its presentation."""
        self._host = discovery_info.host
        self._serial = discovery_info.properties.get("serialnumber") or None
        self._name = discovery_info.properties.get("unitname") or None
        self._async_abort_entries_match({CONF_HOST: self._host})
        await self.async_set_unique_id(self._serial or self._host)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})
        return await self._async_discovered()

    async def _async_discovered(self) -> ConfigFlowResult:
        self.context["title_placeholders"] = {"name": self._name or self._host or "BrightSign"}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered player and enter the DWS password if it has one."""
        errors: dict[str, str] = {}
        host = self._host or ""
        if user_input is not None:
            password = user_input.get(CONF_PASSWORD, DEFAULT_PASSWORD)
            result = await self._async_validate(host, password, errors)
            if result is not None:
                if result.serial and result.serial != self.unique_id:
                    await self.async_set_unique_id(result.serial, raise_on_progress=False)
                    self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return self.async_create_entry(
                    title=self._title(host, result), data=self._entry_data(host, password, result)
                )
        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=PASSWORD_SCHEMA,
            description_placeholders={"host": host},
            errors=errors,
        )

    # ----- reauth ----------------------------------------------------------

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The stored DWS password was rejected or the HTTPS certificate changed."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new DWS password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            password = user_input.get(CONF_PASSWORD, DEFAULT_PASSWORD)
            result = await self._async_validate(entry.data[CONF_HOST], password, errors)
            if result is not None:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_PASSWORD: password,
                        CONF_REST_SCHEME: result.rest_scheme,
                        CONF_HAS_LWS: result.lws,
                        CONF_FINGERPRINT: result.fingerprint,
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=PASSWORD_SCHEMA,
            description_placeholders={"host": entry.data[CONF_HOST]},
            errors=errors,
        )
