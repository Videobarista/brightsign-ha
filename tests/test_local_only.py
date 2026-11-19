"""The integration only talks to the configured player.

The runtime half of the "Local only" badge: every HTTP request, TCP connection and UDP
datagram the client makes during probing, polling and commands goes to the player's
address, the send_udp action only accepts local addresses, and the sync listener ignores
traffic from outside the local network. The static half is scripts/check_local_only.py,
which is tested here as well (including against a deliberately leaky copy).
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import shutil
from typing import Any
from unittest.mock import MagicMock, patch

import aiohttp
import pytest
import voluptuous as vol
from yarl import URL

from custom_components.brightsign import _local_ip
from custom_components.brightsign.api import BrightSignClient
from custom_components.brightsign.ptp import _PtpProtocol, parse_ptp_header
from custom_components.brightsign.sync import _SyncProtocol, _UdpSyncProtocol
from custom_components.brightsign.udp_events import UdpEventListener, _UdpEventProtocol

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "brightsign"
HOST = "192.0.2.10"


def _load_checker() -> Any:
    spec = importlib.util.spec_from_file_location(
        "check_local_only", ROOT / "scripts" / "check_local_only.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingSession:
    """Fake aiohttp session: records every URL, then fails like an unreachable player."""

    def __init__(self) -> None:
        self.urls: list[URL] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.urls.append(URL(url))
        raise aiohttp.ClientConnectionError("unreachable")

    def get(self, url: str, **kwargs: Any) -> Any:
        self.urls.append(URL(url))
        raise aiohttp.ClientConnectionError("unreachable")

    def post(self, url: str, **kwargs: Any) -> Any:
        self.urls.append(URL(url))
        raise aiohttp.ClientConnectionError("unreachable")


async def test_all_traffic_goes_to_the_player() -> None:
    """Probe, polling, commands and UDP only reach the configured player."""
    session = RecordingSession()
    tcp_hosts: list[str] = []

    async def _record_tcp(host: str, port: int, **kwargs: Any) -> Any:
        tcp_hosts.append(host)
        raise OSError("unreachable")

    client = BrightSignClient(session, HOST, "pw", no_verify_context=MagicMock())  # type: ignore[arg-type]
    with patch("custom_components.brightsign.api.asyncio.open_connection", _record_tcp):
        await client.async_probe()
        client.rest_scheme = "https"
        for call in (
            client.async_get_info(),
            client.async_get_video_output(),
            client.async_reboot(),
            client.async_set_hdmi_output(True),
            client.async_send_cec("4004"),
            client.async_get_lws(),
            client.async_get_legacy_info(),
            client.async_snapshot(),
            client.async_get_video_modes(),
            client.async_set_video_mode("1920x1080x60p"),
            client.async_get_edid(),
            client.async_get_storage(),
            client.async_send_custom("next"),
            client.async_set_user_variable("Title", "Hello"),
            client.async_get_unit_name(),
        ):
            with pytest.raises(Exception):  # noqa: B017 - every call fails offline
                await call
        await client.async_fetch_fingerprint()
        await client.async_fetch_certificate()

    datagrams: list[tuple[str, int]] = []

    async def _record_udp(sock: Any, data: bytes, address: tuple[str, int]) -> None:
        datagrams.append(address)

    loop = asyncio.get_running_loop()
    with (
        patch("custom_components.brightsign.api.socket.socket", MagicMock()),
        patch.object(loop, "sock_sendto", _record_udp),
    ):
        await client.async_send_udp_command("play", 5000)

    assert session.urls, "no requests recorded"
    assert {url.host for url in session.urls} == {HOST}
    assert tcp_hosts and set(tcp_hosts) == {HOST}
    assert datagrams == [(HOST, 5000)]


@pytest.mark.parametrize(
    "host", ["10.0.0.5", "192.168.1.255", "255.255.255.255", "169.254.1.2", "224.0.126.10"]
)
def test_send_udp_accepts_local_addresses(host: str) -> None:
    assert _local_ip(host) == host


@pytest.mark.parametrize("host", ["8.8.8.8", "example.com", "brightsign.local", "", "1.1.1.1"])
def test_send_udp_rejects_other_addresses(host: str) -> None:
    with pytest.raises(vol.Invalid):
        _local_ip(host)


def test_sync_listener_ignores_outside_and_oversized_traffic() -> None:
    sources: dict = {}
    protocol = _SyncProtocol(MagicMock(), sources)
    with patch("custom_components.brightsign.sync.async_dispatcher_send") as send:
        protocol.datagram_received(b'{"d":"1"}', ("8.8.8.8", 1539))
        protocol.datagram_received(b"x" * 4096, ("10.0.0.5", 1539))
        assert not send.called
        assert sources == {}
        protocol.datagram_received(b'{"d":"1"}', ("10.0.0.5", 1539))
        assert send.call_count == 1
    assert sources["10.0.0.5"][1] == "1"


def _ptp(message_type: int, domain: int, version: int = 2, size: int = 44) -> bytes:
    data = bytearray(size)
    data[0] = message_type
    data[1] = version
    data[4] = domain
    return bytes(data)


def test_ptp_listener_tells_leaders_and_followers_apart() -> None:
    sources: dict = {}
    protocol = _PtpProtocol(sources)
    protocol.datagram_received(_ptp(0x0, 3), ("10.0.0.5", 319))  # Sync: master
    protocol.datagram_received(_ptp(0x1, 3), ("10.0.0.6", 319))  # Delay_Req: follower
    protocol.datagram_received(_ptp(0x0, 3), ("8.8.8.8", 319))  # not local
    protocol.datagram_received(_ptp(0x0, 3, version=1), ("10.0.0.7", 319))  # PTPv1
    protocol.datagram_received(_ptp(0x0, 3, size=20), ("10.0.0.8", 319))  # too short
    protocol.datagram_received(_ptp(0x0, 3, size=2048), ("10.0.0.9", 319))  # too large
    assert set(sources) == {"10.0.0.5", "10.0.0.6"}
    assert sources["10.0.0.5"].master_seen is not None
    assert sources["10.0.0.5"].domain == 3
    assert sources["10.0.0.6"].follower_seen is not None
    assert sources["10.0.0.6"].master_seen is None
    assert parse_ptp_header(_ptp(0xB, 7)) == (0xB, 7)


def test_checker_passes() -> None:
    assert _load_checker().main(["check_local_only.py", str(INTEGRATION)]) == 0


def test_checker_catches_a_leaky_copy(tmp_path: Path) -> None:
    leaky = tmp_path / "brightsign"
    shutil.copytree(INTEGRATION, leaky)
    with (leaky / "sensor.py").open("a", encoding="utf-8") as fh:
        fh.write(
            "\nimport requests\n"
            'URL = "https://example.com/collect"\n\n\n'
            "async def _leak(loop, sock):\n"
            '    await loop.create_datagram_endpoint(lambda: None, local_addr=("", 9))\n'
            '    sock.sendto(b"x", ("8.8.8.8", 53))\n'
            "    sock.bind(('', 1))\n"
        )
    with (leaky / "sync.py").open("a", encoding="utf-8") as fh:
        fh.write("\n\ndef _send(sock):\n    sock.sendto(b'y', ('1.2.3.4', 1))\n")
    checker = _load_checker()
    problems = checker.check_manifest(leaky)
    for path in sorted(leaky.rglob("*.py")):
        problems += checker.check_source(path, str(path.relative_to(tmp_path)))
    text = "\n".join(problems)
    for expected in (
        "imports requests",
        "https://example.com",
        "create_datagram_endpoint() listens",
        "sensor.py",
        "sync.py",
        "8.8.8.8",
        "bind() outside",
    ):
        assert expected in text, expected
    assert checker.main(["check_local_only.py", str(leaky)]) == 1


async def test_send_rejected_by_local_ip_never_reaches_socket() -> None:
    """A public address in the action is refused before any socket is created."""
    with patch("custom_components.brightsign.api.socket.socket") as sock:
        with pytest.raises(vol.Invalid):
            _local_ip("8.8.4.4")
        assert not sock.called


def test_udp_event_listener_only_accepts_configured_players() -> None:
    listener = UdpEventListener(MagicMock(), 5000)
    listener.hosts.add("10.0.0.5")
    protocol = _UdpEventProtocol(MagicMock(), listener)
    with patch("custom_components.brightsign.udp_events.async_dispatcher_send") as send:
        protocol.datagram_received(b"play", ("10.0.0.6", 5000))  # not a configured player
        protocol.datagram_received(b"play", ("8.8.8.8", 5000))  # not local
        protocol.datagram_received(b"x" * 2048, ("10.0.0.5", 5000))  # too large
        assert not send.called
        for _ in range(50):
            protocol.datagram_received(b"state:intro", ("10.0.0.5", 5000))
        assert send.call_count == 20  # rate limited per second
        assert send.call_args.args[2] == "state:intro"


def test_ethernet_sync_listener_only_takes_sync_commands() -> None:
    sources: dict = {}
    protocol = _UdpSyncProtocol(sources)
    protocol.datagram_received(b"ply", ("10.0.0.5", 11167))
    protocol.datagram_received(b"pre:video1.mp4", ("10.0.0.6", 11167))
    protocol.datagram_received(b"play", ("10.0.0.7", 11167))  # not a 3-letter command
    protocol.datagram_received(b"ply", ("8.8.8.8", 11167))  # not local
    protocol.datagram_received(b"p" * 100, ("10.0.0.8", 11167))  # too large
    protocol.datagram_received(b"\xffly", ("10.0.0.9", 11167))  # not text
    assert set(sources) == {"10.0.0.5", "10.0.0.6"}
