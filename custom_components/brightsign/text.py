"""User variables of a BrightAuthor presentation (Local Web Server)."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from . import BrightSignConfigEntry
from .api import MAX_VALUE_LENGTH, BrightSignError
from .coordinator import BrightSignCoordinator
from .entity import BrightSignEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BrightSignConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One text entity per user variable of the running presentation."""
    coordinator = entry.runtime_data.coordinator
    lws = coordinator.data.lws if coordinator.data else None
    if coordinator.has_lws and lws:
        async_add_entities(UserVariableText(coordinator, name) for name in sorted(lws.user_vars))


class UserVariableText(BrightSignEntity, TextEntity):
    """A user variable; changing it updates the presentation."""

    _attr_translation_key = "user_variable"
    _attr_native_max = MAX_VALUE_LENGTH

    def __init__(self, coordinator: BrightSignCoordinator, name: str) -> None:
        super().__init__(coordinator, f"uservar_{slugify(name) or 'var'}")
        self._var = name
        self._attr_translation_placeholders = {"name": name}

    @property
    def native_value(self) -> str | None:
        lws = self.coordinator.data.lws
        return lws.user_vars.get(self._var) if lws else None

    async def async_set_value(self, value: str) -> None:
        if not self.player_online:
            raise HomeAssistantError(f"BrightSign {self.coordinator.client.host} is offline")
        try:
            await self.coordinator.client.async_set_user_variable(self._var, value)
        except BrightSignError as err:
            raise HomeAssistantError(f"Changing {self._var} failed: {err}") from err
        await self.coordinator.async_request_refresh()
