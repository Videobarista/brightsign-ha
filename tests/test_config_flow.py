"""Config flow tests for the BrightSign integration."""

from __future__ import annotations

from ipaddress import ip_address
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.brightsign.api import ProbeResult
from custom_components.brightsign.config_flow import serial_from_hostname
from custom_components.brightsign.const import (
    CONF_FINGERPRINT,
    CONF_HAS_LEGACY,
    CONF_HAS_LWS,
    CONF_MAC,
    CONF_REST_SCHEME,
    CONF_SERIAL,
    CONF_SNAPSHOT_INTERVAL,
    CONF_UDP_PORT,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.discovery_flow import DiscoveryKey
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

PROBE = "custom_components.brightsign.config_flow.BrightSignClient.async_probe"
SCAN = "custom_components.brightsign.config_flow.BrightSignConfigFlow._async_scan"
SETUP = "custom_components.brightsign.async_setup_entry"


def REST_OK() -> ProbeResult:
    return ProbeResult(
        reachable=True,
        rest_scheme="https",
        serial="XAE3TEST0001",
        model="HD224",
        name="Lobby",
        fingerprint="ab" * 32,
    )


def LEGACY() -> ProbeResult:
    return ProbeResult(reachable=True)


def AUTH_FAILED() -> ProbeResult:
    return ProbeResult(reachable=True, rest_scheme="https", auth_failed=True)


def OFFLINE() -> ProbeResult:
    return ProbeResult(reachable=False)


@pytest.fixture(autouse=True)
def mock_setup_entry():
    with patch(SETUP, AsyncMock(return_value=True)) as mock:
        yield mock


async def test_user_rest_player(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    with patch(PROBE, AsyncMock(return_value=REST_OK())):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "10.0.0.5", CONF_PASSWORD: "secret"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Lobby"
    assert result["data"][CONF_REST_SCHEME] == "https"
    assert result["data"][CONF_SERIAL] == "XAE3TEST0001"
    assert result["result"].unique_id == "XAE3TEST0001"


async def test_user_legacy_player_uses_host_as_id(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(PROBE, AsyncMock(return_value=LEGACY())):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "10.0.0.6", CONF_PASSWORD: ""}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "BrightSign player"
    assert result["data"][CONF_REST_SCHEME] is None
    assert result["data"][CONF_HAS_LWS] is False
    assert result["result"].unique_id == "10.0.0.6"


async def test_user_errors_then_recover(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(PROBE, AsyncMock(return_value=OFFLINE())):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "10.0.0.7", CONF_PASSWORD: ""}
        )
    assert result["errors"] == {"base": "cannot_connect"}
    with patch(PROBE, AsyncMock(return_value=AUTH_FAILED())):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "10.0.0.7", CONF_PASSWORD: "wrong"}
        )
    assert result["errors"] == {CONF_PASSWORD: "invalid_auth"}
    with patch(PROBE, AsyncMock(return_value=REST_OK())):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "10.0.0.7", CONF_PASSWORD: "right"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_already_configured(hass: HomeAssistant) -> None:
    MockConfigEntry(
        domain=DOMAIN, unique_id="XAE3TEST0001", data={CONF_HOST: "10.0.0.9"}
    ).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(PROBE, AsyncMock(return_value=REST_OK())):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "10.0.0.5", CONF_PASSWORD: ""}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_dhcp_discovery(hass: HomeAssistant) -> None:
    info = DhcpServiceInfo(ip="10.0.0.20", hostname="brightsign-x", macaddress="90ac3f0495aa")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=info
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    with patch(PROBE, AsyncMock(return_value=LEGACY())):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: ""}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MAC] == "90:ac:3f:04:95:aa"
    assert result["result"].unique_id == "90:ac:3f:04:95:aa"


async def test_dhcp_updates_host_of_known_player(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="SERIAL1",
        data={CONF_HOST: "10.0.0.20", CONF_MAC: "90:ac:3f:04:95:aa"},
    )
    entry.add_to_hass(hass)
    info = DhcpServiceInfo(ip="10.0.0.21", hostname="brightsign-x", macaddress="90ac3f0495aa")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=info
    )
    assert result["type"] is FlowResultType.ABORT
    assert entry.data[CONF_HOST] == "10.0.0.21"


async def test_dhcp_discovery_auth_error(hass: HomeAssistant) -> None:
    info = DhcpServiceInfo(ip="10.0.0.30", hostname="brightsign-y", macaddress="90ac3f28628c")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=info
    )
    with patch(PROBE, AsyncMock(return_value=AUTH_FAILED())):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "wrong"}
        )
    assert result["errors"] == {CONF_PASSWORD: "invalid_auth"}
    with patch(PROBE, AsyncMock(return_value=REST_OK())):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "serial"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "XAE3TEST0001"


async def test_zeroconf_discovery(hass: HomeAssistant) -> None:
    info = ZeroconfServiceInfo(
        ip_address=ip_address("10.0.0.40"),
        ip_addresses=[ip_address("10.0.0.40")],
        hostname="brightsign-XAE3TEST0001.local.",
        name="BrightSign Web Service._http._tcp.local.",
        port=8080,
        type="_http._tcp.local.",
        properties={"serialnumber": "XAE3TEST0001", "unitname": "Lobby"},
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info
    )
    assert result["step_id"] == "discovery_confirm"
    with patch(PROBE, AsyncMock(return_value=ProbeResult(reachable=True, lws=True))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: ""}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Lobby"
    assert result["result"].unique_id == "XAE3TEST0001"


async def test_reauth(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="XAE3TEST0001",
        data={CONF_HOST: "10.0.0.5", CONF_PASSWORD: "old", CONF_REST_SCHEME: "https"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    with patch(PROBE, AsyncMock(return_value=AUTH_FAILED())):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "x"}
        )
    assert result["errors"] == {CONF_PASSWORD: "invalid_auth"}
    with patch(PROBE, AsyncMock(return_value=REST_OK())):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"


async def test_user_without_password_adds_basic_player(hass: HomeAssistant) -> None:
    """A protected player without a password is added without the REST API."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(PROBE, AsyncMock(return_value=AUTH_FAILED())):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "10.0.0.50", CONF_PASSWORD: ""}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_REST_SCHEME] is None
    assert result["data"][CONF_FINGERPRINT] is None


async def test_reconfigure_adds_password(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="10.0.0.50",
        data={CONF_HOST: "10.0.0.50", CONF_PASSWORD: "", CONF_REST_SCHEME: None},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    with patch(PROBE, AsyncMock(return_value=AUTH_FAILED())):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "10.0.0.50", CONF_PASSWORD: "wrong"}
        )
    assert result["errors"] == {CONF_PASSWORD: "invalid_auth"}
    with patch(PROBE, AsyncMock(return_value=REST_OK())):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "10.0.0.51", CONF_PASSWORD: "serial"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "10.0.0.51"
    assert entry.data[CONF_REST_SCHEME] == "https"
    assert entry.data[CONF_FINGERPRINT] == "ab" * 32


async def test_dhcp_hostname_serial_used_as_default_password(hass: HomeAssistant) -> None:
    """Without a password the serial from the DHCP hostname is tried (upper case)."""
    info = DhcpServiceInfo(
        ip="10.0.0.60", hostname="brightsign-abc123456", macaddress="90ac3f000001"
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=info
    )
    probe = AsyncMock(side_effect=[AUTH_FAILED(), REST_OK()])
    with patch(PROBE, probe):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: ""}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PASSWORD] == "ABC123456"
    assert result["data"][CONF_REST_SCHEME] == "https"
    assert probe.call_count == 2


async def test_user_serial_from_presentation_used_as_default_password(hass: HomeAssistant) -> None:
    """The serial reported by the presentation (no auth needed) is tried as password."""
    first = ProbeResult(
        reachable=True, rest_scheme="https", auth_failed=True, lws=True, serial="xae3test0001"
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(PROBE, AsyncMock(side_effect=[first, REST_OK()])):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "10.0.0.61", CONF_PASSWORD: ""}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PASSWORD] == "XAE3TEST0001"


async def test_serial_rejected_falls_back_to_basic(hass: HomeAssistant) -> None:
    """When the serial is not the password either, the player is added without REST."""
    info = DhcpServiceInfo(
        ip="10.0.0.62", hostname="brightsign-abc123456", macaddress="90ac3f000002"
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=info
    )
    with patch(PROBE, AsyncMock(side_effect=[AUTH_FAILED(), AUTH_FAILED(), AUTH_FAILED()])):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: ""}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PASSWORD] == ""
    assert result["data"][CONF_REST_SCHEME] is None


def test_serial_from_hostname() -> None:
    assert serial_from_hostname("brightsign-abcd12345678.lan") == "ABCD12345678"
    assert serial_from_hostname("Lobby") is None
    assert serial_from_hostname(None) is None


async def test_legacy_player_named_from_info_page(hass: HomeAssistant) -> None:
    """An older player is named after the name on its Info page, not its address."""
    legacy = ProbeResult(
        reachable=True, legacy=True, name="Lobby", model="XD1032", serial="ABCD12345678"
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(PROBE, AsyncMock(return_value=legacy)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "10.0.0.70", CONF_PASSWORD: ""}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Lobby"
    assert result["data"][CONF_HAS_LEGACY] is True
    assert result["result"].unique_id == "ABCD12345678"


async def test_title_from_model_and_serial(hass: HomeAssistant) -> None:
    probe = ProbeResult(reachable=True, legacy=True, model="XD1032", serial="ABCD12345678")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(PROBE, AsyncMock(return_value=probe)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "10.0.0.71", CONF_PASSWORD: ""}
        )
    assert result["title"] == "BrightSign XD1032 ABCD12345678"


async def test_options_flow(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="X1", data={CONF_HOST: "10.0.0.80"})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UDP_PORT: 5001, CONF_SNAPSHOT_INTERVAL: 120}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {CONF_UDP_PORT: 5001, CONF_SNAPSHOT_INTERVAL: 120}


async def test_user_picks_player_found_on_network(hass: HomeAssistant) -> None:
    found = {"10.0.0.70": "90:ac:3f:00:00:70", "10.0.0.71": "90:ac:3f:00:00:71"}
    MockConfigEntry(domain=DOMAIN, unique_id="OTHER", data={CONF_HOST: "10.0.0.71"}).add_to_hass(
        hass
    )
    with patch(SCAN, AsyncMock(return_value=found)):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"] == {"found": "1"}  # .71 is already configured
    with patch(PROBE, AsyncMock(return_value=REST_OK())):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "10.0.0.70", CONF_PASSWORD: ""}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "10.0.0.70"
    assert result["data"][CONF_MAC] == "90:ac:3f:00:00:70"


async def test_user_without_players_found_can_type_address(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["description_placeholders"] == {"found": "0"}
    with patch(PROBE, AsyncMock(return_value=REST_OK())):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: " 10.0.0.72 ", CONF_PASSWORD: ""}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "10.0.0.72"
    assert result["data"][CONF_MAC] is None


async def test_dhcp_unknown_player_uses_serial_from_hostname(hass: HomeAssistant) -> None:
    info = DhcpServiceInfo(
        ip="10.0.0.62", hostname="brightsign-abc123456", macaddress="90ac3f000062"
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=info
    )
    assert result["step_id"] == "discovery_confirm"
    progress = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert progress[0]["context"]["unique_id"] == "ABC123456"


async def test_dhcp_known_player_by_serial_is_updated_and_remembered(hass: HomeAssistant) -> None:
    """A known player at a new address: updated, and the discovery is stored for rediscovery."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="ABC123456", data={CONF_HOST: "10.0.0.60", CONF_MAC: None}
    )
    entry.add_to_hass(hass)
    key = DiscoveryKey(domain="dhcp", key="90ac3f000061", version=1)
    info = DhcpServiceInfo(
        ip="10.0.0.61", hostname="brightsign-abc123456", macaddress="90ac3f000061"
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_DHCP, "discovery_key": key},
        data=info,
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "10.0.0.61"
    assert entry.data[CONF_MAC] == "90:ac:3f:00:00:61"
    assert key in entry.discovery_keys.get("dhcp", ())


async def test_dhcp_other_device_on_old_address_is_not_merged(hass: HomeAssistant) -> None:
    """A different player that got a known player's old address is not mixed up with it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="SERIAL1",
        data={CONF_HOST: "10.0.0.20", CONF_MAC: "90:ac:3f:04:95:aa"},
    )
    entry.add_to_hass(hass)
    info = DhcpServiceInfo(ip="10.0.0.20", hostname="brightsign-x", macaddress="90ac3f0495bb")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=info
    )
    assert result["type"] is FlowResultType.ABORT  # same address, unknown: no duplicate
    assert entry.data[CONF_MAC] == "90:ac:3f:04:95:aa"


async def test_zeroconf_known_player_updates_address(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="XAE3TEST0001", data={CONF_HOST: "10.0.0.40"})
    entry.add_to_hass(hass)
    info = ZeroconfServiceInfo(
        ip_address=ip_address("10.0.0.41"),
        ip_addresses=[ip_address("10.0.0.41")],
        hostname="brightsign-XAE3TEST0001.local.",
        name="BrightSign Web Service._http._tcp.local.",
        port=8080,
        type="_http._tcp.local.",
        properties={"serialnumber": "XAE3TEST0001", "unitname": "Lobby"},
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info
    )
    assert result["type"] is FlowResultType.ABORT
    assert entry.data[CONF_HOST] == "10.0.0.41"
