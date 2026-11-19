"""Coordinator behaviour: another player at the address, new interfaces."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.brightsign.api import BrightSignConnectionError, LwsData
from custom_components.brightsign.const import CONF_HAS_LWS, CONF_SERIAL, DOMAIN, REDETECT_EVERY
from custom_components.brightsign.coordinator import BrightSignCoordinator
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir


def _client(serial: str) -> MagicMock:
    client = MagicMock()
    client.host = "10.0.0.5"
    client.async_get_info = AsyncMock(return_value={"serial": serial, "model": "XT1144"})
    client.async_get_video_output = AsyncMock(return_value={})
    client.async_get_power_save = AsyncMock(return_value=None)
    client.async_get_video_modes = AsyncMock(return_value=[])
    client.async_get_edid = AsyncMock(return_value={})
    client.async_get_storage = AsyncMock(return_value={})
    client.async_get_unit_name = AsyncMock(return_value="Wall-leader")
    client.async_get_lws = AsyncMock(side_effect=BrightSignConnectionError("closed"))
    client.async_reachable = AsyncMock(return_value=True)
    return client


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="AAAA11111111",
        title="BrightSign XT1144 AAAA11111111",
        data={CONF_HOST: "10.0.0.5", CONF_SERIAL: "AAAA11111111"},
    )
    entry.add_to_hass(hass)
    return entry


async def test_another_player_at_the_address(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    client = _client("BBBB22222222")
    coordinator = BrightSignCoordinator(hass, entry, client, has_rest=True, has_lws=False)
    data = await coordinator._async_update_data()
    assert not data.online
    assert data.other_player == "BBBB22222222"
    issue_id = f"address_taken_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    client.async_get_info = AsyncMock(return_value={"serial": "aaaa11111111"})
    coordinator.data = data
    data = await coordinator._async_update_data()
    assert data.online
    assert data.unit_name == "Wall-leader"
    assert data.player_name == "Wall-leader"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_new_web_server_is_detected(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    client = _client("AAAA11111111")
    client.async_get_lws = AsyncMock(
        return_value=LwsData(identity={"serialNumber": "AAAA11111111"})
    )
    coordinator = BrightSignCoordinator(hass, entry, client, has_rest=True, has_lws=False)
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        for _ in range(REDETECT_EVERY):
            await coordinator._async_update_data()
    assert entry.data[CONF_HAS_LWS] is True
    reload.assert_called_once_with(entry.entry_id)
