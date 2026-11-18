"""HDMI output switch for BrightSign players."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BrightSignConfigEntry
from .api import BrightSignError
from .coordinator import BrightSignCoordinator
from .entity import BrightSignEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BrightSignConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the HDMI output switch (REST API players only)."""
    coordinator = entry.runtime_data.coordinator
    if coordinator.has_rest:
        async_add_entities([HdmiOutputSwitch(coordinator)])


class HdmiOutputSwitch(BrightSignEntity, SwitchEntity):
    """HDMI output on/off (power-save mode); most displays go to standby when off."""

    _attr_translation_key = "hdmi_output"

    def __init__(self, coordinator: BrightSignCoordinator) -> None:
        super().__init__(coordinator, "hdmi_output")

    @property
    def is_on(self) -> bool | None:
        power_save = self.coordinator.data.power_save
        return None if power_save is None else not power_save

    async def _set(self, on: bool) -> None:
        try:
            await self.coordinator.client.async_set_hdmi_output(on)
        except BrightSignError as err:
            raise HomeAssistantError(f"BrightSign HDMI output change failed: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
