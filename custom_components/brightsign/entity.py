"""Base entities for the BrightSign integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import info_value
from .const import CONF_MAC, CONF_SERIAL, DOMAIN, MANUFACTURER
from .coordinator import BrightSignCoordinator
from .sync import SyncTracker


def build_device_info(coordinator: BrightSignCoordinator) -> DeviceInfo:
    """Device registry info from the entry plus the latest poll."""
    entry = coordinator.config_entry
    data = coordinator.data
    info = data.info if data else {}
    identity = data.lws.identity if data and data.lws else {}
    device = DeviceInfo(
        identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
        manufacturer=MANUFACTURER,
        name=entry.title,
        model=info_value(info, "model"),
        sw_version=info_value(info, "FWVersion") or identity.get("firmwareVersion"),
        serial_number=info_value(info, "serial")
        or identity.get("serialNumber")
        or entry.data.get(CONF_SERIAL),
        configuration_url=f"http://{entry.data[CONF_HOST]}/",
    )
    if mac := entry.data.get(CONF_MAC):
        device["connections"] = {(CONNECTION_NETWORK_MAC, mac)}
    return device


class BrightSignEntity(CoordinatorEntity[BrightSignCoordinator]):
    """Entity backed by the poll coordinator; unavailable while the player is offline."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BrightSignCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{key}"
        self._attr_device_info = build_device_info(coordinator)

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return super().available and data is not None and data.online


class BrightSignSyncEntity(Entity):
    """Entity fed by passive sync multicast traffic from this player."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: BrightSignCoordinator, tracker: SyncTracker, key: str) -> None:
        entry = coordinator.config_entry
        self._tracker = tracker
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{key}"
        self._attr_device_info = build_device_info(coordinator)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._tracker.async_add_listener(self.async_write_ha_state))
