"""The BrightSign integration (local network only)."""

from __future__ import annotations

from dataclasses import dataclass

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
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
    ATTR_COMMAND,
    ATTR_DEVICE_ID,
    ATTR_HOST,
    ATTR_MESSAGE,
    ATTR_PORT,
    CONF_FINGERPRINT,
    CONF_HAS_LEGACY,
    CONF_HAS_LWS,
    CONF_REST_SCHEME,
    CONF_UDP_PORT,
    DEFAULT_UDP_PORT,
    DOMAIN,
    SERVICE_SEND_COMMAND,
    SERVICE_SEND_UDP,
)
from .coordinator import BrightSignCoordinator
from .sync import SyncListener, SyncTracker, async_acquire_listener
from .udp_events import UdpEventTracker, async_register_player

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.EVENT,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
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


SEND_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_COMMAND): _udp_message,
    }
)

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
    udp_events: UdpEventTracker


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

    async def _send_command(call: ServiceCall) -> None:
        device = dr.async_get(hass).async_get(call.data[ATTR_DEVICE_ID])
        entry = None
        for entry_id in device.config_entries if device else ():
            candidate = hass.config_entries.async_get_entry(entry_id)
            if (
                candidate
                and candidate.domain == DOMAIN
                and candidate.state is ConfigEntryState.LOADED
            ):
                entry = candidate
        if entry is None:
            raise ServiceValidationError("Choose a configured BrightSign player")
        coordinator = entry.runtime_data.coordinator
        if not coordinator.has_rest:
            raise ServiceValidationError(
                "This player has no DWS REST API (add the DWS password with Reconfigure)"
            )
        try:
            await coordinator.client.async_send_custom(call.data[ATTR_COMMAND])
        except BrightSignError as err:
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(DOMAIN, SERVICE_SEND_UDP, _send_udp, schema=SEND_UDP_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_SEND_COMMAND, _send_command, schema=SEND_COMMAND_SCHEMA
    )
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: BrightSignConfigEntry) -> bool:
    """1.1 -> 1.2: detect the Info page fallback again (0.3.0 could miss it)."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        data = dict(entry.data)
        if data.get(CONF_HAS_LEGACY) is False and data.get(CONF_REST_SCHEME) is None:
            data.pop(CONF_HAS_LEGACY)
        hass.config_entries.async_update_entry(entry, data=data, minor_version=2)
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
        has_legacy=entry.data.get(CONF_HAS_LEGACY),  # None: detect once
    )
    await coordinator.async_config_entry_first_refresh()

    listener = await async_acquire_listener(hass)
    tracker = SyncTracker(hass, entry.data[CONF_HOST])
    tracker.async_start()

    lws = coordinator.data.lws if coordinator.data else None
    udp_port = entry.options.get(CONF_UDP_PORT) or (
        lws.udp_destination_port if lws and lws.udp_destination_port else DEFAULT_UDP_PORT
    )
    entry.async_on_unload(await async_register_player(hass, udp_port, entry.data[CONF_HOST]))
    udp_events = UdpEventTracker(hass, entry.data[CONF_HOST])
    udp_events.async_start()
    entry.async_on_unload(udp_events.async_stop)

    entry.runtime_data = BrightSignRuntimeData(coordinator, tracker, listener, udp_events)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: BrightSignConfigEntry) -> None:
    """Options changed: reload to apply the UDP port and snapshot interval."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: BrightSignConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.sync.async_stop()
        entry.runtime_data.listener.async_stop()
    return unloaded
