"""Passive listener for the PTP (IEEE 1588) traffic of BrightSign sync groups.

Players in a sync group share a PTP domain. The leader is the PTP master: it sends Sync,
Follow_Up, Announce and Delay_Resp messages. Followers send Delay_Req messages. Listening
to 224.0.1.129 (UDP 319 and 320) tells leaders and followers apart and shows their PTP
domain. Nothing is ever sent.

Hardening: only PTP version 2 headers from local addresses, at most 512 bytes, at most
256 sources, and no state is written per packet (the sync monitor reads the table every
few seconds). Binding the PTP ports can be refused (another PTP service, or Home
Assistant not running as root); the feature is then simply off.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time

from homeassistant.core import HomeAssistant, callback

from .api import is_local_address
from .const import DOMAIN, MAX_PTP_DATAGRAM, PTP_GROUP, PTP_PORTS
from .sync import open_multicast_socket

_LOGGER = logging.getLogger(__name__)
_SHARED_KEY = f"{DOMAIN}_ptp_listener"
_MAX_SOURCES = 256

# PTP version 2 message types
PTP_SYNC = 0x0
PTP_DELAY_REQ = 0x1
PTP_FOLLOW_UP = 0x8
PTP_DELAY_RESP = 0x9
PTP_ANNOUNCE = 0xB
MASTER_MESSAGES = frozenset({PTP_SYNC, PTP_FOLLOW_UP, PTP_DELAY_RESP, PTP_ANNOUNCE})


def parse_ptp_header(data: bytes) -> tuple[int, int] | None:
    """(message type, domain number) of a PTP version 2 message; None otherwise."""
    if not 34 <= len(data) <= MAX_PTP_DATAGRAM or data[1] & 0x0F != 2:
        return None
    return data[0] & 0x0F, data[4]


@dataclass
class PtpSource:
    """What one address sent recently."""

    domain: int
    master_seen: float | None = None
    follower_seen: float | None = None


class _PtpProtocol(asyncio.DatagramProtocol):
    """Receive-only protocol for one PTP port."""

    def __init__(self, sources: dict[str, PtpSource]) -> None:
        self._sources = sources

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        header = parse_ptp_header(data)
        if header is None or not is_local_address(addr[0]):
            return
        message_type, domain = header
        if message_type in MASTER_MESSAGES:
            master = True
        elif message_type == PTP_DELAY_REQ:
            master = False
        else:
            return
        source = self._sources.get(addr[0])
        if source is None:
            if len(self._sources) >= _MAX_SOURCES:
                return
            source = self._sources[addr[0]] = PtpSource(domain)
            _LOGGER.debug(
                "Receiving PTP traffic from %s (domain %d, %s)",
                addr[0],
                domain,
                "master" if master else "follower",
            )
        source.domain = domain
        if master:
            source.master_seen = time.monotonic()
        else:
            source.follower_seen = time.monotonic()


class PtpListener:
    """One shared PTP listener for all configured players."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._transports: list[asyncio.BaseTransport] = []
        self._users = 0
        self.sources: dict[str, PtpSource] = {}

    async def async_start(self) -> None:
        """Join the PTP multicast group on both PTP ports (once, shared by all entries)."""
        self._users += 1
        if self._users > 1:
            return
        for port in PTP_PORTS:
            try:
                sock = open_multicast_socket(PTP_GROUP, port)
            except OSError as err:
                _LOGGER.debug("Cannot listen for PTP traffic on %s:%s: %s", PTP_GROUP, port, err)
                continue
            transport, _ = await self._hass.loop.create_datagram_endpoint(
                lambda: _PtpProtocol(self.sources), sock=sock
            )
            self._transports.append(transport)
        if self._transports:
            _LOGGER.debug("Listening for PTP traffic on %s", PTP_GROUP)

    @callback
    def async_stop(self) -> None:
        """Leave the group when the last entry unloads."""
        self._users = max(0, self._users - 1)
        if self._users == 0:
            for transport in self._transports:
                transport.close()
            self._transports.clear()


async def async_acquire_ptp_listener(hass: HomeAssistant) -> PtpListener:
    """Start (or reuse) the shared PTP listener."""
    listener: PtpListener | None = hass.data.get(_SHARED_KEY)
    if listener is None:
        listener = PtpListener(hass)
        hass.data[_SHARED_KEY] = listener
    await listener.async_start()
    return listener
