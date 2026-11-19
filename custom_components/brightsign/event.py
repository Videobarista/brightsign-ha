"""UDP messages a presentation sends, as an event entity (created on the first message)."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BrightSignConfigEntry
from .const import EVENT_UDP_MESSAGE
from .coordinator import BrightSignCoordinator
from .entity import build_device_info
from .udp_events import UdpEventTracker

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BrightSignConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the UDP message event as soon as the player sends its first message."""
    coordinator = entry.runtime_data.coordinator
    tracker = entry.runtime_data.udp_events

    def _add() -> None:
        async_add_entities([UdpMessageEvent(coordinator, tracker)])

    if tracker.seen:
        _add()
    else:
        tracker.async_on_first_seen(_add)


class UdpMessageEvent(EventEntity):
    """Fires for every UDP message from this player; the text is in the event data."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "udp_message"
    _attr_event_types = ["message"]

    def __init__(self, coordinator: BrightSignCoordinator, tracker: UdpEventTracker) -> None:
        entry = coordinator.config_entry
        self._coordinator = coordinator
        self._tracker = tracker
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_udp_message"
        self._attr_device_info = build_device_info(coordinator)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._tracker.async_add_listener(self._handle))
        if self._tracker.last_message is not None:
            self._handle(self._tracker.last_message)

    @callback
    def _handle(self, message: str) -> None:
        self._trigger_event("message", {"message": message})
        self.async_write_ha_state()
        entry = self._coordinator.config_entry
        device_id = self.registry_entry.device_id if self.registry_entry else None
        self.hass.bus.async_fire(
            EVENT_UDP_MESSAGE,
            {"device_id": device_id, "host": entry.data[CONF_HOST], "message": message},
        )
