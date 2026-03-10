#!/usr/bin/env python3
import json
import os
import time
from copy import deepcopy

import httpx

TOKEN = os.environ["ANTHROPIC_TOKEN"]
URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-3-haiku-20240307"

BASE_HEADERS = {
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
    "authorization": f"Bearer {TOKEN}",
}


def usage_slim(u):
    if not isinstance(u, dict):
        return None
    return {
        "input_tokens": u.get("input_tokens"),
        "cache_creation_input_tokens": u.get("cache_creation_input_tokens"),
        "cache_read_input_tokens": u.get("cache_read_input_tokens"),
        "cache_creation": u.get("cache_creation"),
        "output_tokens": u.get("output_tokens"),
    }


def call(payload, beta_scope):
    headers = dict(BASE_HEADERS)
    headers["anthropic-beta"] = (
        "oauth-2025-04-20,prompt-caching-scope-2026-01-05"
        if beta_scope
        else "oauth-2025-04-20"
    )
    r = httpx.post(URL, headers=headers, json=payload, timeout=120)
    if r.status_code != 200:
        return {"status": r.status_code, "error": r.text[:400], "usage": None}
    data = r.json()
    return {"status": 200, "error": "", "usage": usage_slim(data.get("usage"))}


stable_base = "SENTINEL_POC_STABLE_CACHE_BLOCK " * 180

cases = [
    {
        "name": "A_working_scope_beta_and_cache_control_ttl1h",
        "beta_scope": True,
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
        "change_stable_on_run2": False,
        "include_dynamic": True,
    },
    {
        "name": "B_no_prompt_caching_scope_beta_but_cache_control",
        "beta_scope": False,
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
        "change_stable_on_run2": False,
        "include_dynamic": True,
    },
    {
        "name": "C_scope_beta_but_no_cache_control",
        "beta_scope": True,
        "cache_control": None,
        "change_stable_on_run2": False,
        "include_dynamic": True,
    },
    {
        "name": "D_scope_beta_cache_control_but_stable_changes_by_one_char",
        "beta_scope": True,
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
        "change_stable_on_run2": True,
        "include_dynamic": True,
    },
    {
        "name": "E_scope_beta_cache_control_dynamic_changes_only",
        "beta_scope": True,
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
        "change_stable_on_run2": False,
        "include_dynamic": True,
    },
    {
        "name": "F_scope_beta_cache_control_no_ttl",
        "beta_scope": True,
        "cache_control": {"type": "ephemeral"},
        "change_stable_on_run2": False,
        "include_dynamic": True,
    },
    {
        "name": "G_scope_beta_cache_control_ttl5m",
        "beta_scope": True,
        "cache_control": {"type": "ephemeral", "ttl": "5m"},
        "change_stable_on_run2": False,
        "include_dynamic": True,
    },
    {
        "name": "H_non_allowlisted_style_manual_forced_markers",
        "beta_scope": True,
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
        "change_stable_on_run2": False,
        "include_dynamic": True,
        "note": "query_source concept is client-side only; server sees markers+headers only",
    },
]

results = []

for case in cases:
    now = int(time.time())
    stable_1 = stable_base
    stable_2 = stable_base + "X" if case["change_stable_on_run2"] else stable_base

    def make_payload(stable_text, run_id):
        system_blocks = [{"type": "text", "text": stable_text}]
        if case["cache_control"] is not None:
            system_blocks[0]["cache_control"] = deepcopy(case["cache_control"])
        if case["include_dynamic"]:
            system_blocks.append({"type": "text", "text": f"runtime_dynamic_{now}_{run_id}"})

        return {
            "model": MODEL,
            "max_tokens": 8,
            "temperature": 0,
            "system": system_blocks,
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": f"reply {run_id}"}]}
            ],
        }

    p1 = make_payload(stable_1, 1)
    r1 = call(p1, case["beta_scope"])
    time.sleep(1)
    p2 = make_payload(stable_2, 2)
    r2 = call(p2, case["beta_scope"])

    u1 = r1.get("usage") or {}
    u2 = r2.get("usage") or {}
    summary = {
        "run1_cache_created": (u1.get("cache_creation_input_tokens") or 0) > 0,
        "run2_cache_read": (u2.get("cache_read_input_tokens") or 0) > 0,
    }

    results.append(
        {
            "case": case["name"],
            "beta_scope": case["beta_scope"],
            "cache_control": case["cache_control"],
            "change_stable_on_run2": case["change_stable_on_run2"],
            "run1": r1,
            "run2": r2,
            "summary": summary,
            **({"note": case["note"]} if "note" in case else {}),
        }
    )

print(json.dumps(results, indent=2))
