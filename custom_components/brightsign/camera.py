"""Screenshot of what a BrightSign player shows (DWS REST API players)."""

from __future__ import annotations

import time

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BrightSignConfigEntry
from .api import BrightSignError
from .const import CONF_SNAPSHOT_INTERVAL, DEFAULT_SNAPSHOT_INTERVAL
from .coordinator import BrightSignCoordinator
from .entity import BrightSignEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BrightSignConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the screenshot camera for players with the REST API."""
    coordinator = entry.runtime_data.coordinator
    if coordinator.has_rest:
        interval = entry.options.get(CONF_SNAPSHOT_INTERVAL, DEFAULT_SNAPSHOT_INTERVAL)
        async_add_entities([ScreenshotCamera(coordinator, interval)])


class ScreenshotCamera(BrightSignEntity, Camera):
    """Screenshot via the DWS REST API, cached so the player is not asked too often
    (every screenshot is written to, and removed from, the player's SD card)."""

    _attr_translation_key = "screenshot"

    def __init__(self, coordinator: BrightSignCoordinator, interval: int) -> None:
        BrightSignEntity.__init__(self, coordinator, "screenshot")
        Camera.__init__(self)
        self._interval = max(10, int(interval))
        self._image: bytes | None = None
        self._taken = 0.0

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        if self._image is not None and time.monotonic() - self._taken < self._interval:
            return self._image
        if not self.player_online:
            return self._image
        self._taken = time.monotonic()
        try:
            image = await self.coordinator.client.async_snapshot()
        except BrightSignError:
            return self._image  # keep showing the last screenshot
        if image:
            self._image = image
        return self._image
