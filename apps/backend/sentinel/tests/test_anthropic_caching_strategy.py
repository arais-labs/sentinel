"""Tests for the Anthropic OAuth prompt caching strategy (V4).

Strategy: 1 slot on last stable system block (caches entire prefix) +
3 slots on most recent conversation messages (rolling breakpoints).

Total: never exceeds 4 cache_control blocks (Anthropic hard limit).
"""

from __future__ import annotations

import json
from typing import Any

from app.services.llm.providers.anthropic import (
    _build_oauth_cache_aware_payload,
    _ANTHROPIC_MAX_CACHE_CONTROL_BLOCKS,
    _SYSTEM_CACHE_BUDGET,
    _MESSAGE_CACHE_BUDGET,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _system_block(text: str, *, kind: str = "base_prompt") -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "text": text}
    if kind == "runtime_info":
        block["_runtime_dynamic"] = True
    return block


def _user_msg(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _assistant_msg(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def _tool_call_msg(tool_id: str, name: str, args: dict) -> dict[str, Any]:
    return {"role": "assistant", "content": [
        {"type": "tool_use", "id": tool_id, "name": name, "input": args}
    ]}


def _tool_result_msg(tool_id: str, result: str) -> dict[str, Any]:
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": result}
    ]}


def _count_cache_markers(items: list[dict[str, Any]]) -> int:
    """Count all cache_control markers across blocks and nested message content."""
    count = 0
    for item in items:
        if item.get("cache_control") is not None:
            count += 1
        content = item.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("cache_control") is not None:
                    count += 1
    return count


def _get_cached_msg_indices(messages: list[dict[str, Any]]) -> list[int]:
    """Return indices of messages that have any cache_control markers."""
    indices = []
    for idx, msg in enumerate(messages):
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("cache_control"):
                    indices.append(idx)
                    break
    return indices


# ---------------------------------------------------------------------------
# Test 1: Basic session — 1 system marker, messages get breakpoints
# ---------------------------------------------------------------------------


def test_basic_session():
    system_blocks = [
        _system_block("You are Sentinel."),
        _system_block("Current time: now", kind="runtime_info"),
        _system_block("## Delegation Policy\nDelegate things."),
        _system_block("## Memory: Identity\nYou are Ari."),
    ]
    messages = [
        _user_msg("Hello"),
        _assistant_msg("Hi!"),
        _user_msg("What's up?"),
    ]

    sys_out, msg_out = _build_oauth_cache_aware_payload(
        system_blocks=system_blocks, messages=messages,
    )

    # Exactly 1 system block should be cached (the last stable one)
    sys_markers = _count_cache_markers(sys_out)
    assert sys_markers == 1, f"Expected 1 system marker, got {sys_markers}"

    # The cached block should be the last stable one (index 3, "Memory: Identity")
    assert sys_out[3].get("cache_control") is not None
    # Dynamic block should NOT be cached
    assert sys_out[1].get("cache_control") is None
    # Earlier stable blocks should NOT have markers (prefix caching handles them)
    assert sys_out[0].get("cache_control") is None
    assert sys_out[2].get("cache_control") is None

    # Messages: 3 breakpoints (all 3 messages, budget = 3)
    msg_markers = _count_cache_markers(msg_out)
    assert msg_markers == 3

    # Total: 1 + 3 = 4 <= limit
    assert sys_markers + msg_markers <= _ANTHROPIC_MAX_CACHE_CONTROL_BLOCKS


# ---------------------------------------------------------------------------
# Test 2: Long conversation — only 3 most recent messages
# ---------------------------------------------------------------------------


def test_long_conversation():
    system_blocks = [_system_block("Prompt")]
    messages = []
    for i in range(20):
        messages.append(_user_msg(f"User {i}"))
        messages.append(_assistant_msg(f"Assistant {i}"))

    sys_out, msg_out = _build_oauth_cache_aware_payload(
        system_blocks=system_blocks, messages=messages,
    )

    sys_markers = _count_cache_markers(sys_out)
    msg_markers = _count_cache_markers(msg_out)

    assert sys_markers == 1
    assert msg_markers == 3, f"Expected 3 message breakpoints, got {msg_markers}"
    assert sys_markers + msg_markers == 4

    # Breakpoints on the LAST 3 messages
    cached = _get_cached_msg_indices(msg_out)
    n = len(msg_out)
    assert cached == [n - 3, n - 2, n - 1]


# ---------------------------------------------------------------------------
# Test 3: Tool use — tool results get breakpoints, tool_use blocks don't
# ---------------------------------------------------------------------------


def test_tool_use_conversation():
    system_blocks = [_system_block("Prompt")]
    messages = [
        _user_msg("Check status"),
        _tool_call_msg("c1", "runtime_exec", {"command": "git status"}),
        _tool_result_msg("c1", "On branch main"),
        _assistant_msg("Branch is clean."),
        _user_msg("Run tests"),
        _tool_call_msg("c2", "runtime_exec", {"command": "pytest"}),
        _tool_result_msg("c2", "42 passed"),
    ]

    sys_out, msg_out = _build_oauth_cache_aware_payload(
        system_blocks=system_blocks, messages=messages,
    )

    msg_markers = _count_cache_markers(msg_out)
    assert msg_markers == 3

    # The tool_call (assistant with tool_use) should NOT be cached
    tool_call_1 = msg_out[1]  # assistant tool_use
    assert tool_call_1["content"][0]["type"] == "tool_use"
    assert tool_call_1["content"][-1].get("cache_control") is None

    # Last tool_result SHOULD be cached
    last_result = msg_out[-1]
    assert last_result["content"][-1].get("cache_control") is not None


# ---------------------------------------------------------------------------
# Test 4: Dynamic runtime_info is the last system block — skip it
# ---------------------------------------------------------------------------


def test_dynamic_last_system_block():
    """When runtime_info is the last system block, cache the second-to-last."""
    system_blocks = [
        _system_block("You are Sentinel."),
        _system_block("## Memory\nStuff."),
        _system_block("Current time: 2026-03-10", kind="runtime_info"),
    ]
    messages = [_user_msg("Hello")]

    sys_out, msg_out = _build_oauth_cache_aware_payload(
        system_blocks=system_blocks, messages=messages,
    )

    # Dynamic block (last) should NOT be cached
    assert sys_out[2].get("cache_control") is None
    # The second block (last stable) should be cached
    assert sys_out[1].get("cache_control") is not None
    # First block should NOT (prefix caching covers it)
    assert sys_out[0].get("cache_control") is None


# ---------------------------------------------------------------------------
# Test 5: Many system blocks — still only 1 system marker
# ---------------------------------------------------------------------------


def test_many_system_blocks():
    system_blocks = [
        _system_block("Core prompt"),
        _system_block("Policy 1"),
        _system_block("Policy 2"),
        _system_block("Policy 3"),
        _system_block("Time", kind="runtime_info"),
        _system_block("Memory 1"),
        _system_block("Memory 2"),
        _system_block("Memory 3"),
        _system_block("Memory 4"),
        _system_block("Session summary"),
    ]
    messages = [
        _user_msg("Hello"),
        _assistant_msg("Hi!"),
        _user_msg("Do something"),
        _assistant_msg("Done."),
        _user_msg("More"),
        _assistant_msg("OK."),
    ]

    sys_out, msg_out = _build_oauth_cache_aware_payload(
        system_blocks=system_blocks, messages=messages,
    )

    sys_markers = _count_cache_markers(sys_out)
    msg_markers = _count_cache_markers(msg_out)

    # 1 system + 3 messages = 4
    assert sys_markers == 1
    assert msg_markers == 3
    assert sys_markers + msg_markers == 4

    # The last stable block ("Session summary" at index 9) should have the marker
    assert sys_out[9].get("cache_control") is not None

    # No internal markers leaked
    for block in sys_out:
        assert "_runtime_dynamic" not in block


# ---------------------------------------------------------------------------
# Test 6: Empty conversation — just system
# ---------------------------------------------------------------------------


def test_empty_conversation():
    system_blocks = [_system_block("Prompt")]
    messages = []

    sys_out, msg_out = _build_oauth_cache_aware_payload(
        system_blocks=system_blocks, messages=messages,
    )

    assert _count_cache_markers(sys_out) == 1
    assert _count_cache_markers(msg_out) == 0


# ---------------------------------------------------------------------------
# Test 7: Realistic full Sentinel context
# ---------------------------------------------------------------------------


def test_realistic_sentinel_context():
    system_blocks = [
        _system_block("You are Sentinel. Be concise and factual."),
        _system_block(
            "Current date: Tuesday, March 10, 2026\nSession: b18ab60b",
            kind="runtime_info",
        ),
        _system_block("## Delegation Policy\n" + "x" * 500),
        _system_block("## Hierarchical Memory Policy\n" + "x" * 800),
        _system_block("## Trigger Automation Policy\n" + "x" * 400),
        _system_block("## Browser Automation Playbook\n" + "x" * 600),
        _system_block("## Memory (pinned): Agent Identity\n" + "x" * 300),
        _system_block("## Memory (pinned): User Profile\n" + "x" * 200),
        _system_block("## Memory (pinned): Sentinel Project\n" + "x" * 400),
        _system_block("## Non-Pinned Roots\n" + "x" * 300),
        _system_block("## Relevant Branches\n" + "x" * 500),
        _system_block("Session summary: token caching work\n" + "x" * 200),
    ]

    messages = [
        _user_msg("Fix the caching bug"),
        _tool_call_msg("c1", "runtime_exec", {"command": "grep cache"}),
        _tool_result_msg("c1", "line 48: _MAX_CACHE = 4"),
        _assistant_msg("Found the issue."),
        _user_msg("OK fix it"),
        _tool_call_msg("c2", "runtime_exec", {"command": "python fix.py"}),
        _tool_result_msg("c2", "patched"),
        _tool_call_msg("c3", "runtime_exec", {"command": "pytest"}),
        _tool_result_msg("c3", "66 passed"),
        _assistant_msg("Fixed. All tests pass."),
        _user_msg("Push it"),
        _tool_call_msg("c4", "git_exec", {"command": "git push"}),
        _tool_result_msg("c4", "pushed to token-caching-poc"),
        _assistant_msg("Pushed."),
        _user_msg("Does caching work now?"),
    ]

    sys_out, msg_out = _build_oauth_cache_aware_payload(
        system_blocks=system_blocks, messages=messages,
    )

    sys_markers = _count_cache_markers(sys_out)
    msg_markers = _count_cache_markers(msg_out)

    # 1 system + 3 messages = 4 total
    assert sys_markers == 1
    assert msg_markers == 3
    total = sys_markers + msg_markers
    assert total == 4, f"Total markers {total} must be exactly 4"

    # System marker on last stable block (index 11, "Session summary")
    assert sys_out[11].get("cache_control") is not None
    # runtime_info (index 1) not cached
    assert sys_out[1].get("cache_control") is None

    # Message breakpoints on the last 3 eligible messages
    cached_indices = _get_cached_msg_indices(msg_out)
    n = len(msg_out)
    assert cached_indices == [n - 3, n - 2, n - 1]


# ---------------------------------------------------------------------------
# Test 8: No mutation of inputs
# ---------------------------------------------------------------------------


def test_no_input_mutation():
    system_blocks = [
        _system_block("Prompt"),
        _system_block("Time", kind="runtime_info"),
    ]
    messages = [_user_msg("Hello"), _assistant_msg("Hi!")]

    sys_snap = json.dumps(system_blocks)
    msg_snap = json.dumps(messages)

    _build_oauth_cache_aware_payload(
        system_blocks=system_blocks, messages=messages,
    )

    assert json.dumps(system_blocks) == sys_snap, "system_blocks mutated!"
    assert json.dumps(messages) == msg_snap, "messages mutated!"


# ---------------------------------------------------------------------------
# Test 9: Total markers never exceeds 4 regardless of conversation size
# ---------------------------------------------------------------------------


def test_total_markers_never_exceed_four():
    """Fuzz-like test: various conversation sizes, always <= 4 markers."""
    for num_sys in (1, 3, 5, 10, 15):
        for num_msgs in (0, 1, 2, 5, 10, 30):
            system_blocks = [_system_block(f"Sys {i}") for i in range(num_sys)]
            messages = []
            for i in range(num_msgs):
                messages.append(_user_msg(f"U{i}"))
                messages.append(_assistant_msg(f"A{i}"))

            sys_out, msg_out = _build_oauth_cache_aware_payload(
                system_blocks=system_blocks, messages=messages,
            )

            total = _count_cache_markers(sys_out) + _count_cache_markers(msg_out)
            assert total <= 4, (
                f"num_sys={num_sys} num_msgs={num_msgs}: total markers {total} > 4"
            )
