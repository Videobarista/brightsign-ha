"""Sensors for BrightSign players."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CONF_HOST, PERCENTAGE, EntityCategory, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BrightSignConfigEntry
from .api import find_key, info_value, parse_uptime
from .const import CONF_SERIAL
from .coordinator import BrightSignCoordinator, BrightSignData
from .entity import (
    BrightSignEntity,
    BrightSignSyncEntity,
    async_add_sync_entities_when_leader,
    build_device_info,
)
from .sync import SyncTracker
from .udp_events import UdpEventTracker

PARALLEL_UPDATES = 0


def _uptime(data: BrightSignData) -> Any:
    value = find_key(data.info, "upTimeSeconds")
    try:
        if value is not None:
            return int(value)
    except (TypeError, ValueError):
        pass
    return parse_uptime(data.legacy.get("uptime"))


def _video_mode(data: BrightSignData) -> Any:
    mode = find_key(data.video, "activeMode")
    if isinstance(mode, dict):
        mode = mode.get("modeName") or find_key(mode, "modeName")
    return mode or find_key(data.video, "modeName") or data.legacy.get("video_mode")


@dataclass(frozen=True, kw_only=True)
class BrightSignSensorDescription(SensorEntityDescription):
    """Describes a polled sensor."""

    value_fn: Callable[[BrightSignData], Any]


REST_SENSORS: tuple[BrightSignSensorDescription, ...] = (
    BrightSignSensorDescription(
        key="os_version",
        translation_key="os_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: info_value(d.info, "FWVersion") or d.legacy.get("firmware"),
    ),
    BrightSignSensorDescription(
        key="uptime",
        translation_key="uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_uptime,
    ),
    BrightSignSensorDescription(
        key="video_mode",
        translation_key="video_mode",
        value_fn=_video_mode,
    ),
)


def _display_name(data: BrightSignData) -> str | None:
    display = data.display
    name = " ".join(part for part in (display.get("manufacturer"), display.get("model")) if part)
    return name or display.get("product_code")


def _storage_used(data: BrightSignData) -> float | None:
    size, free = data.storage.get("size"), data.storage.get("free")
    if not size or free is None:
        return None
    return round(100 * (size - free) / size, 1)


EXTRA_REST_SENSORS: tuple[BrightSignSensorDescription, ...] = (
    BrightSignSensorDescription(
        key="connected_display",
        translation_key="connected_display",
        value_fn=_display_name,
    ),
    BrightSignSensorDescription(
        key="storage_free",
        translation_key="storage_free",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.storage.get("free"),
    ),
    BrightSignSensorDescription(
        key="storage_used",
        translation_key="storage_used",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_storage_used,
    ),
)

LEGACY_SENSORS: tuple[BrightSignSensorDescription, ...] = (
    BrightSignSensorDescription(
        key="poe",
        translation_key="poe",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.legacy.get("poe"),
    ),
)

LWS_SENSORS: tuple[BrightSignSensorDescription, ...] = (
    BrightSignSensorDescription(
        key="autorun_version",
        translation_key="autorun_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.lws.identity.get("autorunVersion") if d.lws else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BrightSignConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors for what the player offers."""
    coordinator = entry.runtime_data.coordinator
    tracker = entry.runtime_data.sync
    entities: list[SensorEntity] = []
    if coordinator.has_rest or coordinator.has_legacy:
        entities += [BrightSignSensor(coordinator, d) for d in REST_SENSORS]
    if coordinator.has_rest:
        entities += [BrightSignSensor(coordinator, d) for d in EXTRA_REST_SENSORS]
    if coordinator.has_legacy and not coordinator.has_rest:
        entities += [BrightSignSensor(coordinator, d) for d in LEGACY_SENSORS]
    if coordinator.has_lws:
        entities += [BrightSignSensor(coordinator, d) for d in LWS_SENSORS]
    entities += [SerialNumberSensor(coordinator), IpAddressSensor(coordinator)]
    async_add_entities(entities)
    async_add_sync_entities_when_leader(
        hass,
        coordinator,
        tracker,
        "sensor",
        ["sync_item", "sync_item_started", "sync_item_duration"],
        lambda: [
            SyncItemSensor(coordinator, tracker),
            SyncItemStartedSensor(coordinator, tracker),
            SyncItemDurationSensor(coordinator, tracker),
        ],
        async_add_entities,
    )
    udp_events = entry.runtime_data.udp_events
    if udp_events.seen:
        async_add_entities([LastUdpMessageSensor(coordinator, udp_events)])
    else:
        udp_events.async_on_first_seen(
            lambda: async_add_entities([LastUdpMessageSensor(coordinator, udp_events)])
        )


class BrightSignSensor(BrightSignEntity, SensorEntity):
    """Polled sensor."""

    entity_description: BrightSignSensorDescription

    def __init__(
        self, coordinator: BrightSignCoordinator, description: BrightSignSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)


class SerialNumberSensor(BrightSignEntity, SensorEntity):
    """Serial number (also the factory default DWS password)."""

    _attr_translation_key = "serial_number"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BrightSignCoordinator) -> None:
        super().__init__(coordinator, "serial_number")

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        identity = data.lws.identity if data and data.lws else {}
        return (
            info_value(data.info, "serial")
            or identity.get("serialNumber")
            or data.legacy.get("serial")
            or self.coordinator.config_entry.data.get(CONF_SERIAL)
        )


class IpAddressSensor(BrightSignEntity, SensorEntity):
    """IP address (or host name) the integration uses for this player."""

    _attr_translation_key = "ip_address"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BrightSignCoordinator) -> None:
        super().__init__(coordinator, "ip_address")

    @property
    def native_value(self) -> str:
        return self.coordinator.config_entry.data[CONF_HOST]


class SyncItemSensor(BrightSignSyncEntity, SensorEntity):
    """Sync item the group plays (when this player is the sync leader)."""

    _attr_translation_key = "sync_item"

    def __init__(self, coordinator: BrightSignCoordinator, tracker: SyncTracker) -> None:
        super().__init__(coordinator, tracker, "sync_item")

    @property
    def native_value(self) -> str | None:
        return self._tracker.item

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"encrypted_traffic": self._tracker.encrypted}


class SyncItemStartedSensor(BrightSignSyncEntity, SensorEntity):
    """When the current sync item (re)started, as seen by Home Assistant."""

    _attr_translation_key = "sync_item_started"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: BrightSignCoordinator, tracker: SyncTracker) -> None:
        super().__init__(coordinator, tracker, "sync_item_started")

    @property
    def native_value(self) -> datetime | None:
        return self._tracker.item_started


class SyncItemDurationSensor(BrightSignSyncEntity, SensorEntity):
    """Play time of the previous sync item (loop length for a single looping item)."""

    _attr_translation_key = "sync_item_duration"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: BrightSignCoordinator, tracker: SyncTracker) -> None:
        super().__init__(coordinator, tracker, "sync_item_duration")

    @property
    def native_value(self) -> float | None:
        return self._tracker.last_item_duration


class LastUdpMessageSensor(SensorEntity):
    """Last UDP message the presentation sent (created on the first message)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "last_udp_message"

    def __init__(self, coordinator: BrightSignCoordinator, tracker: UdpEventTracker) -> None:
        entry = coordinator.config_entry
        self._tracker = tracker
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_last_udp_message"
        self._attr_device_info = build_device_info(coordinator)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._tracker.async_add_listener(lambda _message: self.async_write_ha_state())
        )

    @property
    def native_value(self) -> str | None:
        return self._tracker.last_message

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        received = self._tracker.last_received
        return {"received": received.isoformat() if received else None}
