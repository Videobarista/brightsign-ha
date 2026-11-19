"""Hardening tests: untrusted player data, validation and flooding."""

from __future__ import annotations

import json
import random
from unittest.mock import MagicMock

from custom_components.brightsign.api import (
    MAX_ITEMS,
    build_digest_header,
    find_key,
    is_local_address,
    is_valid_udp_message,
    parse_edid,
    parse_legacy_info,
    parse_udp_events,
    parse_uptime,
    parse_user_vars,
    parse_video_modes,
    parse_xml,
    sanitize_text,
    unwrap,
    xml_fields,
)
from custom_components.brightsign.sync import SyncTracker, decode_sync_message, player_time
from homeassistant.core import HomeAssistant


def test_sanitize_text_strips_control_characters_and_bounds_length() -> None:
    assert sanitize_text("a\x00b\nc\x7f") == "abc"
    assert len(sanitize_text("x" * 500)) == 64


def test_udp_message_validation() -> None:
    assert is_valid_udp_message("play")
    assert not is_valid_udp_message("")
    assert not is_valid_udp_message("play\nstop")
    assert not is_valid_udp_message("é")
    assert not is_valid_udp_message("x" * 1025)


def test_local_address() -> None:
    assert is_local_address("192.168.1.50")
    assert not is_local_address("93.184.216.34")
    assert not is_local_address("player.example")


def test_xml_rejects_entities_and_oversize() -> None:
    assert parse_xml('<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>') is None
    assert parse_xml("<a>" + "x" * 1_100_000 + "</a>") is None


def test_udp_events_are_bounded_and_invalid_actions_dropped() -> None:
    events = "".join(
        f"<udpEvent><label>L{i}</label><action>a{i}</action></udpEvent>" for i in range(200)
    )
    bad = "<udpEvent><action>bad\nx</action></udpEvent>"
    xml = f"<R><receivePort>99999</receivePort><e>{bad}{events}</e></R>"
    parsed, port = parse_udp_events(parse_xml(xml))
    assert len(parsed) == MAX_ITEMS
    assert all("\n" not in e["action"] for e in parsed)
    assert port is None  # out of range


def test_user_vars_bounded() -> None:
    xml = "<U>" + "".join(f'<V name="n{i}">v</V>' for i in range(500)) + "</U>"
    assert len(parse_user_vars(parse_xml(xml))) == MAX_ITEMS


def test_digest_prefers_sha256() -> None:
    header = build_digest_header(
        [
            'Digest realm="r", nonce="n", qop="auth", algorithm=MD5',
            'Digest realm="r", nonce="n", qop="auth", algorithm=SHA-256',
        ],
        "GET",
        "/api/v1/info/",
        "admin",
        "pw",
    )
    assert header is not None and "algorithm=SHA-256" in header


def test_find_key_depth_bounded() -> None:
    deep: dict = {}
    node = deep
    for _ in range(50):
        node["x"] = {}
        node = node["x"]
    node["serial"] = "S"
    assert find_key(deep, "serial") is None
    assert unwrap({"data": {"result": {"a": 1}}}) == {"a": 1}


async def test_sync_heartbeat_does_not_write_states(hass: HomeAssistant) -> None:
    tracker = SyncTracker(hass, "10.0.0.5")
    update = MagicMock()
    tracker.async_add_listener(update)
    tracker.async_handle({"d": "1", "p": "1\n1\n20261118T153917.243498"})
    assert update.call_count == 1  # became active
    for _ in range(20):
        tracker.async_handle({"d": "vsync:1", "p": "vsync:1\nvsync\n20261118T154540.5"})
    assert update.call_count == 1  # heartbeats only: no writes
    tracker.async_handle({"d": "1", "p": "1\n1\n20261118T155417.834393"})
    assert update.call_count == 2
    assert tracker.last_item_duration == 900.6
    assert tracker.item_started is not None
    tracker.async_handle(None)
    assert tracker.encrypted


def test_player_time() -> None:
    stamp = player_time("1\n1\n20261118T155417.834393")
    assert stamp is not None and stamp.minute == 54
    assert player_time("") is None


async def test_fuzz_parsers_never_crash(hass: HomeAssistant) -> None:
    """Random and mutated input must never raise from the parsers."""
    rng = random.Random(1234)  # noqa: S311 - test data, not crypto
    seeds = [
        "<R><receivePort>5000</receivePort><udpEvent><label>P</label><action>p</action></udpEvent></R>",
        '<U><BrightSignVar name="a">b</BrightSignVar></U>',
        '{"data": {"result": {"serial": "X", "attached": {"result": true}}}}',
        '{"d": "1", "p": "1\\n1\\n20261118T155417.834393", "v": 1}',
    ]
    tracker = SyncTracker(hass, "10.0.0.5")
    for _ in range(2000):
        seed = rng.choice(seeds)
        chars = list(seed)
        for _ in range(rng.randint(0, 8)):
            if chars and rng.random() < 0.5:
                del chars[rng.randrange(len(chars))]
            else:
                chars.insert(rng.randrange(len(chars) + 1), chr(rng.randint(0, 0x2FF)))
        text = "".join(chars)
        root = parse_xml(text)
        parse_udp_events(root)
        parse_user_vars(root)
        xml_fields(root)
        decoded = decode_sync_message(text.encode("utf-8", errors="ignore"))
        tracker.async_handle(decoded)
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        find_key(data, "serial")
        build_digest_header([text], "GET", "/", "admin", "pw")
        parse_legacy_info(text)
        parse_uptime(text)
        parse_edid(text)
        parse_video_modes(data if isinstance(data, list) else [text])


LEGACY_PAGE = """<html><head><title>BrightSign&reg;</title></head><body>
<ul><li><a href="index.html">INFO</a></li></ul>
<table><tr><td>Name:</td><td>Lobby</td></tr>
<tr><td>Model:</td><td>XD1032</td></tr>
<tr><td>Unique ID:</td><td>ABCD12345678</td></tr>
<tr><td>Firmware Version:</td><td>7.1.113</td></tr>
<tr><td>Video Mode:</td><td>1920x1080x50p (1920x1080x50p)</td></tr>
<tr><td>Uptime:</td><td>3 hours 3 minutes</td></tr>
<tr><td>PoE:</td><td>Inactive</td></tr></table>
<script>var x = "Name:";</script></body></html>"""


def test_parse_legacy_info_page() -> None:
    info = parse_legacy_info(LEGACY_PAGE)
    assert info["name"] == "Lobby"
    assert info["model"] == "XD1032"
    assert info["serial"] == "ABCD12345678"
    assert info["firmware"] == "7.1.113"
    assert info["video_mode"] == "1920x1080x50p"
    assert info["poe"] == "Inactive"
    assert parse_uptime(info["uptime"]) == 3 * 3600 + 3 * 60


def test_parse_legacy_info_rejects_other_pages() -> None:
    assert parse_legacy_info("<html>401 Not authorized</html>") == {}
    assert parse_legacy_info("<html>BrightSign</html>") == {}
    assert parse_legacy_info("BrightSign " + "x" * 1_100_000) == {}
    assert parse_uptime("unknown") is None
    empty_name = parse_legacy_info(
        "<p>BrightSign</p><p>Name:</p><p>Model:</p><p>XD1032</p><p>Unique ID:</p><p>X1</p>"
    )
    assert "name" not in empty_name
    assert empty_name["model"] == "XD1032"
    one_line = parse_legacy_info("<p>BrightSign</p><p>Unique ID: X1</p><p>Firmware Version: 8</p>")
    assert one_line == {"serial": "X1", "firmware": "8"}


def _edid(manufacturer: str, product: int, name: str) -> str:
    code = 0
    for char in manufacturer:
        code = (code << 5) | (ord(char) - 64)
    raw = bytearray(128)
    raw[0:8] = bytes.fromhex("00ffffffffffff00")
    raw[8:10] = code.to_bytes(2, "big")
    raw[10:12] = product.to_bytes(2, "little")
    raw[54:72] = b"\x00\x00\x00\xfc\x00" + name.encode().ljust(13, b"\x0a")
    return raw.hex()


def test_parse_edid_identifies_display() -> None:
    info = parse_edid(_edid("SAM", 0x7123, "QM55B"))
    assert info["manufacturer"] == "Samsung"
    assert info["model"] == "QM55B"
    assert info["product_code"] == "7123"
    assert parse_edid("00" * 128) == {}  # power save: zeroes
    assert parse_edid("not hex") == {}
    assert parse_edid({"manufacturer": "LG\x00", "monitorName": "65UH5"}) == {
        "manufacturer": "LG",
        "model": "65UH5",
    }


def test_parse_video_modes_validated_and_bounded() -> None:
    modes = parse_video_modes(
        [{"modeName": "1920x1080x60p"}, "3840x2160x30p", {"name": "bad mode;rm"}, 42]
        + [f"{i}x{i}x60p" for i in range(100, 300)]
    )
    assert modes[:2] == ["1920x1080x60p", "3840x2160x30p"]
    assert "bad mode;rm" not in modes
    assert len(modes) == MAX_ITEMS
