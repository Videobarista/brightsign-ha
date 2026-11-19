"""Sync roles, groups and conflict detection."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from custom_components.brightsign.const import (
    DOMAIN,
    SYNC_CONFLICT_CHECKS,
    SYNC_ROLE_FOLLOWER,
    SYNC_ROLE_LEADER,
    SYNC_ROLE_NONE,
)
from custom_components.brightsign.ptp import PtpListener, PtpSource
from custom_components.brightsign.sync import SyncListener, UdpSyncListener
from custom_components.brightsign.sync_monitor import SyncMonitor
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

LEADER_A = "10.0.0.5"
FOLLOWER = "10.0.0.6"
LEADER_B = "10.0.0.7"


async def test_roles_groups_and_conflict(hass: HomeAssistant) -> None:
    sync = SyncListener(hass)
    ptp = PtpListener(hass)
    udp_sync = UdpSyncListener(hass)
    monitor = SyncMonitor(hass, sync, ptp, udp_sync)
    unregister = [
        monitor.async_register(LEADER_A, "Wall-leader"),
        monitor.async_register(FOLLOWER, "Wall-follower1"),
    ]
    first_seen = MagicMock()
    monitor.async_on_first_seen(FOLLOWER, first_seen)

    now = time.monotonic()
    sync.sources[LEADER_A] = (now, "1")
    ptp.sources[LEADER_A] = PtpSource(domain=1, master_seen=now)
    ptp.sources[FOLLOWER] = PtpSource(domain=1, follower_seen=now)
    monitor.async_check()

    assert monitor.view(LEADER_A).role == SYNC_ROLE_LEADER
    assert monitor.view(LEADER_A).sync_group == "1"
    assert monitor.view(LEADER_A).ptp_domain == 1
    assert monitor.view(FOLLOWER).role == SYNC_ROLE_FOLLOWER
    assert monitor.view(FOLLOWER).ptp_domain == 1
    assert not monitor.view(LEADER_A).conflict
    first_seen.assert_called_once()
    assert monitor.seen(FOLLOWER)

    # A second leader in the same group (not configured in Home Assistant)
    issues = ir.async_get(hass)
    for check in range(SYNC_CONFLICT_CHECKS):
        sync.sources[LEADER_B] = (time.monotonic(), "1")
        sync.sources[LEADER_A] = (time.monotonic(), "1")
        monitor.async_check()
        if check < SYNC_CONFLICT_CHECKS - 1:
            assert not monitor.view(LEADER_A).conflict  # debounced
    assert monitor.view(LEADER_A).conflict
    assert monitor.view(LEADER_A).other_leaders == (LEADER_B,)
    issue = issues.async_get_issue(DOMAIN, "sync_conflict_sync_1")
    assert issue is not None
    assert "Wall-leader (10.0.0.5)" in issue.translation_placeholders["leaders"]

    # The other leader stops: the conflict and the issue disappear
    del sync.sources[LEADER_B]
    monitor.async_check()
    assert not monitor.view(LEADER_A).conflict
    assert issues.async_get_issue(DOMAIN, "sync_conflict_sync_1") is None

    # No traffic any more: no role
    sync.sources.clear()
    ptp.sources.clear()
    monitor.async_check()
    assert monitor.view(LEADER_A).role == SYNC_ROLE_NONE

    for stop in unregister:
        stop()


async def test_ptp_from_other_devices_is_ignored(hass: HomeAssistant) -> None:
    """PTP masters that are not configured players (e.g. audio networks) never conflict."""
    sync = SyncListener(hass)
    ptp = PtpListener(hass)
    udp_sync = UdpSyncListener(hass)
    monitor = SyncMonitor(hass, sync, ptp, udp_sync)
    stop = monitor.async_register(LEADER_A, "Player")
    now = time.monotonic()
    ptp.sources[LEADER_A] = PtpSource(domain=0, master_seen=now)
    ptp.sources["10.0.0.99"] = PtpSource(domain=0, master_seen=now)  # audio grandmaster
    for _ in range(SYNC_CONFLICT_CHECKS + 1):
        ptp.sources[LEADER_A].master_seen = time.monotonic()
        ptp.sources["10.0.0.99"].master_seen = time.monotonic()
        monitor.async_check()
    assert monitor.view(LEADER_A).role == SYNC_ROLE_LEADER
    assert not monitor.view(LEADER_A).conflict
    stop()


async def test_ethernet_sync_masters(hass: HomeAssistant) -> None:
    """Masters of Ethernet sync scripts are seen by their broadcasts; two masters conflict."""
    sync = SyncListener(hass)
    ptp = PtpListener(hass)
    udp_sync = UdpSyncListener(hass)
    monitor = SyncMonitor(hass, sync, ptp, udp_sync)
    stop = monitor.async_register(LEADER_A, "Wall-leader")
    udp_sync.sources[LEADER_A] = time.monotonic()
    monitor.async_check()
    assert monitor.view(LEADER_A).role == SYNC_ROLE_LEADER
    assert monitor.view(LEADER_A).sync_group == "UDP 11167"
    udp_sync.sources["10.0.0.20"] = time.monotonic()  # a second master on the network
    for _ in range(SYNC_CONFLICT_CHECKS):
        monitor.async_check()
    assert monitor.view(LEADER_A).conflict
    assert ir.async_get(hass).async_get_issue(DOMAIN, "sync_conflict_udp_11167") is not None
    stop()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "sync_conflict_udp_11167") is None
