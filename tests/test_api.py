"""Tests for the network scan and protocol helpers."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.brightsign.api import (
    MAX_SCAN_HOSTS,
    BrightSignClient,
    BrightSignConnectionError,
    BrightSignNotSupported,
    async_scan_for_players,
    is_brightsign_mac,
    parse_arp_table,
    scan_targets,
)
from custom_components.brightsign.sync import player_time

ARP = """IP address       HW type     Flags       HW address            Mask     Device
10.0.0.5         0x1         0x2         90:ac:3f:12:34:56     *        eth0
10.0.0.6         0x1         0x2         aa:bb:cc:dd:ee:ff     *        eth0
10.0.0.7         0x1         0x0         00:00:00:00:00:00     *        eth0
10.0.0.8         0x1         0x2         90:AC:3F:00:00:08     *        eth0
not-an-ip        0x1         0x2         90:ac:3f:00:00:09     *        eth0
10.0.0.10        0x1         zz          90:ac:3f:00:00:10     *        eth0
"""


def test_is_brightsign_mac() -> None:
    assert is_brightsign_mac("90:ac:3f:12:34:56")
    assert is_brightsign_mac("90:AC:3F:12:34:56")
    assert not is_brightsign_mac("aa:bb:cc:dd:ee:ff")
    assert not is_brightsign_mac(None)
    assert not is_brightsign_mac("")


def test_parse_arp_table_keeps_complete_entries() -> None:
    assert parse_arp_table(ARP) == {
        "10.0.0.5": "90:ac:3f:12:34:56",
        "10.0.0.6": "aa:bb:cc:dd:ee:ff",
        "10.0.0.8": "90:ac:3f:00:00:08",
    }
    assert parse_arp_table("") == {}


def test_scan_targets_only_private_and_bounded() -> None:
    targets = scan_targets(["10.0.0.2/24"])
    assert len(targets) == 253  # .1 - .254 without Home Assistant itself
    assert "10.0.0.2" not in targets
    assert scan_targets(["8.8.8.8/24", "127.0.0.1/8", "169.254.1.1/16", "bad"]) == []
    # A large network is narrowed to the /24 Home Assistant is in
    large = scan_targets(["172.16.5.9/12"])
    assert large[0] == "172.16.5.1"
    assert len(large) == 253
    assert len(scan_targets(["10.0.0.2/22", "10.1.0.2/22"])) == MAX_SCAN_HOSTS
    assert len(scan_targets(["10.0.0.2/24", "10.0.0.3/24"])) == 254  # no duplicates


async def test_scan_finds_only_brightsign_players() -> None:
    writer = MagicMock()
    knocked: list[str] = []

    async def fake_connect(host: str, port: int):
        assert port == 80
        knocked.append(host)
        if host == "10.0.0.5":
            return MagicMock(), writer
        raise ConnectionRefusedError

    with (
        patch("custom_components.brightsign.api.asyncio.open_connection", fake_connect),
        patch("custom_components.brightsign.api._read_arp_table", return_value=ARP),
    ):
        found = await async_scan_for_players(["10.0.0.5", "10.0.0.6", "8.8.8.8"])
    assert found == {"10.0.0.5": "90:ac:3f:12:34:56", "10.0.0.8": "90:ac:3f:00:00:08"}
    writer.close.assert_called_once()
    assert "8.8.8.8" not in knocked  # public addresses are never contacted


async def test_scan_survives_timeouts_and_missing_arp_table() -> None:
    async def slow_connect(host: str, port: int):
        await asyncio.sleep(10)

    with (
        patch("custom_components.brightsign.api.asyncio.open_connection", slow_connect),
        patch("custom_components.brightsign.api.SCAN_TIMEOUT", 0.01),
        patch("custom_components.brightsign.api.ARP_TABLE", "/nonexistent/arp"),
    ):
        assert await async_scan_for_players(["10.0.0.5"]) == {}


def test_player_time() -> None:
    assert player_time("1\n1\n20261118T155417.834393") == datetime(2026, 11, 18, 15, 54, 17, 834393)
    assert player_time("1\n1\n20261118T155417") == datetime(2026, 11, 18, 15, 54, 17)
    assert player_time("garbage") is None
    assert player_time("") is None


JPEG = b"\xff\xd8" + b"\x00" * 64 + b"\xff\xd9"
SNAPSHOT = "/sd/remote_snapshots/img-2026-11-19-06-37-17.jpg"


async def test_snapshot_is_downloaded_and_removed() -> None:
    """Removing a snapshot tries the path without and with a trailing slash."""
    client = BrightSignClient(MagicMock(), "10.0.0.5", rest_scheme="http")
    calls: list[tuple[str, str]] = []

    async def fake_rest(method: str, path: str, payload: dict | None = None):
        calls.append((method, path))
        if method == "POST":
            return {"data": {"result": {"filename": SNAPSHOT}}}
        if path.endswith(".jpg"):
            raise BrightSignNotSupported(path)
        return {}

    with (
        patch.object(client, "_rest", fake_rest),
        patch.object(client, "_http", AsyncMock(return_value=JPEG)),
    ):
        assert await client.async_snapshot() == JPEG
        assert calls[-1] == ("DELETE", "/api/v1/files" + SNAPSHOT + "/")


async def test_snapshot_removal_stops_when_not_allowed() -> None:
    client = BrightSignClient(MagicMock(), "10.0.0.5", rest_scheme="http")
    deletes: list[str] = []

    async def fake_rest(method: str, path: str, payload: dict | None = None):
        if method == "POST":
            return {"data": {"result": {"filename": SNAPSHOT}}}
        deletes.append(path)
        raise BrightSignNotSupported(path)

    with (
        patch.object(client, "_rest", fake_rest),
        patch.object(client, "_http", AsyncMock(return_value=JPEG)),
    ):
        await client.async_snapshot()
        await client.async_snapshot()
    assert len(deletes) == 2  # both forms once, then no more attempts


async def test_incomplete_snapshot_is_downloaded_again() -> None:
    client = BrightSignClient(MagicMock(), "10.0.0.5", rest_scheme="http")
    download = AsyncMock(side_effect=[JPEG[:20], JPEG])

    async def fake_rest(method: str, path: str, payload: dict | None = None):
        if method == "POST":
            return {"data": {"result": {"filename": SNAPSHOT}}}
        return {}

    with (
        patch.object(client, "_rest", fake_rest),
        patch.object(client, "_http", download),
        patch("custom_components.brightsign.api.asyncio.sleep", AsyncMock()),
    ):
        assert await client.async_snapshot() == JPEG
    assert download.call_count == 2


async def test_cec_not_offered() -> None:
    client = BrightSignClient(MagicMock(), "10.0.0.5", rest_scheme="http")
    with (
        patch.object(client, "_rest", AsyncMock(side_effect=BrightSignNotSupported("x"))),
        pytest.raises(BrightSignNotSupported),
    ):
        await client.async_send_cec("4004")
    assert client.cec_supported is False


class _Content:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, size: int):
        for chunk in self._chunks:
            yield chunk


async def test_responses_are_read_to_the_end() -> None:
    """A large image arrives in several chunks: all of them are read (no half screenshots)."""
    client = BrightSignClient(MagicMock(), "10.0.0.5", rest_scheme="http")
    response = MagicMock()
    response.content_length = None
    response.content = _Content([b"a" * 65536, b"b" * 65536, b"c" * 100])
    assert await client._read_limited(response, 200_000) == b"a" * 65536 + b"b" * 65536 + b"c" * 100
    response.content = _Content([b"a" * 65536, b"b" * 65536])
    with pytest.raises(BrightSignConnectionError):
        await client._read_limited(response, 100_000)


async def test_video_mode_uses_documented_body() -> None:
    client = BrightSignClient(MagicMock(), "10.0.0.5", rest_scheme="http")
    rest = AsyncMock(return_value={})
    with patch.object(client, "_rest", rest):
        await client.async_set_video_mode("1920x1080x60p")
    rest.assert_called_once_with(
        "PUT", "/api/v1/video/hdmi/output/0/mode/", {"name": {"modeName": "1920x1080x60p"}}
    )
    rest = AsyncMock(side_effect=[BrightSignConnectionError("HTTP 500 for x"), {}])
    with patch.object(client, "_rest", rest):
        await client.async_set_video_mode("1920x1080x60p")
    assert rest.call_args.args[2] == {"modeName": "1920x1080x60p"}
