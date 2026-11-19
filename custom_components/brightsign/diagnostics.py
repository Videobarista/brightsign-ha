"""Diagnostics for the BrightSign integration (secrets and identifiers redacted)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import BrightSignConfigEntry
from .const import CONF_FINGERPRINT, CONF_HOSTNAME, CONF_MAC, CONF_SERIAL

TO_REDACT = {
    CONF_PASSWORD,
    CONF_HOST,
    CONF_MAC,
    CONF_SERIAL,
    CONF_FINGERPRINT,
    CONF_HOSTNAME,
    "serial",
    "serialNumber",
    "deviceName",
    "unitName",
    "unitDescription",
    "ethernet",
    "wireless",
    "network",
    "ip",
    "mac",
    "user_vars",
    "unit_name",
    "other_player",
    "other_leaders",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BrightSignConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    runtime = entry.runtime_data
    data = runtime.coordinator.data
    sync = runtime.sync
    return async_redact_data(
        {
            "entry": dict(entry.data),
            "capabilities": {
                "rest": runtime.coordinator.has_rest,
                "local_web_server": runtime.coordinator.has_lws,
            },
            "data": asdict(data) if data else None,
            "sync": {
                "active": sync.active,
                "item": sync.item,
                "last_item_duration": sync.last_item_duration,
                "encrypted": sync.encrypted,
                "domain": sync.domain,
            },
            "sync_role": asdict(runtime.monitor.view(entry.data[CONF_HOST])),
        },
        TO_REDACT,
    )
