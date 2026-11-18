"""Passive listener for BrightSign synchronization (roSyncManager) traffic.

A sync leader multicasts small JSON messages to 224.0.126.10:1539, e.g.
``{"d": "1", "p": "1\\n1\\n20261118T155417.834393", "v": 1}`` (current sync item and the
player time it started) and ``{"d": "vsync:1", ...}`` (heartbeat). Messages are only
read; nothing is ever sent to the group. Encrypted sync traffic is counted as
activity but not decoded.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
import json
import logging
import socket
import struct
import time

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_SYNC, SYNC_GROUP, SYNC_PORT, SYNC_TIMEOUT

_LOGGER = logging.getLogger(__name__)
_SHARED_KEY = f"{DOMAIN}_sync_listener"


class _SyncProtocol(asyncio.DatagramProtocol):
    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        message: dict | None = None
        try:
            decoded = json.loads(data.decode("utf-8"))
            if isinstance(decoded, dict):
                message = decoded
        except (UnicodeDecodeError, ValueError):
            message = None  # encrypted or non-JSON sync traffic: activity only
        async_dispatcher_send(self._hass, SIGNAL_SYNC.format(addr[0]), message)


class SyncListener:
    """One shared multicast listener for all configured players."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._transport: asyncio.DatagramTransport | None = None
        self._users = 0

    async def async_start(self) -> None:
        self._users += 1
        if self._transport is not None:
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.bind(("", SYNC_PORT))
            mreq = struct.pack("=4s4s", socket.inet_aton(SYNC_GROUP), socket.inet_aton("0.0.0.0"))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.setblocking(False)
        except OSError as err:
            _LOGGER.warning(
                "Cannot listen for BrightSign sync traffic on %s:%s: %s", SYNC_GROUP, SYNC_PORT, err
            )
            return
        self._transport, _ = await self._hass.loop.create_datagram_endpoint(
            lambda: _SyncProtocol(self._hass), sock=sock
        )

    @callback
    def async_stop(self) -> None:
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


def _player_time(payload: str) -> datetime | None:
    """Parse the player timestamp at the end of the 'p' field."""
    last = payload.strip().splitlines()[-1] if payload.strip() else ""
    for fmt in ("%Y%m%dT%H%M%S.%f", "%Y%m%dT%H%M%S"):
        try:
            return datetime.strptime(last, fmt)
        except ValueError:
            continue
    return None


class SyncTracker:
    """Sync state for one player IP (filled only when that player is a sync leader)."""

    def __init__(self, hass: HomeAssistant, host: str) -> None:
        self._hass = hass
        self._host = host
        self._unsub: CALLBACK_TYPE | None = None
        self._listeners: list[Callable[[], None]] = []
        self._last_seen: float | None = None
        self._last_key: tuple[str, str] | None = None
        self._last_player_time: datetime | None = None
        self.item: str | None = None
        self.item_started: datetime | None = None
        self.last_item_duration: float | None = None
        self.encrypted = False

    @callback
    def async_start(self) -> None:
        self._unsub = async_dispatcher_connect(
            self._hass, SIGNAL_SYNC.format(self._host), self._handle
        )

    @callback
    def async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @property
    def active(self) -> bool:
        """True while this player sent sync traffic recently."""
        return self._last_seen is not None and time.monotonic() - self._last_seen < SYNC_TIMEOUT

    @property
    def seen(self) -> bool:
        """True once any sync traffic from this player was received."""
        return self._last_seen is not None

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(update)

        @callback
        def remove() -> None:
            if update in self._listeners:
                self._listeners.remove(update)

        return remove

    @callback
    def _handle(self, message: dict | None) -> None:
        self._last_seen = time.monotonic()
        if message is None:
            self.encrypted = True
        else:
            item = str(message.get("d", ""))
            if item and not item.startswith("vsync"):
                payload = str(message.get("p", ""))
                stamp = payload.strip().splitlines()[-1] if payload.strip() else ""
                key = (item, stamp)
                if key != self._last_key:
                    player_time = _player_time(payload)
                    if self._last_key is not None:
                        self.item_started = dt_util.utcnow()
                        if player_time and self._last_player_time:
                            self.last_item_duration = round(
                                (player_time - self._last_player_time).total_seconds(), 1
                            )
                    self._last_key = key
                    self._last_player_time = player_time
                    self.item = item
        for update in list(self._listeners):
            update()
