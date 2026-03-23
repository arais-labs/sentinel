from __future__ import annotations

import pytest

from app.services.araios.system_modules.str_replace_editor.handlers import _parse_str_replace_output
from app.services.tools.executor import ToolValidationError


def test_parse_str_replace_output_accepts_last_json_line() -> None:
    payload = _parse_str_replace_output("\nnoise\n{\"ok\":true,\"path\":\"a\"}\n")
    assert payload["ok"] is True
    assert payload["path"] == "a"


def test_parse_str_replace_output_rejects_invalid() -> None:
    with pytest.raises(ToolValidationError):
        _parse_str_replace_output("not-json")
