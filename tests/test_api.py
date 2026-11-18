"""Tests for the protocol helpers (no network)."""

from __future__ import annotations

from custom_components.brightsign.api import (
    build_digest_header,
    find_key,
    parse_udp_events,
    parse_user_vars,
    parse_xml,
    xml_fields,
)
from custom_components.brightsign.sync import _player_time

UDP_EVENTS = """<?xml version="1.0" encoding="UTF-8"?>
<BrightSignUDPEvents><receivePort>5001</receivePort>
<udpEvents><udpEvent><label>Play</label><action>play</action></udpEvent>
<udpEvent><label>Stop</label><action>stop</action></udpEvent></udpEvents></BrightSignUDPEvents>"""

USER_VARS = """<BrightSignUserVariables><BrightSignVar name="Title">Hi</BrightSignVar>
<BrightSignVar name="Volume">80</BrightSignVar></BrightSignUserVariables>"""


def test_parse_udp_events() -> None:
    events, port = parse_udp_events(parse_xml(UDP_EVENTS))
    assert [e["action"] for e in events] == ["play", "stop"]
    assert port == 5001


def test_parse_user_vars_and_fields() -> None:
    assert parse_user_vars(parse_xml(USER_VARS)) == {"Title": "Hi", "Volume": "80"}
    assert xml_fields(parse_xml("<a><serialNumber>X1</serialNumber></a>")) == {"serialNumber": "X1"}


def test_parse_xml_rejects_doctype_and_html() -> None:
    assert parse_xml('<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>') is None
    assert parse_xml("not xml") is None


def test_find_key_unwraps_results() -> None:
    data = {"attached": {"result": True}, "activeMode": {"result": {"modeName": "1080p50"}}}
    assert find_key(data, "attached") is True
    assert find_key(data, "modeName") == "1080p50"


def test_digest_header() -> None:
    header = build_digest_header(
        ['Digest realm="brightsign", nonce="abc", qop="auth", algorithm=MD5'],
        "GET",
        "/api/v1/info/",
        "admin",
        "pw",
    )
    assert header is not None
    assert header.startswith("Digest ") and 'uri="/api/v1/info/"' in header
    assert build_digest_header(['Basic realm="x"'], "GET", "/", "a", "b") is None


def test_player_time() -> None:
    stamp = _player_time("1\n1\n20261118T155417.834393")
    assert stamp is not None and stamp.minute == 54
    assert _player_time("") is None
