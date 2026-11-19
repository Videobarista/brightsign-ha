"""Config flow for BrightSign players.

The DWS password is only asked for when it is needed: the integration first tries the
player without a password and then with its serial number (the factory default password,
from the DHCP host name, the presentation, the Info page or the HTTPS certificate).
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.components import network
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.util.ssl import get_default_no_verify_context

from .api import BrightSignClient, ProbeResult, async_scan_for_players, sanitize_text, scan_targets
from .const import (
    CONF_FINGERPRINT,
    CONF_HAS_LEGACY,
    CONF_HAS_LWS,
    CONF_HOSTNAME,
    CONF_MAC,
    CONF_REST_SCHEME,
    CONF_SERIAL,
    CONF_SNAPSHOT_INTERVAL,
    CONF_UDP_PORT,
    DEFAULT_PASSWORD,
    DEFAULT_SNAPSHOT_INTERVAL,
    DEFAULT_UDP_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Default DHCP hostname of a player: brightsign-<serial number>
_HOSTNAME_SERIAL = re.compile(r"brightsign-([0-9a-z]{6,20})", re.IGNORECASE)


def serial_from_hostname(hostname: str | None) -> str | None:
    """Serial number from a default BrightSign hostname, upper case."""
    match = _HOSTNAME_SERIAL.fullmatch((hostname or "").split(".")[0])
    return match.group(1).upper() if match else None


PASSWORD_SCHEMA = vol.Schema({vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): str})


class BrightSignConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BrightSign."""

    VERSION = 1
    MINOR_VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Options: UDP port for messages from the presentation, screenshot interval."""
        return BrightSignOptionsFlow()

    def __init__(self) -> None:
        self._host: str | None = None
        self._mac: str | None = None
        self._hostname: str | None = None
        self._serial: str | None = None
        self._name: str | None = None
        self._found: dict[str, str] = {}  # players found by the network scan: IP -> MAC
        self._probe: ProbeResult | None = None  # last automatic probe
        self._password = DEFAULT_PASSWORD  # password the automatic probe found

    # ----- helpers -----------------------------------------------------------

    async def _async_scan(self) -> dict[str, str]:
        """BrightSign players on Home Assistant's own local networks: IP -> MAC."""
        adapters = await network.async_get_adapters(self.hass)
        interfaces = [
            f"{ip['address']}/{ip['network_prefix']}"
            for adapter in adapters
            if adapter["enabled"]
            for ip in adapter["ipv4"]
        ]
        found = await async_scan_for_players(scan_targets(interfaces))
        _LOGGER.debug("Network scan found %d BrightSign player(s)", len(found))
        return found

    def _async_known_entry(self, mac: str | None, serial: str | None) -> ConfigEntry | None:
        """Configured entry of this player, by MAC address or serial number."""
        for entry in self._async_current_entries(include_ignore=False):
            if not entry.unique_id:
                continue
            if mac and entry.data.get(CONF_MAC) == mac:
                return entry
            if serial and serial in (entry.unique_id, entry.data.get(CONF_SERIAL)):
                return entry
        return None

    def _is_configured(self, host: str, mac: str) -> bool:
        return any(
            entry.data.get(CONF_HOST) == host or entry.data.get(CONF_MAC) == mac
            for entry in self._async_current_entries(include_ignore=False)
        )

    async def _async_probe(self, host: str, password: str) -> ProbeResult:
        client = BrightSignClient(
            async_get_clientsession(self.hass, verify_ssl=False),
            host,
            password,
            no_verify_context=get_default_no_verify_context(),
        )
        return await client.async_probe()

    def _candidates(self, result: ProbeResult, hint: str | None, extra: list[str]) -> list[str]:
        """Passwords to try: the stored one (reconfigure), then the serial number."""
        candidates: list[str] = [password for password in extra if password]
        for serial in (hint, self._serial, result.serial, *result.certificate_serials):
            for candidate in (serial.upper(), serial) if serial else ():
                if candidate not in candidates:
                    candidates.append(candidate)
        return candidates[:5]

    async def _async_auto(
        self, host: str, hint: str | None = None, extra: list[str] | None = None
    ) -> ProbeResult:
        """Probe without a password, then with the stored password and the serial number."""
        self._password = DEFAULT_PASSWORD
        result = await self._async_probe(host, DEFAULT_PASSWORD)
        if result.reachable and result.auth_failed:
            for candidate in self._candidates(result, hint, extra or []):
                retry = await self._async_probe(host, candidate)
                if retry.reachable and not retry.auth_failed:
                    _LOGGER.debug("%s accepted a default or stored DWS password", host)
                    self._password = candidate
                    return retry
        return result

    @staticmethod
    def _basic(result: ProbeResult) -> ProbeResult:
        """No password: use the player without the DWS (add the password later)."""
        result.rest_scheme = None
        result.fingerprint = None
        result.legacy = False
        return result

    def _entry_data(self, host: str, password: str, result: ProbeResult) -> dict[str, Any]:
        return {
            CONF_HOST: host,
            CONF_PASSWORD: password,
            CONF_REST_SCHEME: result.rest_scheme,
            CONF_HAS_LWS: result.lws,
            CONF_HAS_LEGACY: result.legacy,
            CONF_FINGERPRINT: result.fingerprint,
            CONF_SERIAL: result.serial or self._serial,
            CONF_MAC: self._mac,
            CONF_HOSTNAME: self._hostname,
        }

    def _title(self, host: str, result: ProbeResult) -> str:
        """Player name, else model/serial; never the IP address."""
        if result.name or self._name:
            return result.name or self._name or "BrightSign player"
        serial = result.serial or self._serial
        if result.model and serial:
            return f"BrightSign {result.model} {serial}"
        if result.model or serial:
            return f"BrightSign {result.model or serial}"
        return "BrightSign player"

    async def _async_create(
        self, host: str, password: str, result: ProbeResult
    ) -> ConfigFlowResult:
        """Create the entry; the serial number is the unique ID when it is known."""
        if result.serial:
            await self.async_set_unique_id(result.serial, raise_on_progress=False)
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        elif self.unique_id is None:
            await self.async_set_unique_id(
                self._serial or self._mac or host, raise_on_progress=False
            )
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        return self.async_create_entry(
            title=self._title(host, result), data=self._entry_data(host, password, result)
        )

    async def _async_validate(
        self,
        host: str,
        password: str,
        errors: dict[str, str],
        serial_hint: str | None = None,
        stored: str | None = None,
    ) -> tuple[ProbeResult, str] | None:
        """Check a host with the given password, or automatically when it is empty."""
        if password:
            result = await self._async_probe(host, password)
        else:
            result = await self._async_auto(host, serial_hint, [stored] if stored else None)
            password = self._password
        if not result.reachable:
            errors["base"] = "cannot_connect"
            return None
        if result.auth_failed:
            if password:
                errors[CONF_PASSWORD] = "invalid_auth"
                return None
            result = self._basic(result)
        return result, password

    def _display_name(self) -> str:
        result = self._probe or ProbeResult()
        if self._name or result.name or result.serial or self._serial or result.model:
            return self._title(self._host or "", result)
        return self._host or "BrightSign"

    # ----- manual ----------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a player found on the network, or enter an IP address or hostname."""
        errors: dict[str, str] = {}
        if user_input is None:
            self._found = await self._async_scan()
        else:
            host = user_input[CONF_HOST].strip()
            self._mac = self._found.get(host)
            self._async_abort_entries_match({CONF_HOST: host})
            self._host = host
            result = await self._async_auto(host)
            self._probe = result
            if not result.reachable:
                errors["base"] = "cannot_connect"
            elif result.auth_failed:
                return await self.async_step_password()
            else:
                return await self._async_create(host, self._password, result)
        found = {ip: mac for ip, mac in self._found.items() if not self._is_configured(ip, mac)}
        host_selector: SelectSelector | TextSelector = TextSelector()
        if found:
            host_selector = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=ip, label=f"{ip} ({mac.upper()})")
                        for ip, mac in found.items()
                    ],
                    custom_value=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_HOST): host_selector}),
                user_input or ({CONF_HOST: next(iter(found))} if found else None),
            ),
            description_placeholders={"found": str(len(found))},
            errors=errors,
        )

    async def async_step_password(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The serial number was not accepted: enter the DWS password, or go on without."""
        errors: dict[str, str] = {}
        host = self._host or ""
        if user_input is not None:
            password = user_input.get(CONF_PASSWORD, DEFAULT_PASSWORD)
            if not password:
                return await self._async_create(
                    host, DEFAULT_PASSWORD, self._basic(self._probe or ProbeResult(reachable=True))
                )
            result = await self._async_probe(host, password)
            if not result.reachable:
                errors["base"] = "cannot_connect"
            elif result.auth_failed:
                errors[CONF_PASSWORD] = "invalid_auth"
            else:
                return await self._async_create(host, password, result)
        return self.async_show_form(
            step_id="password",
            data_schema=PASSWORD_SCHEMA,
            description_placeholders={"name": self._display_name(), "host": host},
            errors=errors,
        )

    # ----- discovery -------------------------------------------------------

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        """Player found by its BrightSign MAC address or hostname, or a known player's address."""
        self._host = host = discovery_info.ip
        self._mac = mac = format_mac(discovery_info.macaddress)
        self._hostname = sanitize_text(discovery_info.hostname, 63) or None
        self._serial = serial_from_hostname(discovery_info.hostname)
        if entry := self._async_known_entry(mac, self._serial):
            # Known player, maybe at a new address. Aborting through the unique ID also
            # records this discovery, so Home Assistant offers the player again after it
            # has been removed.
            updates: dict[str, Any] = {CONF_HOST: host, CONF_MAC: mac}
            if self._hostname:
                updates[CONF_HOSTNAME] = self._hostname
            await self.async_set_unique_id(entry.unique_id, raise_on_progress=False)
            self._abort_if_unique_id_configured(updates=updates)
        self._async_abort_entries_match({CONF_HOST: host})
        await self.async_set_unique_id(self._serial or mac)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        return await self._async_discovered()

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Player found by the mDNS advertisement of its presentation."""
        self._host = discovery_info.host
        self._serial = discovery_info.properties.get("serialnumber") or None
        self._name = discovery_info.properties.get("unitname") or None
        if entry := self._async_known_entry(None, self._serial):
            await self.async_set_unique_id(entry.unique_id, raise_on_progress=False)
            self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})
        self._async_abort_entries_match({CONF_HOST: self._host})
        await self.async_set_unique_id(self._serial or self._host)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})
        return await self._async_discovered()

    async def _async_discovered(self) -> ConfigFlowResult:
        """Try the player right away: the password is only asked when it is needed."""
        self._probe = await self._async_auto(self._host or "", self._serial)
        self.context["title_placeholders"] = {"name": self._display_name()}
        if self._probe.reachable and self._probe.auth_failed:
            return await self.async_step_password()
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered player."""
        errors: dict[str, str] = {}
        host = self._host or ""
        if user_input is not None:
            result = self._probe
            if result is None or not result.reachable:
                # The player did not answer when it was discovered: try again now
                result = self._probe = await self._async_auto(host, self._serial)
            if result.reachable and result.auth_failed:
                return await self.async_step_password()
            if result.reachable:
                return await self._async_create(host, self._password, result)
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={"name": self._display_name(), "host": host},
            errors=errors,
        )

    # ----- reconfigure -----------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the address or the DWS password; an empty password keeps the current one."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            password = user_input.get(CONF_PASSWORD, DEFAULT_PASSWORD)
            validated = await self._async_validate(
                host,
                password,
                errors,
                entry.data.get(CONF_SERIAL),
                entry.data.get(CONF_PASSWORD),
            )
            if validated is not None:
                result, password = validated
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_HOST: host,
                        CONF_PASSWORD: password,
                        CONF_REST_SCHEME: result.rest_scheme,
                        CONF_HAS_LWS: result.lws,
                        CONF_HAS_LEGACY: result.legacy,
                        CONF_FINGERPRINT: result.fingerprint,
                        CONF_SERIAL: result.serial or entry.data.get(CONF_SERIAL),
                    },
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_HOST): str,
                        vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
                    }
                ),
                user_input or {CONF_HOST: entry.data[CONF_HOST]},
            ),
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
            validated = await self._async_validate(
                entry.data[CONF_HOST], password, errors, entry.data.get(CONF_SERIAL)
            )
            if validated is not None:
                result, password = validated
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_PASSWORD: password,
                        CONF_REST_SCHEME: result.rest_scheme,
                        CONF_HAS_LWS: result.lws,
                        CONF_HAS_LEGACY: result.legacy,
                        CONF_FINGERPRINT: result.fingerprint,
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=PASSWORD_SCHEMA,
            description_placeholders={"host": entry.data[CONF_HOST]},
            errors=errors,
        )


class BrightSignOptionsFlow(OptionsFlowWithReload):
    """Options for one player; the player reloads when they change."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_UDP_PORT, default=options.get(CONF_UDP_PORT, DEFAULT_UDP_PORT)
                ): vol.All(vol.Coerce(int), vol.Range(min=1024, max=65535)),
                vol.Optional(
                    CONF_SNAPSHOT_INTERVAL,
                    default=options.get(CONF_SNAPSHOT_INTERVAL, DEFAULT_SNAPSHOT_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
