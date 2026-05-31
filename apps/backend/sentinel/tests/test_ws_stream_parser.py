from __future__ import annotations

from app.services.ws.ws_stream_parser import parse_ws_message


def test_parse_ws_message_defaults_agent_mode_to_normal():
    parsed = parse_ws_message('{"type":"message","content":"hello"}')
    assert parsed is not None
    assert parsed.agent_mode.value == "normal"


def test_parse_ws_message_accepts_explicit_agent_mode():
    parsed = parse_ws_message('{"type":"message","content":"hello","agent_mode":"read_only"}')
    assert parsed is not None
    assert parsed.agent_mode.value == "read_only"


def test_parse_ws_message_rejects_invalid_agent_mode():
    parsed = parse_ws_message('{"type":"message","content":"hello","agent_mode":"invalid_mode"}')
    assert parsed is None
