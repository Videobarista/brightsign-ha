"""The BrightSign integration (local network only)."""

from __future__ import annotations

from dataclasses import dataclass

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import (
    BrightSignClient,
    BrightSignError,
    async_send_udp,
    is_local_address,
    is_valid_udp_message,
)
from .const import (
    ATTR_HOST,
    ATTR_MESSAGE,
    ATTR_PORT,
    CONF_FINGERPRINT,
    CONF_HAS_LWS,
    CONF_REST_SCHEME,
    DEFAULT_UDP_PORT,
    DOMAIN,
    SERVICE_SEND_UDP,
)
from .coordinator import BrightSignCoordinator
from .sync import SyncListener, SyncTracker, async_acquire_listener

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _local_ip(value: str) -> str:
    """Only IP addresses on the local network (private, link-local, broadcast, multicast)."""
    host = cv.string(value).strip()
    if not is_local_address(host):
        raise vol.Invalid("host must be a local IP address (no host names or public addresses)")
    return host


def _udp_message(value: str) -> str:
    message = cv.string(value)
    if not is_valid_udp_message(message):
        raise vol.Invalid("message must be 1-1024 printable ASCII characters")
    return message


SEND_UDP_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_HOST): _local_ip,
        vol.Optional(ATTR_PORT, default=DEFAULT_UDP_PORT): cv.port,
        vol.Required(ATTR_MESSAGE): _udp_message,
    }
)


@dataclass
class BrightSignRuntimeData:
    """Runtime objects of one config entry."""

    coordinator: BrightSignCoordinator
    sync: SyncTracker
    listener: SyncListener


type BrightSignConfigEntry = ConfigEntry[BrightSignRuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the send_udp action."""

    async def _send_udp(call: ServiceCall) -> None:
        try:
            await async_send_udp(
                call.data[ATTR_HOST], call.data[ATTR_PORT], call.data[ATTR_MESSAGE]
            )
        except BrightSignError as err:
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(DOMAIN, SERVICE_SEND_UDP, _send_udp, schema=SEND_UDP_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BrightSignConfigEntry) -> bool:
    """Set up a BrightSign player."""
    client = BrightSignClient(
        async_get_clientsession(hass, verify_ssl=False),
        entry.data[CONF_HOST],
        entry.data.get(CONF_PASSWORD, ""),
        entry.data.get(CONF_REST_SCHEME),
        entry.data.get(CONF_FINGERPRINT),
    )
    coordinator = BrightSignCoordinator(
        hass,
        entry,
        client,
        has_rest=entry.data.get(CONF_REST_SCHEME) is not None,
        has_lws=entry.data.get(CONF_HAS_LWS, False),
    )
    await coordinator.async_config_entry_first_refresh()

    listener = await async_acquire_listener(hass)
    tracker = SyncTracker(hass, entry.data[CONF_HOST])
    tracker.async_start()
    entry.runtime_data = BrightSignRuntimeData(coordinator, tracker, listener)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BrightSignConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.sync.async_stop()
        entry.runtime_data.listener.async_stop()
    return unloaded
