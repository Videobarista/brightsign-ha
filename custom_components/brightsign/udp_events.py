"""Receive-only listener for UDP messages that presentations send (e.g. on state changes).

BrightAuthor presentations can send UDP messages to a destination address and port
(Presentation settings > Interactive > Networking). Messages from configured players are
passed to their entities; anything else is ignored.

Hardening: only datagrams from addresses of configured players are accepted, at most
1 KiB, at most 20 per second per player, and text is sanitized and bounded.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import socket
import time

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.util import dt as dt_util

from .api import is_local_address, sanitize_text
from .const import DOMAIN, MAX_UDP_EVENT_DATAGRAM, MAX_UDP_EVENTS_PER_SECOND, SIGNAL_UDP_EVENT

_LOGGER = logging.getLogger(__name__)
_SHARED_KEY = f"{DOMAIN}_udp_event_listeners"


def decode_udp_message(data: bytes) -> str:
    """Printable text of a datagram (bounded)."""
    return sanitize_text(data.decode("utf-8", errors="replace"), 255)


class _UdpEventProtocol(asyncio.DatagramProtocol):
    """Receive-only protocol; dispatches per source address."""

    def __init__(self, hass: HomeAssistant, listener: UdpEventListener) -> None:
        self._hass = hass
        self._listener = listener
        self._window: dict[str, tuple[float, int]] = {}

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        source = addr[0]
        if (
            len(data) > MAX_UDP_EVENT_DATAGRAM
            or not is_local_address(source)
            or source not in self._listener.hosts
        ):
            return
        now = time.monotonic()
        start, count = self._window.get(source, (now, 0))
        if now - start >= 1:
            start, count = now, 0
        if count >= MAX_UDP_EVENTS_PER_SECOND:
            return  # flooding: drop until the next second
        self._window[source] = (start, count + 1)
        message = decode_udp_message(data)
        if message:
            async_dispatcher_send(self._hass, SIGNAL_UDP_EVENT.format(source), message)


class UdpEventListener:
    """One listener per UDP port, shared by the players that send to it."""

    def __init__(self, hass: HomeAssistant, port: int) -> None:
        self._hass = hass
        self.port = port
        self.hosts: set[str] = set()
        self._transport: asyncio.BaseTransport | None = None

    async def async_start(self) -> None:
        if self._transport is not None:
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.bind(("", self.port))
            sock.setblocking(False)
        except OSError as err:
            _LOGGER.warning(
                "Cannot listen for BrightSign UDP messages on port %s: %s", self.port, err
            )
            return
        self._transport, _ = await self._hass.loop.create_datagram_endpoint(
            lambda: _UdpEventProtocol(self._hass, self), sock=sock
        )
        _LOGGER.debug("Listening for BrightSign UDP messages on port %s", self.port)

    @callback
    def async_stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None


async def async_register_player(hass: HomeAssistant, port: int, host: str) -> CALLBACK_TYPE:
    """Listen on ``port`` for messages from ``host``; returns an unregister callback."""
    listeners: dict[int, UdpEventListener] = hass.data.setdefault(_SHARED_KEY, {})
    listener = listeners.get(port)
    if listener is None:
        listener = listeners[port] = UdpEventListener(hass, port)
    listener.hosts.add(host)
    await listener.async_start()

    @callback
    def unregister() -> None:
        listener.hosts.discard(host)
        if not listener.hosts:
            listener.async_stop()
            listeners.pop(port, None)

    return unregister


class UdpEventTracker:
    """UDP messages from one player."""

    def __init__(self, hass: HomeAssistant, host: str) -> None:
        self._hass = hass
        self._host = host
        self._unsub: CALLBACK_TYPE | None = None
        self._listeners: list[Callable[[str], None]] = []
        self._first_seen: list[Callable[[], None]] = []
        self.last_message: str | None = None
        self.last_received = None

    @callback
    def async_start(self) -> None:
        self._unsub = async_dispatcher_connect(
            self._hass, SIGNAL_UDP_EVENT.format(self._host), self.async_handle
        )

    @callback
    def async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._first_seen.clear()

    @property
    def seen(self) -> bool:
        return self.last_message is not None

    @callback
    def async_on_first_seen(self, action: Callable[[], None]) -> None:
        self._first_seen.append(action)

    @callback
    def async_add_listener(self, update: Callable[[str], None]) -> CALLBACK_TYPE:
        self._listeners.append(update)

        @callback
        def remove() -> None:
            if update in self._listeners:
                self._listeners.remove(update)

        return remove

    @callback
    def async_handle(self, message: str) -> None:
        first = self.last_message is None
        self.last_message = message
        self.last_received = dt_util.utcnow()
        if first:
            actions, self._first_seen = self._first_seen, []
            for action in actions:
                action()
        for update in list(self._listeners):
            update(message)
