"""Buttons for BrightSign players."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import re

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BrightSignConfigEntry
from .api import BrightSignClient, BrightSignError
from .const import CEC_DISPLAY_ON, CEC_DISPLAY_STANDBY
from .coordinator import BrightSignCoordinator
from .entity import BrightSignEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class BrightSignButtonDescription(ButtonEntityDescription):
    """Describes a REST button."""

    press_fn: Callable[[BrightSignClient], Awaitable[None]]


REST_BUTTONS: tuple[BrightSignButtonDescription, ...] = (
    BrightSignButtonDescription(
        key="reboot",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda c: c.async_reboot(),
    ),
    BrightSignButtonDescription(
        key="cec_display_on",
        translation_key="cec_display_on",
        press_fn=lambda c: c.async_send_cec(CEC_DISPLAY_ON),
    ),
    BrightSignButtonDescription(
        key="cec_display_standby",
        translation_key="cec_display_standby",
        press_fn=lambda c: c.async_send_cec(CEC_DISPLAY_STANDBY),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BrightSignConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create buttons."""
    coordinator = entry.runtime_data.coordinator
    entities: list[ButtonEntity] = []
    if coordinator.has_rest:
        entities += [BrightSignButton(coordinator, d) for d in REST_BUTTONS]
    lws = coordinator.data.lws if coordinator.data else None
    if lws:
        for event in lws.udp_events:
            entities.append(UdpEventButton(coordinator, event["label"], event["action"]))
    async_add_entities(entities)


class BrightSignButton(BrightSignEntity, ButtonEntity):
    """Button calling the local DWS REST API."""

    entity_description: BrightSignButtonDescription

    def __init__(
        self, coordinator: BrightSignCoordinator, description: BrightSignButtonDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """CEC buttons go unavailable once the player turned out not to offer CEC."""
        if self.entity_description.key.startswith("cec_") and (
            self.coordinator.client.cec_supported is False
        ):
            return False
        return super().available

    async def async_press(self) -> None:
        if not self.player_online:
            raise HomeAssistantError(f"BrightSign {self.coordinator.client.host} is offline")
        try:
            await self.entity_description.press_fn(self.coordinator.client)
        except BrightSignError as err:
            self.async_write_ha_state()
            raise HomeAssistantError(f"BrightSign command failed: {err}") from err


class UdpEventButton(BrightSignEntity, ButtonEntity):
    """Sends one UDP command defined in the running presentation."""

    _attr_translation_key = "udp_event"

    def __init__(self, coordinator: BrightSignCoordinator, label: str, action: str) -> None:
        slug = re.sub(r"[^a-z0-9]+", "_", action.lower()).strip("_") or "event"
        super().__init__(coordinator, f"udp_{slug}")
        self._action = action
        self._attr_translation_placeholders = {"name": label}

    async def async_press(self) -> None:
        lws = self.coordinator.data.lws
        port = lws.udp_receive_port if lws else None
        try:
            await self.coordinator.client.async_send_udp_command(self._action, port)
        except BrightSignError as err:
            raise HomeAssistantError(f"BrightSign UDP command failed: {err}") from err
