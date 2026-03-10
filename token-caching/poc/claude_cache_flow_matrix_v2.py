#!/usr/bin/env python3
import json
import os
import time
import uuid
from copy import deepcopy

import httpx

TOKEN = os.environ["ANTHROPIC_TOKEN"]
URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-3-haiku-20240307"
RUN_ID = uuid.uuid4().hex[:10]

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


cases = [
    {
        "name": "A_beta_scope_plus_cache_control_ttl1h",
        "beta_scope": True,
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
        "change_stable_on_run2": False,
    },
    {
        "name": "B_no_scope_beta_with_cache_control_ttl1h",
        "beta_scope": False,
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
        "change_stable_on_run2": False,
    },
    {
        "name": "C_scope_beta_no_cache_control",
        "beta_scope": True,
        "cache_control": None,
        "change_stable_on_run2": False,
    },
    {
        "name": "D_scope_beta_cache_control_ttl1h_stable_plus_one_char_change",
        "beta_scope": True,
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
        "change_stable_on_run2": True,
    },
    {
        "name": "E_scope_beta_cache_control_no_ttl",
        "beta_scope": True,
        "cache_control": {"type": "ephemeral"},
        "change_stable_on_run2": False,
    },
    {
        "name": "F_scope_beta_cache_control_ttl5m",
        "beta_scope": True,
        "cache_control": {"type": "ephemeral", "ttl": "5m"},
        "change_stable_on_run2": False,
    },
]

results = []

for idx, case in enumerate(cases, start=1):
    now = int(time.time())
    # isolate cache namespace per case and run to avoid cross-case contamination
    stable_anchor = f"POC_CASE_{idx}_{RUN_ID}_{case['name']}::"
    stable_text_1 = (stable_anchor + " STABLE_SEGMENT ") * 140
    stable_text_2 = stable_text_1 + "X" if case["change_stable_on_run2"] else stable_text_1

    def make_payload(stable_text, run_num):
        system_blocks = [{"type": "text", "text": stable_text}]
        if case["cache_control"] is not None:
            system_blocks[0]["cache_control"] = deepcopy(case["cache_control"])
        # always dynamic second block to verify dynamic changes do not break prefix cache
        system_blocks.append({"type": "text", "text": f"dynamic_{RUN_ID}_{idx}_{now}_{run_num}"})
        return {
            "model": MODEL,
            "max_tokens": 8,
            "temperature": 0,
            "system": system_blocks,
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": f"reply {run_num}"}]}
            ],
        }

    r1 = call(make_payload(stable_text_1, 1), case["beta_scope"])
    time.sleep(1)
    r2 = call(make_payload(stable_text_2, 2), case["beta_scope"])

    u1 = r1.get("usage") or {}
    u2 = r2.get("usage") or {}

    results.append(
        {
            "case": case["name"],
            "run_id": RUN_ID,
            "beta_scope": case["beta_scope"],
            "cache_control": case["cache_control"],
            "run1": r1,
            "run2": r2,
            "summary": {
                "run1_created_tokens": u1.get("cache_creation_input_tokens") if u1 else None,
                "run2_read_tokens": u2.get("cache_read_input_tokens") if u2 else None,
                "run1_created_bool": (u1.get("cache_creation_input_tokens") or 0) > 0,
                "run2_read_bool": (u2.get("cache_read_input_tokens") or 0) > 0,
            },
        }
    )

print(json.dumps(results, indent=2))
