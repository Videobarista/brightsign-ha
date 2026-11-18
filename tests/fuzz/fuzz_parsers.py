"""Coverage guided fuzz target for the parsers (Atheris).

Run: pip install atheris && python tests/fuzz/fuzz_parsers.py -max_total_time=300
"""

import sys

import atheris

with atheris.instrument_imports():
    from custom_components.brightsign.api import (
        build_digest_header,
        parse_udp_events,
        parse_user_vars,
        parse_xml,
        xml_fields,
    )
    from custom_components.brightsign.sync import decode_sync_message, player_time


def test_one_input(data: bytes) -> None:
    text = data.decode("utf-8", errors="replace")
    root = parse_xml(text)
    parse_udp_events(root)
    parse_user_vars(root)
    xml_fields(root)
    decode_sync_message(data)
    player_time(text)
    build_digest_header([text], "GET", "/", "admin", "pw")


if __name__ == "__main__":
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()
