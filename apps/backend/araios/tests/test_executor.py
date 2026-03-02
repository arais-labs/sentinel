import asyncio

from app.services.executor import execute_action


def _run(coro):
    return asyncio.run(coro)


def test_execute_action_handles_nested_scope_in_comprehension():
    code = """
cache = {}
def resolve(value):
    if value not in cache:
        cache[value] = value.upper()
    return cache[value]
result = {"ok": True, "items": [resolve(v) for v in params.get("values", [])]}
"""
    result = _run(execute_action(code, {"params": {"values": ["a", "b", "a"]}}))
    assert result["ok"] is True
    assert result["items"] == ["A", "B", "A"]


def test_execute_action_returns_error_on_exception():
    code = "raise ValueError('boom')"
    result = _run(execute_action(code, {"params": {}}))
    assert result["ok"] is False
    assert "boom" in result["error"]
