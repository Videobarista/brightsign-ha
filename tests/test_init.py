"""Setting up players: entities per interface, sync entities, unloading."""

from __future__ import annotations

from collections.abc import Generator
import time
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.brightsign.api import BrightSignConnectionError
from custom_components.brightsign.const import (
    CONF_FINGERPRINT,
    CONF_HAS_LEGACY,
    CONF_HAS_LWS,
    CONF_HOSTNAME,
    CONF_REST_SCHEME,
    CONF_SERIAL,
    DOMAIN,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

CLIENT = "custom_components.brightsign.api.BrightSignClient"
# The camera platform needs Home Assistant's HTTP server; it is left out here.
PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]


@pytest.fixture(autouse=True)
def no_sockets() -> Generator[None]:
    """The shared listeners are not opened in tests."""
    with (
        patch("custom_components.brightsign.PLATFORMS", PLATFORMS),
        patch("custom_components.brightsign.sync.SyncListener.async_start", AsyncMock()),
        patch("custom_components.brightsign.ptp.PtpListener.async_start", AsyncMock()),
        patch("custom_components.brightsign.sync.UdpSyncListener.async_start", AsyncMock()),
        patch(
            "custom_components.brightsign.async_register_player",
            AsyncMock(return_value=lambda: None),
        ),
    ):
        yield


def _entity_id(hass: HomeAssistant, platform: str, unique_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)


async def test_basic_player_and_sync_role(hass: HomeAssistant) -> None:
    """A player without DWS: basic entities; sync entities appear with sync traffic."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ABCD12345678",
        title="Wall-leader",
        data={
            CONF_HOST: "10.0.0.5",
            CONF_PASSWORD: "",
            CONF_REST_SCHEME: None,
            CONF_HAS_LWS: False,
            CONF_HAS_LEGACY: False,
            CONF_SERIAL: "ABCD12345678",
            CONF_HOSTNAME: "Wall-leader",
        },
    )
    entry.add_to_hass(hass)
    with (
        patch(f"{CLIENT}.async_reachable", AsyncMock(return_value=True)),
        patch(
            f"{CLIENT}.async_get_lws", AsyncMock(side_effect=BrightSignConnectionError("closed"))
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        online = _entity_id(hass, "binary_sensor", "ABCD12345678_online")
        assert online is not None
        assert hass.states.get(online).state == "on"
        host_name = _entity_id(hass, "sensor", "ABCD12345678_host_name")
        assert hass.states.get(host_name).state == "Wall-leader"
        assert _entity_id(hass, "sensor", "ABCD12345678_sync_role") is None

        runtime = entry.runtime_data
        runtime.udp_sync.sources["10.0.0.5"] = time.monotonic()
        runtime.monitor.async_check()
        await hass.async_block_till_done()
        role = _entity_id(hass, "sensor", "ABCD12345678_sync_role")
        assert role is not None
        assert hass.states.get(role).state == "leader"
        group = _entity_id(hass, "sensor", "ABCD12345678_sync_group")
        assert hass.states.get(group).state == "UDP 11167"
        conflict = _entity_id(hass, "binary_sensor", "ABCD12345678_sync_conflict")
        assert hass.states.get(conflict).state == "off"

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.NOT_LOADED


async def test_rest_player_entities(hass: HomeAssistant) -> None:
    """A player with the REST API gets the REST entities; no duplicate video mode sensor."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="UJE2TEST0001",
        title="BrightSign XT1144 UJE2TEST0001",
        data={
            CONF_HOST: "10.0.0.50",
            CONF_PASSWORD: "UJE2TEST0001",
            CONF_REST_SCHEME: "https",
            CONF_FINGERPRINT: "ab" * 32,
            CONF_HAS_LWS: False,
            CONF_HAS_LEGACY: False,
            CONF_SERIAL: "UJE2TEST0001",
        },
    )
    entry.add_to_hass(hass)
    info = {"serial": "UJE2TEST0001", "model": "XT1144", "FWVersion": "9.0.1"}
    video = {"attached": True, "activeMode": {"modeName": "1920x1080x60p"}}
    with (
        patch(f"{CLIENT}.async_get_info", AsyncMock(return_value=info)),
        patch(f"{CLIENT}.async_get_video_output", AsyncMock(return_value=video)),
        patch(f"{CLIENT}.async_get_power_save", AsyncMock(return_value=False)),
        patch(f"{CLIENT}.async_get_video_modes", AsyncMock(return_value=["1920x1080x60p"])),
        patch(f"{CLIENT}.async_get_edid", AsyncMock(return_value={})),
        patch(f"{CLIENT}.async_get_storage", AsyncMock(return_value={"free": 10, "size": 20})),
        patch(f"{CLIENT}.async_get_unit_name", AsyncMock(return_value="Lobby")),
        patch(f"{CLIENT}.async_reachable", AsyncMock(return_value=True)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert entry.title == "Lobby"  # the unit name replaces the generated title

        unit_name = _entity_id(hass, "sensor", "UJE2TEST0001_unit_name")
        assert hass.states.get(unit_name).state == "Lobby"
        select = _entity_id(hass, "select", "UJE2TEST0001_video_mode_select")
        assert hass.states.get(select).state == "1920x1080x60p"
        assert _entity_id(hass, "sensor", "UJE2TEST0001_video_mode") is None
        storage = _entity_id(hass, "sensor", "UJE2TEST0001_storage_used")
        assert hass.states.get(storage).state == "50.0"

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
