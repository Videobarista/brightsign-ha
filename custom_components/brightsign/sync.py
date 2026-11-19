"""Passive listener for BrightSign synchronization (roSyncManager) traffic.

A sync leader multicasts small JSON messages to 224.0.126.10:1539, e.g.
``{"d": "1", "p": "1\\n1\\n20261118T155417.834393", "v": 1}`` (sync domain "1", the sync
item and the player time it started) and ``{"d": "vsync:1", ...}`` (heartbeat of domain
"1"). The domain identifies the sync group. Messages are only received; nothing is ever
sent to the group. Encrypted sync traffic (BrightSignOS 8+) is counted as activity but not
decoded.

Hardening: datagrams above 2 KiB and datagrams from non-local addresses are ignored,
text is bounded, and entity states are only written when something changes (the
heartbeat arrives every second).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
import json
import logging
import re
import socket
import struct
import time

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.util import dt as dt_util

from .api import is_local_address, sanitize_text
from .const import (
    DOMAIN,
    MAX_SYNC_DATAGRAM,
    MAX_UDP_SYNC_DATAGRAM,
    SIGNAL_SYNC,
    SYNC_GROUP,
    SYNC_PORT,
    SYNC_TIMEOUT,
    UDP_SYNC_PORT,
)

_LOGGER = logging.getLogger(__name__)
_SHARED_KEY = f"{DOMAIN}_sync_listener"
_UDP_SYNC_KEY = f"{DOMAIN}_udp_sync_listener"
# A sync command: three lower case letters, optionally followed by a parameter
_UDP_SYNC_COMMAND = re.compile(r"[a-z]{3}(?:[^a-z][\x20-\x7e]*)?")


def open_multicast_socket(group: str, port: int) -> socket.socket:
    """Non-blocking UDP socket that receives the given multicast group (receive-only)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(("", port))
        mreq = struct.pack("=4sl", socket.inet_aton(group), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.setblocking(False)
    except OSError:
        sock.close()
        raise
    return sock


def is_encrypted(value: object) -> bool:
    """True for an encrypted roSyncManager field, e.g. ``{"e": "<base64>"}`` (BrightSignOS 8+)."""
    text = str(value).strip()
    return text.startswith("{") and '"e"' in text


def sync_domain(message: dict | None) -> str | None:
    """Sync group (roSyncManager domain) of a decoded message; None when unknown."""
    if not message:
        return None
    raw = message.get("d", "")
    if is_encrypted(raw):
        return None
    domain = sanitize_text(raw, 64)
    if domain.startswith("vsync:"):
        domain = domain[len("vsync:") :]
    return domain or None


def decode_sync_message(data: bytes) -> dict | None:
    """Decode one sync datagram; None for encrypted or non-JSON traffic."""
    try:
        decoded = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return decoded if isinstance(decoded, dict) else None


class _SyncProtocol(asyncio.DatagramProtocol):
    """Receive-only protocol for the sync multicast group."""

    def __init__(
        self, hass: HomeAssistant, sources: dict[str, tuple[float, str | None]] | None = None
    ) -> None:
        self._hass = hass
        # Every sync leader on the network: address -> (last seen, sync domain)
        self._sources = sources if sources is not None else {}

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) > MAX_SYNC_DATAGRAM or not is_local_address(addr[0]):
            return
        message = decode_sync_message(data)
        if addr[0] not in self._sources:
            if len(self._sources) >= 256:
                return
            _LOGGER.debug(
                "Receiving BrightSign sync traffic from %s (domain %s)",
                addr[0],
                sync_domain(message) or "unknown",
            )
        self._sources[addr[0]] = (time.monotonic(), sync_domain(message))
        async_dispatcher_send(self._hass, SIGNAL_SYNC.format(addr[0]), message)


class SyncListener:
    """One shared multicast listener for all configured players."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._transport: asyncio.BaseTransport | None = None
        self._users = 0
        self.sources: dict[str, tuple[float, str | None]] = {}

    async def async_start(self) -> None:
        """Join the sync multicast group (once, shared by all entries)."""
        self._users += 1
        if self._transport is not None:
            return
        try:
            sock = open_multicast_socket(SYNC_GROUP, SYNC_PORT)
        except OSError as err:
            _LOGGER.warning(
                "Cannot listen for BrightSign sync traffic on %s:%s: %s", SYNC_GROUP, SYNC_PORT, err
            )
            return
        self._transport, _ = await self._hass.loop.create_datagram_endpoint(
            lambda: _SyncProtocol(self._hass, self.sources), sock=sock
        )
        _LOGGER.debug("Listening for BrightSign sync traffic on %s:%s", SYNC_GROUP, SYNC_PORT)

    @callback
    def async_stop(self) -> None:
        """Leave the group when the last entry unloads."""
        self._users = max(0, self._users - 1)
        if self._users == 0 and self._transport is not None:
            self._transport.close()
            self._transport = None


async def async_acquire_listener(hass: HomeAssistant) -> SyncListener:
    """Start (or reuse) the shared sync listener."""
    listener: SyncListener | None = hass.data.get(_SHARED_KEY)
    if listener is None:
        listener = SyncListener(hass)
        hass.data[_SHARED_KEY] = listener
    await listener.async_start()
    return listener


def player_time(payload: str) -> datetime | None:
    """Parse the player timestamp at the end of the 'p' field."""
    lines = payload.strip().splitlines()
    last = lines[-1][:32] if lines else ""
    for fmt in ("%Y%m%dT%H%M%S.%f", "%Y%m%dT%H%M%S"):
        try:
            return datetime.strptime(last, fmt)  # noqa: DTZ007 - player-local, only diffs used
        except ValueError:
            continue
    return None


class _UdpSyncProtocol(asyncio.DatagramProtocol):
    """Receive-only: masters of Ethernet sync scripts broadcasting their commands."""

    def __init__(self, sources: dict[str, float]) -> None:
        self._sources = sources

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) > MAX_UDP_SYNC_DATAGRAM or not is_local_address(addr[0]):
            return
        text = data.decode("ascii", errors="replace").strip()
        if not _UDP_SYNC_COMMAND.fullmatch(text):
            return
        if addr[0] not in self._sources:
            if len(self._sources) >= 256:
                return
            _LOGGER.debug("Receiving Ethernet sync commands from %s", addr[0])
        self._sources[addr[0]] = time.monotonic()


class UdpSyncListener:
    """One shared listener for the broadcasts of Ethernet sync scripts (UDP 11167)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._transport: asyncio.BaseTransport | None = None
        self._users = 0
        self.sources: dict[str, float] = {}  # master address -> last seen

    async def async_start(self) -> None:
        """Listen (once, shared by all entries)."""
        self._users += 1
        if self._transport is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.bind(("", UDP_SYNC_PORT))
            sock.setblocking(False)
        except OSError as err:
            sock.close()
            _LOGGER.debug("Cannot listen for Ethernet sync commands on %s: %s", UDP_SYNC_PORT, err)
            return
        self._transport, _ = await self._hass.loop.create_datagram_endpoint(
            lambda: _UdpSyncProtocol(self.sources), sock=sock
        )

    @callback
    def async_stop(self) -> None:
        """Stop when the last entry unloads."""
        self._users = max(0, self._users - 1)
        if self._users == 0 and self._transport is not None:
            self._transport.close()
            self._transport = None


async def async_acquire_udp_sync_listener(hass: HomeAssistant) -> UdpSyncListener:
    """Start (or reuse) the shared Ethernet sync listener."""
    listener: UdpSyncListener | None = hass.data.get(_UDP_SYNC_KEY)
    if listener is None:
        listener = UdpSyncListener(hass)
        hass.data[_UDP_SYNC_KEY] = listener
    await listener.async_start()
    return listener


class SyncTracker:
    """Sync state for one player IP (filled only when that player is a sync leader)."""

    def __init__(self, hass: HomeAssistant, host: str) -> None:
        self._hass = hass
        self._host = host
        self._unsub: CALLBACK_TYPE | None = None
        self._listeners: list[Callable[[], None]] = []
        self._first_seen: list[Callable[[], None]] = []
        self._last_seen: float | None = None
        self._last_key: tuple[str, str] | None = None
        self._last_player_time: datetime | None = None
        self.item: str | None = None
        self.domain: str | None = None
        self.item_started: datetime | None = None
        self.last_item_duration: float | None = None
        self.encrypted = False

    @callback
    def async_start(self) -> None:
        """Listen for sync traffic from this player's address."""
        self._unsub = async_dispatcher_connect(
            self._hass, SIGNAL_SYNC.format(self._host), self.async_handle
        )

    @callback
    def async_stop(self) -> None:
        """Stop listening."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._first_seen.clear()

    @property
    def seen(self) -> bool:
        """True once sync traffic from this player was received (it is a sync leader)."""
        return self._last_seen is not None

    @callback
    def async_on_first_seen(self, action: Callable[[], None]) -> None:
        """Run ``action`` once, when the first sync traffic from this player arrives."""
        self._first_seen.append(action)

    @property
    def active(self) -> bool:
        """True while this player sent sync traffic recently."""
        return self._last_seen is not None and time.monotonic() - self._last_seen < SYNC_TIMEOUT

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> CALLBACK_TYPE:
        """Register an entity update callback."""
        self._listeners.append(update)

        @callback
        def remove() -> None:
            if update in self._listeners:
                self._listeners.remove(update)

        return remove

    def _snapshot(self) -> tuple:
        return (self.item, self.domain, self.item_started, self.last_item_duration, self.encrypted)

    @callback
    def async_handle(self, message: dict | None) -> None:
        """Process one decoded datagram from this player."""
        was_active = self.active
        before = self._snapshot()
        first = self._last_seen is None
        self._last_seen = time.monotonic()
        payload = str(message.get("p", ""))[:256] if message else ""
        if (
            message is None
            or "e" in message
            or is_encrypted(message.get("d", ""))
            or is_encrypted(payload)
        ):
            # Encrypted traffic: the player is active, but item and timing cannot be read
            self.encrypted = True
            self.domain = sync_domain(message) or self.domain
        else:
            self.domain = sync_domain(message) or self.domain
            raw = sanitize_text(message.get("d", ""), 64)
            if raw and not raw.startswith("vsync"):
                lines = payload.strip().splitlines()
                # The sync item is the line before the player time stamp
                names = lines[:-1] if len(lines) > 1 and player_time(payload) else lines
                item = sanitize_text(names[-1]) if names else raw
                key = (raw, lines[-1][:32] if lines else "")
                if key != self._last_key:
                    stamp = player_time(payload)
                    if self._last_key is not None:
                        self.item_started = dt_util.utcnow()
                        if stamp and self._last_player_time:
                            duration = (stamp - self._last_player_time).total_seconds()
                            self.last_item_duration = (
                                round(duration, 1) if 0 <= duration < 7 * 86400 else None
                            )
                    self._last_key = key
                    self._last_player_time = stamp
                    self.item = item
        if first:
            actions, self._first_seen = self._first_seen, []
            for action in actions:
                action()
        if was_active and self._snapshot() == before:
            return  # heartbeat only: nothing to write
        for update in list(self._listeners):
            update()
