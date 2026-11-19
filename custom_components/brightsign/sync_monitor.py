"""Sync roles, sync groups and sync conflicts of the BrightSign players on the network.

Combines the two passive listeners every few seconds:

- roSyncManager traffic (sync.py): every sender is a sync leader; the domain in its
  messages is the sync group (not readable when the traffic is encrypted).
- PTP traffic (ptp.py): the PTP master of a group is its leader, followers send delay
  requests; the PTP domain is shared by all players of a group. Other devices can use PTP
  too (for example audio networks), so only configured players are taken from it.
- Ethernet sync scripts (sync.py): the master broadcasts short commands to UDP 11167 when
  its video (re)starts. Clients do not send anything, so only the master is seen.

Two leaders in the same group is a conflict: after it lasts 30 seconds it is shown on the
players involved and as a repair issue, with the addresses of all leaders (also those
that are not configured in Home Assistant).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging
import re
import time

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DOMAIN,
    SIGNAL_SYNC_ROLE,
    SYNC_CHECK_INTERVAL,
    SYNC_CONFLICT_CHECKS,
    SYNC_ROLE_FOLLOWER,
    SYNC_ROLE_LEADER,
    SYNC_ROLE_NONE,
    SYNC_TIMEOUT,
    UDP_SYNC_PORT,
    UDP_SYNC_TIMEOUT,
)
from .ptp import PtpListener
from .sync import SyncListener, UdpSyncListener

_LOGGER = logging.getLogger(__name__)
_SHARED_KEY = f"{DOMAIN}_sync_monitor"


@dataclass(frozen=True)
class SyncView:
    """Sync situation of one player."""

    role: str = SYNC_ROLE_NONE
    sync_group: str | None = None  # roSyncManager domain (leaders, unencrypted traffic)
    ptp_domain: int | None = None
    conflict: bool = False
    other_leaders: tuple[str, ...] = ()


def _issue_id(kind: str, group: str) -> str:
    return f"sync_conflict_{kind}_{re.sub(r'[^a-z0-9]+', '_', group.lower())[:32]}"


class SyncMonitor:
    """Shared by all entries: works out roles and conflicts, one update per change."""

    def __init__(
        self,
        hass: HomeAssistant,
        sync: SyncListener,
        ptp: PtpListener,
        udp_sync: UdpSyncListener,
    ) -> None:
        self._hass = hass
        self._sync = sync
        self._ptp = ptp
        self._udp_sync = udp_sync
        self._players: dict[str, str] = {}  # configured host -> player name
        self._views: dict[str, SyncView] = {}
        self._seen: set[str] = set()
        self._first_seen: dict[str, list[Callable[[], None]]] = {}
        self._conflict_checks: dict[tuple[str, str], int] = {}
        self._unsub: CALLBACK_TYPE | None = None

    # ----- players -----------------------------------------------------------

    @callback
    def async_register(self, host: str, name: str) -> CALLBACK_TYPE:
        """Track a configured player; returns the callback that stops tracking it."""
        self._players[host] = name
        if self._unsub is None:
            self._unsub = async_track_time_interval(
                self._hass, self.async_check, SYNC_CHECK_INTERVAL
            )

        @callback
        def unregister() -> None:
            self._players.pop(host, None)
            self._views.pop(host, None)
            self._seen.discard(host)
            self._first_seen.pop(host, None)
            if not self._players and self._unsub is not None:
                self._unsub()
                self._unsub = None
                for kind, group in list(self._conflict_checks):
                    ir.async_delete_issue(self._hass, DOMAIN, _issue_id(kind, group))
                self._conflict_checks.clear()

        return unregister

    def view(self, host: str) -> SyncView:
        """Current sync situation of a configured player."""
        return self._views.get(host, SyncView())

    def seen(self, host: str) -> bool:
        """True once the player took part in a sync group (leader or follower)."""
        return host in self._seen

    @callback
    def async_on_first_seen(self, host: str, action: Callable[[], None]) -> None:
        """Run ``action`` once, when the player first shows up in a sync group."""
        self._first_seen.setdefault(host, []).append(action)

    # ----- periodic check ------------------------------------------------------

    @callback
    def async_check(self, _now: datetime | None = None) -> None:
        """Work out roles, groups and conflicts from the latest traffic."""
        now = time.monotonic()

        # roSyncManager: every recent sender is a leader of its domain
        sync_active: dict[str, str | None] = {}
        sync_groups: dict[str, list[str]] = {}
        for address, (last_seen, domain) in list(self._sync.sources.items()):
            if now - last_seen < SYNC_TIMEOUT:
                sync_active[address] = domain
                if domain:
                    sync_groups.setdefault(domain, []).append(address)

        # PTP: masters and followers among the configured players
        ptp_roles: dict[str, tuple[str, int]] = {}
        ptp_groups: dict[str, list[str]] = {}
        for address, source in list(self._ptp.sources.items()):
            if address not in self._players:
                continue
            master = source.master_seen is not None and now - source.master_seen < SYNC_TIMEOUT
            follower = (
                source.follower_seen is not None and now - source.follower_seen < SYNC_TIMEOUT
            )
            if master:
                ptp_roles[address] = (SYNC_ROLE_LEADER, source.domain)
                ptp_groups.setdefault(str(source.domain), []).append(address)
            elif follower:
                ptp_roles[address] = (SYNC_ROLE_FOLLOWER, source.domain)

        # Ethernet sync scripts: every recent sender is a master (one group per port)
        udp_masters = [
            address
            for address, last_seen in list(self._udp_sync.sources.items())
            if now - last_seen < UDP_SYNC_TIMEOUT
        ]
        udp_group = f"UDP {UDP_SYNC_PORT}"

        conflicts = self._async_update_conflicts(
            {"sync": sync_groups, "ptp": ptp_groups, "udp": {str(UDP_SYNC_PORT): udp_masters}}
        )

        for host in list(self._players):
            udp_master = host in udp_masters
            leader = host in sync_active or udp_master
            role = SYNC_ROLE_LEADER if leader else SYNC_ROLE_NONE
            ptp_domain = None
            if host in ptp_roles:
                ptp_role, ptp_domain = ptp_roles[host]
                if role == SYNC_ROLE_NONE:
                    role = ptp_role
            leaders = conflicts.get(host, ())
            view = SyncView(
                role=role,
                sync_group=sync_active.get(host) or (udp_group if udp_master else None),
                ptp_domain=ptp_domain,
                conflict=bool(leaders),
                other_leaders=tuple(address for address in leaders if address != host),
            )
            changed = view != self._views.get(host)
            self._views[host] = view
            if role != SYNC_ROLE_NONE and host not in self._seen:
                self._seen.add(host)
                for action in self._first_seen.pop(host, []):
                    action()
            if changed:
                async_dispatcher_send(self._hass, SIGNAL_SYNC_ROLE.format(host))

    def _async_update_conflicts(
        self, groups_by_kind: dict[str, dict[str, list[str]]]
    ) -> dict[str, tuple[str, ...]]:
        """Debounced conflicts: address -> all leaders of its group (when 2 or more)."""
        conflicts: dict[str, tuple[str, ...]] = {}
        current: set[tuple[str, str]] = set()
        for kind, groups in groups_by_kind.items():
            for group, leaders in groups.items():
                if len(leaders) < 2:
                    continue
                key = (kind, group)
                current.add(key)
                checks = self._conflict_checks.get(key, 0) + 1
                self._conflict_checks[key] = checks
                if checks < SYNC_CONFLICT_CHECKS:
                    continue
                ordered = tuple(sorted(leaders))
                for address in ordered:
                    conflicts[address] = ordered
                if checks == SYNC_CONFLICT_CHECKS:
                    self._async_raise_issue(kind, group, ordered)
        for key in [key for key in self._conflict_checks if key not in current]:
            if self._conflict_checks.pop(key) >= SYNC_CONFLICT_CHECKS:
                _LOGGER.info("Sync conflict in group %s is over", key[1])
            ir.async_delete_issue(self._hass, DOMAIN, _issue_id(*key))
        return conflicts

    def _async_raise_issue(self, kind: str, group: str, leaders: tuple[str, ...]) -> None:
        names = ", ".join(
            f"{self._players[address]} ({address})" if address in self._players else address
            for address in leaders
        )
        label = {"sync": group, "ptp": f"PTP {group}", "udp": f"UDP {group}"}[kind]
        _LOGGER.warning("Sync group %s has more than one leader: %s", label, names)
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            _issue_id(kind, group),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="sync_conflict",
            translation_placeholders={"group": label, "leaders": names},
        )


def async_get_monitor(
    hass: HomeAssistant, sync: SyncListener, ptp: PtpListener, udp_sync: UdpSyncListener
) -> SyncMonitor:
    """The shared sync monitor."""
    monitor: SyncMonitor | None = hass.data.get(_SHARED_KEY)
    if monitor is None:
        monitor = SyncMonitor(hass, sync, ptp, udp_sync)
        hass.data[_SHARED_KEY] = monitor
    return monitor
