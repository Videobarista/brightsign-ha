"""Data update coordinator for a BrightSign player."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import (
    BrightSignAuthError,
    BrightSignCertificateError,
    BrightSignClient,
    BrightSignConnectionError,
    BrightSignNotSupported,
    LwsData,
    info_value,
)
from .const import CONF_HAS_LEGACY, CONF_SERIAL, DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass
class BrightSignData:
    """Snapshot of a player's state."""

    online: bool = False
    info: dict[str, Any] = field(default_factory=dict)
    video: dict[str, Any] = field(default_factory=dict)
    power_save: bool | None = None
    lws: LwsData | None = None
    legacy: dict[str, str] = field(default_factory=dict)

    @property
    def player_name(self) -> str | None:
        """Name the player reports (REST, presentation or legacy Info page)."""
        identity = self.lws.identity if self.lws else {}
        return (
            info_value(self.info, "deviceName")
            or identity.get("unitName")
            or self.legacy.get("name")
            or None
        )


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
        has_legacy: bool | None = False,
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
        self.has_legacy = has_legacy

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
        if self.has_legacy is None and not self.has_rest:
            await self._async_detect_legacy()
        if self.has_legacy and not data.online:
            try:
                data.legacy = await self.client.async_get_legacy_info()
                data.online = True
            except BrightSignAuthError as err:
                raise ConfigEntryAuthFailed(
                    f"BrightSign {self.client.host} rejected the DWS password"
                ) from err
            except (BrightSignConnectionError, BrightSignNotSupported) as err:
                _LOGGER.debug("Info page poll of %s failed: %s", self.client.host, err)
        if not data.online:
            data.online = await self.client.async_reachable()
        self._async_adopt_player_name(data)
        return data

    async def _async_detect_legacy(self) -> None:
        """Entries made before the Info page fallback: detect it once, quietly."""
        try:
            found = bool(await self.client.async_get_legacy_info())
        except BrightSignConnectionError:
            return  # offline: try again on the next poll
        except (BrightSignAuthError, BrightSignNotSupported):
            found = False
        self.has_legacy = found
        _LOGGER.debug("Info page fallback on %s: %s", self.client.host, found)
        entry = self.config_entry
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_HAS_LEGACY: found}
        )

    def _async_adopt_player_name(self, data: BrightSignData) -> None:
        """Replace an address-based default title by the name the player reports."""
        name = data.player_name
        entry = self.config_entry
        if not name or entry.title == name:
            return
        serial = entry.data.get(CONF_SERIAL) or data.legacy.get("serial") or ""
        generated = entry.title in (f"BrightSign {entry.data[CONF_HOST]}", "BrightSign player") or (
            bool(serial) and entry.title.startswith("BrightSign ") and entry.title.endswith(serial)
        )
        if not generated:
            return  # the user (or an earlier poll) already chose a name
        self.hass.config_entries.async_update_entry(entry, title=name)
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)}
        )
        if device is not None and device.name_by_user is None:
            registry.async_update_device(device.id, name=name)
