#!/usr/bin/env python3
"""
Agent discovery script — shows exactly what an agent does to understand
available tools and how to call them. Uses only stdlib (urllib).

Usage:
    python3 scripts/agent_discovery.py [--base-url http://localhost:9000]
"""

import argparse
import json
import sys
import urllib.request
import urllib.error

BASE_URL = "http://localhost:9000"
TOKEN    = "dev-agent-token"


def call(method: str, url: str, body: dict = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req  = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def discover(base_url: str):
    # ── Step 1: fetch manifest ────────────────────────────────
    print("=" * 60)
    print("STEP 1 — Fetch manifest (single discovery call)")
    print("=" * 60)
    print(f"\n  GET {base_url}/api/manifest")
    print(f"  Authorization: Bearer {TOKEN}\n")

    manifest = call("GET", f"{base_url}/api/manifest")
    print("  ✓ 200 OK\n")

    # ── Step 2: auth instructions ─────────────────────────────
    auth = manifest["auth"]
    print("=" * 60)
    print("STEP 2 — Auth instructions from manifest")
    print("=" * 60)
    print(f"\n  Scheme : {auth['scheme']}")
    print(f"  Header : {auth['header']}")
    print(f"  Note   : {auth['note']}\n")

    # ── Step 3: inventory ─────────────────────────────────────
    modules = manifest["modules"]
    tools   = [m for m in modules if m["type"] == "tool"]
    data    = [m for m in modules if m["type"] != "tool"]

    print("=" * 60)
    print(f"STEP 3 — Module inventory ({len(modules)} total)")
    print("=" * 60)
    print(f"\n  Tool modules : {len(tools)}  (callable actions)")
    print(f"  Data modules : {len(data)}  (CRUD records)\n")

    if tools:
        print("=" * 60)
        print("TOOL MODULES")
        print("=" * 60)
        for mod in tools:
            print(f"\n  [{mod['name'].upper()}]  {mod['description']}")
            for ep in mod["endpoints"]:
                print(f"\n    action : {ep['action']}")
                print(f"    label  : {ep['label']}")
                if ep.get("description"):
                    print(f"    desc   : {ep['description']}")
                print(f"    call   : {ep['method']} {ep['url']}")
                if ep.get("body"):
                    print(f"    params :")
                    for key, meta in ep["body"].items():
                        req = "required" if meta.get("required") else "optional"
                        ph  = f"  e.g. {meta['placeholder']}" if meta.get("placeholder") else ""
                        print(f"             {key} ({meta['type']}, {req}){ph}")

    if data:
        print("\n" + "=" * 60)
        print("DATA MODULES")
        print("=" * 60)
        for mod in data:
            print(f"\n  [{mod['name'].upper()}]  {mod['description']}")
            for ep in mod["endpoints"]:
                body_keys = list(ep.get("body", {}).keys())
                body_hint = f"  fields: {body_keys}" if body_keys else ""
                print(f"    {ep['method']:<7} {ep['url']}{body_hint}")

    # ── Step 4: example live call (first no-param tool action) ─
    example_tool = next(
        (
            (mod, ep)
            for mod in tools
            for ep in mod["endpoints"]
            if not any(m.get("required") for m in ep.get("body", {}).values())
        ),
        None,
    )

    if example_tool:
        mod, ep = example_tool
        print("\n" + "=" * 60)
        print(f"STEP 4 — Live example call: {mod['name']}.{ep['action']}")
        print("=" * 60)
        print(f"\n  {ep['method']} {ep['url']}")
        print("  Body: {}\n")
        try:
            result = call(ep["method"], ep["url"], body={})
            print(f"  Response:\n{json.dumps(result, indent=4)}")
        except urllib.error.HTTPError as e:
            print(f"  ✗ {e.code} — {e.read().decode()}")
        except Exception as exc:
            print(f"  ✗ {exc}")

    print("\n" + "=" * 60)
    print("Discovery complete.")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="araiOS agent discovery")
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()

    try:
        discover(args.base_url)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Cannot connect to {args.base_url} — is the server running?", file=sys.stderr)
        sys.exit(1)
