"""Data update coordinator for a BrightSign player."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import (
    BrightSignAuthError,
    BrightSignCertificateError,
    BrightSignClient,
    BrightSignConnectionError,
    BrightSignNotSupported,
    LwsData,
    find_key,
    info_value,
    parse_edid,
)
from .const import (
    CONF_ADOPTED_NAME,
    CONF_HAS_LEGACY,
    CONF_HAS_LWS,
    CONF_HOSTNAME,
    CONF_SERIAL,
    DOMAIN,
    REDETECT_EVERY,
    SCAN_INTERVAL,
    STORAGE_POLL_EVERY,
)

_LOGGER = logging.getLogger(__name__)


def _is_serial(value: str) -> bool:
    """BrightSign serial numbers are 12 letters and digits."""
    return len(value) == 12 and value.isalnum()


@dataclass
class BrightSignData:
    """Snapshot of a player's state."""

    online: bool = False
    info: dict[str, Any] = field(default_factory=dict)
    video: dict[str, Any] = field(default_factory=dict)
    power_save: bool | None = None
    lws: LwsData | None = None
    legacy: dict[str, str] = field(default_factory=dict)
    video_modes: list[str] = field(default_factory=list)
    display: dict[str, str] = field(default_factory=dict)
    storage: dict[str, int] = field(default_factory=dict)
    unit_name: str | None = None  # REST: registry networking/un
    other_player: str | None = None  # serial of another player found at this address

    @property
    def player_name(self) -> str | None:
        """Name the player reports (REST, presentation or legacy Info page)."""
        identity = self.lws.identity if self.lws else {}
        return (
            info_value(self.info, "deviceName")
            or self.unit_name
            or identity.get("unitName")
            or self.legacy.get("name")
            or None
        )

    @property
    def reported_serial(self) -> str | None:
        """Serial number the player at this address reports."""
        identity = self.lws.identity if self.lws else {}
        serial = (
            info_value(self.info, "serial")
            or identity.get("serialNumber")
            or self.legacy.get("serial")
        )
        if not serial:
            return None
        return str(serial).strip().upper() or None


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
        self._polls = 0
        self._cycle = 0
        self._unit_name: str | None = None
        self._unit_name_supported = True
        self._video_modes: list[str] = []
        self._display: dict[str, str] = {}
        self._display_attached: object = None
        self._storage: dict[str, int] = {}

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
                await self._async_update_rest_extras(data)
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
        self._cycle += 1
        if data.online and self._cycle % REDETECT_EVERY == 0:
            await self._async_redetect()
        if not self._async_check_identity(data):
            return BrightSignData(other_player=data.reported_serial)
        self._async_adopt_player_name(data)
        return data

    def _async_check_identity(self, data: BrightSignData) -> bool:
        """False when another player answers at this address (e.g. after a DHCP change)."""
        entry = self.config_entry
        expected = str(entry.data.get(CONF_SERIAL) or "").strip().upper()
        reported = data.reported_serial
        issue_id = f"address_taken_{entry.entry_id}"
        if reported and not expected:
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_SERIAL: reported}
            )
        if (
            not reported
            or not expected
            or reported == expected
            # Only compare real serial numbers (12 characters): an older entry may hold a
            # guess from a custom host name.
            or not (_is_serial(reported) and _is_serial(expected))
        ):
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return True
        if self.data is None or self.data.other_player != reported:
            _LOGGER.warning(
                "Another BrightSign player answers at %s: %s is shown offline until it is found "
                "at its new address (or reconfigured)",
                self.client.host,
                entry.title,
            )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="address_taken",
            translation_placeholders={"name": entry.title, "host": self.client.host},
        )
        return False

    async def _async_redetect(self) -> None:
        """A new presentation can bring a web server; the Info page can be switched on."""
        entry = self.config_entry
        found: dict[str, bool] = {}
        if not self.has_lws:
            try:
                lws = await self.client.async_get_lws()
            except BrightSignConnectionError:
                lws = None
            if lws is not None and lws.identity:
                found[CONF_HAS_LWS] = True
        if not self.has_rest and self.has_legacy is False:
            try:
                if await self.client.async_get_legacy_info():
                    found[CONF_HAS_LEGACY] = True
            except (BrightSignConnectionError, BrightSignAuthError, BrightSignNotSupported):
                pass  # still not available
        if found:
            _LOGGER.debug("New interfaces on %s: %s; reloading", self.client.host, list(found))
            self.hass.config_entries.async_update_entry(entry, data={**entry.data, **found})
            self.hass.config_entries.async_schedule_reload(entry.entry_id)

    async def _async_update_rest_extras(self, data: BrightSignData) -> None:
        """Slow-changing REST data: video modes, connected display, storage."""
        self._polls += 1
        attached = find_key(data.video, "attached")
        try:
            if not self._video_modes:
                self._video_modes = await self.client.async_get_video_modes()
            if attached != self._display_attached or not self._display:
                self._display_attached = attached
                self._display = parse_edid(find_key(data.video, "edid"))
                if not self._display and attached:
                    self._display = await self.client.async_get_edid()
            if not self._storage or self._polls % STORAGE_POLL_EVERY == 0:
                self._storage = await self.client.async_get_storage()
        except (BrightSignConnectionError, BrightSignNotSupported) as err:
            _LOGGER.debug("Extra REST data of %s not available: %s", self.client.host, err)
        if self._unit_name_supported and (
            self._polls == 1 or self._polls % STORAGE_POLL_EVERY == 0
        ):
            try:
                self._unit_name = await self.client.async_get_unit_name()
            except (BrightSignNotSupported, BrightSignAuthError):
                self._unit_name_supported = False  # not offered (or not allowed): stop asking
            except BrightSignConnectionError as err:
                _LOGGER.debug("Unit name of %s not available: %s", self.client.host, err)
        data.unit_name = self._unit_name
        data.video_modes = self._video_modes
        data.display = self._display if attached is not False else {}
        data.storage = self._storage

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
        """Replace an address-based default title by the player's own (network) name."""
        entry = self.config_entry
        hostname = str(entry.data.get(CONF_HOSTNAME) or "")
        # A host name of its own (not the default brightsign-<serial>) names the player too
        network_name = (
            hostname if hostname and not hostname.lower().startswith("brightsign-") else None
        )
        name = data.player_name or network_name
        if not name or entry.title == name:
            return
        serial = entry.data.get(CONF_SERIAL) or data.legacy.get("serial") or ""
        generated = (
            entry.title in (f"BrightSign {entry.data[CONF_HOST]}", "BrightSign player")
            or (
                bool(serial)
                and entry.title.startswith("BrightSign ")
                and entry.title.endswith(serial)
            )
            # A name the integration gave earlier follows the player (e.g. a new presentation)
            or entry.title == entry.data.get(CONF_ADOPTED_NAME)
        )
        if not generated:
            return  # the user chose this name
        self.hass.config_entries.async_update_entry(
            entry, title=name, data={**entry.data, CONF_ADOPTED_NAME: name}
        )
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)}
        )
        if device is not None and device.name_by_user is None:
            registry.async_update_device(device.id, name=name)
