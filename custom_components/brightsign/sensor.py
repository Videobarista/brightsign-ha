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
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from . import BrightSignConfigEntry
from .api import find_key, info_value
from .const import CONF_SERIAL
from .coordinator import BrightSignCoordinator, BrightSignData
from .entity import BrightSignEntity, BrightSignSyncEntity
from .sync import SyncTracker

PARALLEL_UPDATES = 0


def _uptime(data: BrightSignData) -> Any:
    value = find_key(data.info, "upTimeSeconds")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _video_mode(data: BrightSignData) -> Any:
    mode = find_key(data.video, "activeMode")
    if isinstance(mode, dict):
        return mode.get("modeName") or find_key(mode, "modeName")
    return mode or find_key(data.video, "modeName")


@dataclass(frozen=True, kw_only=True)
class BrightSignSensorDescription(SensorEntityDescription):
    """Describes a polled sensor."""

    value_fn: Callable[[BrightSignData], Any]


REST_SENSORS: tuple[BrightSignSensorDescription, ...] = (
    BrightSignSensorDescription(
        key="os_version",
        translation_key="os_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: info_value(d.info, "FWVersion"),
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
    if coordinator.has_rest:
        entities += [BrightSignSensor(coordinator, d) for d in REST_SENSORS]
    if coordinator.has_lws:
        entities += [BrightSignSensor(coordinator, d) for d in LWS_SENSORS]
        lws = coordinator.data.lws if coordinator.data else None
        for name in sorted(lws.user_vars) if lws else []:
            entities.append(BrightSignUserVariableSensor(coordinator, name))
    entities.append(SerialNumberSensor(coordinator))
    entities += [
        SyncItemSensor(coordinator, tracker),
        SyncItemStartedSensor(coordinator, tracker),
        SyncItemDurationSensor(coordinator, tracker),
    ]
    async_add_entities(entities)


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
            or self.coordinator.config_entry.data.get(CONF_SERIAL)
        )


class BrightSignUserVariableSensor(BrightSignEntity, SensorEntity):
    """A user variable of the running presentation."""

    _attr_translation_key = "user_variable"

    def __init__(self, coordinator: BrightSignCoordinator, name: str) -> None:
        super().__init__(coordinator, f"uservar_{slugify(name) or 'var'}")
        self._var = name
        self._attr_translation_placeholders = {"name": name}

    @property
    def native_value(self) -> str | None:
        lws = self.coordinator.data.lws
        return lws.user_vars.get(self._var) if lws else None


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
