"""Binary sensors for BrightSign players."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from . import BrightSignConfigEntry
from .api import find_key
from .coordinator import BrightSignCoordinator
from .entity import BrightSignEntity, BrightSignSyncEntity, async_add_sync_entities_when_leader
from .sync import SyncTracker

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BrightSignConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create binary sensors."""
    coordinator = entry.runtime_data.coordinator
    tracker = entry.runtime_data.sync
    entities: list[BinarySensorEntity] = [OnlineBinarySensor(coordinator)]
    if coordinator.has_rest:
        entities.append(HdmiConnectedBinarySensor(coordinator))
    async_add_entities(entities)
    async_add_sync_entities_when_leader(
        hass,
        coordinator,
        tracker,
        "binary_sensor",
        ["sync_active"],
        lambda: [SyncActiveBinarySensor(coordinator, tracker)],
        async_add_entities,
    )


class OnlineBinarySensor(BrightSignEntity, BinarySensorEntity):
    """Whether the player answers on the network."""

    _attr_translation_key = "online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BrightSignCoordinator) -> None:
        super().__init__(coordinator, "online")

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.online


class HdmiConnectedBinarySensor(BrightSignEntity, BinarySensorEntity):
    """Whether a display is attached to HDMI (EDID readable)."""

    _attr_translation_key = "hdmi_connected"
    _attr_device_class = BinarySensorDeviceClass.PLUG

    def __init__(self, coordinator: BrightSignCoordinator) -> None:
        super().__init__(coordinator, "hdmi_connected")

    @property
    def is_on(self) -> bool | None:
        if not self.player_online:
            return False
        value = find_key(self.coordinator.data.video, "attached")
        return value if isinstance(value, bool) else None


class SyncActiveBinarySensor(BrightSignSyncEntity, BinarySensorEntity):
    """On while this player sends sync traffic (it is a running sync leader)."""

    _attr_translation_key = "sync_active"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: BrightSignCoordinator, tracker: SyncTracker) -> None:
        super().__init__(coordinator, tracker, "sync_active")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_track_time_interval(self.hass, self._tick, timedelta(seconds=5)))

    @callback
    def _tick(self, _now: datetime) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._tracker.active
