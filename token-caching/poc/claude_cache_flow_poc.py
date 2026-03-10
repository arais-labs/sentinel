#!/usr/bin/env python3
import fnmatch
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


def load_claude_flags(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def get_allowlist(flags: dict[str, Any]) -> list[str]:
    cfg = (
        flags.get("cachedGrowthBookFeatures", {})
        .get("tengu_prompt_cache_1h_config", {})
        .get("allowlist", [])
    )
    return [str(x) for x in cfg if isinstance(x, str)]


def get_system_prompt_global_cache(flags: dict[str, Any]) -> bool:
    return bool(flags.get("cachedGrowthBookFeatures", {}).get("tengu_system_prompt_global_cache", False))


def is_allowlisted(query_source: str, allowlist: list[str]) -> bool:
    return any(fnmatch.fnmatch(query_source, pattern) for pattern in allowlist)


@dataclass
class ProbeCase:
    query_source: str
    allowlisted: bool
    headers: dict[str, str]
    payload: dict[str, Any]


def build_case(
    *,
    token: str,
    query_source: str,
    allowlist: list[str],
    system_global_cache: bool,
    run_marker: int,
) -> ProbeCase:
    allow = is_allowlisted(query_source, allowlist)

    betas = ["oauth-2025-04-20"]
    if allow:
        betas.append("prompt-caching-scope-2026-01-05")

    headers = {
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "anthropic-beta": ",".join(betas),
    }

    stable_core = "You are a coding assistant. Follow user intent exactly. " * 120
    stable_policy = "Only call tools when needed. Return concise answers. " * 90
    runtime_dynamic = f"Current unix time is {int(time.time())}. run={run_marker}."

    system_blocks: list[dict[str, Any]] = []

    if system_global_cache and allow:
        merged = f"{stable_core}\n\n{stable_policy}"
        system_blocks.append(
            {
                "type": "text",
                "text": merged,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        )
    else:
        core_block: dict[str, Any] = {"type": "text", "text": stable_core}
        policy_block: dict[str, Any] = {"type": "text", "text": stable_policy}
        if allow:
            core_block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
            policy_block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
        system_blocks.extend([core_block, policy_block])

    system_blocks.append({"type": "text", "text": runtime_dynamic})

    payload = {
        "model": "claude-3-haiku-20240307",
        "max_tokens": 48,
        "temperature": 0,
        "system": system_blocks,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Reply with only the query source: {query_source}"}
                ],
            }
        ],
    }

    return ProbeCase(query_source=query_source, allowlisted=allow, headers=headers, payload=payload)


def call_messages(case: ProbeCase) -> tuple[int, dict[str, Any] | None, str]:
    url = "https://api.anthropic.com/v1/messages"
    with httpx.Client(timeout=90) as client:
        resp = client.post(url, headers=case.headers, json=case.payload)
    if resp.status_code != 200:
        return resp.status_code, None, resp.text[:300]
    data = resp.json()
    return resp.status_code, data.get("usage"), ""


def run_probe(token: str, flags_path: Path) -> None:
    flags = load_claude_flags(flags_path)
    allowlist = get_allowlist(flags)
    global_cache = get_system_prompt_global_cache(flags)

    print("flags_path", str(flags_path))
    print("allowlist", json.dumps(allowlist))
    print("system_prompt_global_cache", global_cache)

    probes = ["sdk", "manual_probe"]

    results: list[dict[str, Any]] = []

    for qs in probes:
        case1 = build_case(
            token=token,
            query_source=qs,
            allowlist=allowlist,
            system_global_cache=global_cache,
            run_marker=1,
        )
        status1, usage1, err1 = call_messages(case1)

        case2 = build_case(
            token=token,
            query_source=qs,
            allowlist=allowlist,
            system_global_cache=global_cache,
            run_marker=2,
        )
        status2, usage2, err2 = call_messages(case2)

        results.append(
            {
                "query_source": qs,
                "allowlisted": case1.allowlisted,
                "beta_header": case1.headers.get("anthropic-beta"),
                "cached_system_blocks": sum(1 for b in case1.payload["system"] if "cache_control" in b),
                "run1_status": status1,
                "run1_usage": usage1,
                "run1_error": err1,
                "run2_status": status2,
                "run2_usage": usage2,
                "run2_error": err2,
            }
        )

    print("results")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    token = os.environ.get("ANTHROPIC_TOKEN") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if not token:
        raise SystemExit("Set ANTHROPIC_TOKEN or CLAUDE_CODE_OAUTH_TOKEN")
    flags_file = Path(os.environ.get("CLAUDE_FLAGS_FILE", "/mnt/.claude.json"))
    run_probe(token, flags_file)
