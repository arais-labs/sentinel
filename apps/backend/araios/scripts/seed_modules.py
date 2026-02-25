#!/usr/bin/env python3
"""Seed module definitions into araiOS via the API.

Usage:
    python scripts/seed_modules.py [--base-url http://localhost:9000] [--token dev-admin-token] [--force]

Options:
    --base-url  Base URL of araiOS backend (default: http://localhost:9000)
    --token     Admin token (default: dev-admin-token)
    --force     Update existing modules (default: skip if exists)
"""
import argparse
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

MODULES_DIR = Path(__file__).parent.parent / "modules"


def req(method: str, url: str, token: str, body: dict = None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def seed(base_url: str, token: str, force: bool):
    base_url = base_url.rstrip("/")
    files = sorted(MODULES_DIR.glob("*.json"))
    if not files:
        print(f"No JSON files found in {MODULES_DIR}")
        sys.exit(1)

    for path in files:
        data = json.loads(path.read_text())
        name = data["name"]

        status, _ = req("GET", f"{base_url}/api/modules/{name}", token)
        exists = status == 200

        if exists and not force:
            print(f"  skip  {name} (already exists, use --force to update)")
            continue

        if exists and force:
            status, body = req("PATCH", f"{base_url}/api/modules/{name}", token, data)
            verb = "update"
        else:
            status, body = req("POST", f"{base_url}/api/modules", token, data)
            verb = "create"

        if status in (200, 201):
            print(f"  {verb:6}  {name}")
        else:
            print(f"  ERROR   {name}: {status} {str(body)[:200]}")


def main():
    parser = argparse.ArgumentParser(description="Seed araiOS module definitions")
    parser.add_argument("--base-url", default="http://localhost:9000")
    parser.add_argument("--token", default="dev-admin-token")
    parser.add_argument("--force", action="store_true", help="Update existing modules")
    args = parser.parse_args()

    print(f"Seeding modules → {args.base_url}")
    seed(args.base_url, args.token, args.force)
    print("Done.")


if __name__ == "__main__":
    main()
