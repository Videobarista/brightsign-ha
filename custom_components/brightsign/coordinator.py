"""Data update coordinator for a BrightSign player."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import (
    BrightSignAuthError,
    BrightSignCertificateError,
    BrightSignClient,
    BrightSignConnectionError,
    BrightSignNotSupported,
    LwsData,
)
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass
class BrightSignData:
    """Snapshot of a player's state."""

    online: bool = False
    info: dict[str, Any] = field(default_factory=dict)
    video: dict[str, Any] = field(default_factory=dict)
    power_save: bool | None = None
    lws: LwsData | None = None


class BrightSignCoordinator(DataUpdateCoordinator[BrightSignData]):
    """Polls the player. An unreachable player is reported offline, not failed."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: BrightSignClient,
        has_rest: bool,
        has_lws: bool,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {client.host}",
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.has_rest = has_rest
        self.has_lws = has_lws

    async def _async_update_data(self) -> BrightSignData:
        data = BrightSignData()
        if self.has_rest:
            try:
                data.info = await self.client.async_get_info()
                data.online = True
                try:
                    data.video = await self.client.async_get_video_output()
                except BrightSignNotSupported:
                    data.video = {}
                try:
                    data.power_save = await self.client.async_get_power_save()
                except BrightSignNotSupported:
                    data.power_save = None
            except BrightSignCertificateError as err:
                raise ConfigEntryAuthFailed(
                    f"BrightSign {self.client.host}: HTTPS certificate changed"
                ) from err
            except BrightSignAuthError as err:
                raise ConfigEntryAuthFailed(
                    f"BrightSign {self.client.host} rejected the DWS password"
                ) from err
            except (BrightSignConnectionError, BrightSignNotSupported) as err:
                _LOGGER.debug("REST poll of %s failed: %s", self.client.host, err)
        if self.has_lws:
            try:
                data.lws = await self.client.async_get_lws()
                data.online = True
            except BrightSignConnectionError as err:
                _LOGGER.debug("Local Web Server poll of %s failed: %s", self.client.host, err)
        if not data.online:
            data.online = await self.client.async_reachable()
        return data
