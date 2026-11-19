"""Video mode selection (DWS REST API players)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BrightSignConfigEntry
from .api import BrightSignError, find_key
from .coordinator import BrightSignCoordinator
from .entity import BrightSignEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BrightSignConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the video mode select for players with the REST API."""
    coordinator = entry.runtime_data.coordinator
    if coordinator.has_rest:
        async_add_entities([VideoModeSelect(coordinator)])


class VideoModeSelect(BrightSignEntity, SelectEntity):
    """Video mode of the HDMI output. The presentation sets its own mode again when it
    restarts."""

    _attr_translation_key = "video_mode_select"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: BrightSignCoordinator) -> None:
        super().__init__(coordinator, "video_mode_select")

    def _current(self) -> str | None:
        mode = find_key(self.coordinator.data.video, "activeMode")
        if isinstance(mode, dict):
            mode = mode.get("modeName") or find_key(mode, "modeName")
        return mode if isinstance(mode, str) else None

    @property
    def options(self) -> list[str]:
        modes = list(self.coordinator.data.video_modes)
        current = self._current()
        if current and current not in modes:
            modes.insert(0, current)
        return modes

    @property
    def current_option(self) -> str | None:
        return self._current()

    async def async_select_option(self, option: str) -> None:
        if option not in self.coordinator.data.video_modes:
            raise HomeAssistantError(f"{option} is not offered by the connected display")
        if not self.player_online:
            raise HomeAssistantError(f"BrightSign {self.coordinator.client.host} is offline")
        try:
            await self.coordinator.client.async_set_video_mode(option)
        except BrightSignError as err:
            raise HomeAssistantError(f"Changing the video mode failed: {err}") from err
        await self.coordinator.async_request_refresh()
