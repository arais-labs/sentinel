#!/usr/bin/env python3
"""Run disposable native desktop and selected-browser lifecycle/GPU qualification."""

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

DISTRIBUTIONS = ("ubuntu", "debian", "alpine")
DESKTOPS = ("xfce", "lxqt", "gnome", "plasma")
BROWSERS = ("chromium", "firefox", "chrome")
DESKTOP_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_HASH_LOCK = threading.Lock()


def runtime_identity(distribution, cache):
    """Hash live package bytes once per unchanged file, never trust manifest hashes.

    Stat caching detects ordinary edits (including restored mtime), replacement,
    and symlink retargeting. It is a reproducibility check, not protection against
    adversarial metadata spoofing or a change restored between observations.
    """
    configured = (
        os.environ.get("SENTINEL_TEST_RUNTIME") or "build/macos-arm64/runtime/workspace-runtime"
    )
    root = (DESKTOP_ROOT / configured).absolute()
    helper = root / "sentinel-workspace-runtime"
    resources = helper.resolve(strict=True).parent
    manifest_path = root / "manifest.json"
    image_manifest_path = resources / "workspace-images/manifest.json"
    # Serialize cache misses so concurrent rows do not rehash multi-GB blobs.
    with RUNTIME_HASH_LOCK:

        def fingerprint(file):
            resolved = file.resolve(strict=True)
            before = resolved.stat()
            signature = [
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
                before.st_mode,
            ]
            key = (str(resolved), *signature)
            if key not in cache:
                digest = hashlib.sha256()
                with resolved.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                after = resolved.stat()
                if [
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                    after.st_mode,
                ] != signature or file.resolve(strict=True) != resolved:
                    raise OSError(f"Runtime artifact changed while hashing: {file}")
                cache[key] = digest.hexdigest()
            return {"path": str(resolved), "sha256": cache[key], "stat": signature}

        # Fingerprint metadata before parsing and recheck it afterwards.
        metadata = {
            "manifest.json": fingerprint(manifest_path),
            "workspace-images/manifest.json": fingerprint(image_manifest_path),
        }
        manifest = json.loads(manifest_path.read_text())
        images = json.loads(image_manifest_path.read_text())
        entry = images["images"][distribution]
        if (
            entry.get("layout") != distribution
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", entry.get("digest", ""))
            or not entry.get("reference", "").endswith("@" + entry["digest"])
        ):
            raise ValueError("Workspace image must use a pinned distribution layout")
        if not re.search(r"@sha256:[0-9a-f]{64}$", manifest.get("initImage", "")):
            raise ValueError("Runtime init image must be digest-pinned")
        files = {"sentinel-workspace-runtime": helper, "kernel": root / "kernel"}
        for label, directory in (
            ("graphics", resources / "graphics"),
            (
                f"workspace-images/{distribution}",
                resources / "workspace-images" / distribution,
            ),
        ):
            if not directory.is_dir():
                raise FileNotFoundError(f"Missing runtime artifact directory: {directory}")
            found = False
            for file in sorted(directory.rglob("*")):
                if file.is_symlink() and file.is_dir():
                    raise ValueError(f"Nested runtime directory symlink is unsupported: {file}")
                if file.is_file() or file.is_symlink():
                    files[f"{label}/{file.relative_to(directory)}"] = file
                    found = True
            if not found:
                raise ValueError(f"Empty runtime artifact directory: {directory}")
        artifacts = {name: fingerprint(file) for name, file in sorted(files.items())}
        if (
            fingerprint(manifest_path) != metadata["manifest.json"]
            or fingerprint(image_manifest_path) != metadata["workspace-images/manifest.json"]
        ):
            raise OSError("Runtime manifests changed while identifying artifacts")
        return {
            "root": str(root),
            "init_image": manifest["initImage"],
            "workspace_image": entry,
            "artifacts": {**metadata, **artifacts},
        }


def source_identity():
    """Fingerprint executable qualification inputs, not mutable build logs."""
    roots = [
        DESKTOP_ROOT / "tests/fixtures",
        DESKTOP_ROOT / "tests/qualification",
        DESKTOP_ROOT / ".test-dist/main/workspace",
        DESKTOP_ROOT.parents[1]
        / "backend/sentinel/app/services/runtime/guest_commands/linux/browser",
    ]
    files = [DESKTOP_ROOT / "tests/integration/host-desktop.mjs"]
    for root in roots:
        files.extend(
            file
            for file in root.rglob("*")
            if file.is_file() and file.suffix in {".py", ".js", ".mjs", ".html"}
        )
    digest = hashlib.sha256()
    for file in sorted(files):
        digest.update(str(file.relative_to(DESKTOP_ROOT.parents[1])).encode() + b"\0")
        digest.update(file.read_bytes() + b"\0")
    return digest.hexdigest()


def selections(distributions=None, desktops=None, browsers=None):
    rows = []
    for distro in DISTRIBUTIONS:
        if distributions and distro not in distributions:
            continue
        for desktop in DESKTOPS:
            if desktops and desktop not in desktops:
                continue
            for browser in BROWSERS:
                if browsers and browser not in browsers:
                    continue
                unsupported = distro == "alpine" and browser == "chrome"
                if unsupported and not browsers:
                    continue
                row = {
                    "distribution": distro,
                    "desktop": desktop,
                    "browser": browser,
                    "status": "unsupported" if unsupported else "unrun",
                }
                if unsupported:
                    row["error"] = "Google Chrome is supported on Ubuntu and Debian, not Alpine"
                rows.append(row)
    return rows


def report(rows):
    counts = {
        status: sum(row["status"] == status for row in rows)
        for status in ("pass", "fail", "unrun", "unsupported")
    }
    return {
        "schema_version": 2,
        "coverage": "desktop-and-selected-browser-lifecycle-gpu",
        "browser_qualification": "Selected automation lifecycle/GPU/pixels/input/confinement; Firefox selection also requires native Firefox",
        "complete": counts["unrun"] == 0,
        "status": "pass" if rows and counts["pass"] == len(rows) else "fail",
        "counts": counts,
        "rows": rows,
    }


def save_report(destination, rows):
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(report(rows), indent=2) + "\n")
    temporary.replace(destination)


def run_row(
    row,
    root,
    stopped,
    timeout,
    expected_identity=None,
    expected_runtime=None,
    runtime_cache=None,
):
    result = dict(row)
    if stopped.is_set() or row["status"] == "unsupported":
        return result
    evidence = root / row["distribution"] / row["desktop"] / row["browser"]
    evidence.mkdir(parents=True)
    result.update(evidence=str(evidence), started_at=datetime.now(timezone.utc).isoformat())
    started = time.monotonic()
    environment = dict(os.environ)
    environment.update(
        SENTINEL_TEST_DISTRIBUTION=row["distribution"],
        SENTINEL_TEST_DESKTOP=row["desktop"],
        SENTINEL_TEST_BROWSER=row["browser"],
        SENTINEL_DESKTOP_SCREENSHOT=str(evidence / "desktop.png"),
        SENTINEL_DESKTOP_EVIDENCE_DIR=str(evidence),
    )
    # Diagnostic modes must never be inherited into qualification.
    environment.pop("SENTINEL_DESKTOP_DIAGNOSE_CLIPBOARD", None)
    command = ["node", "tests/integration/host-desktop.mjs"]
    result["command"] = command
    process = None
    try:
        result["source_identity"] = source_identity()
        if expected_identity is not None and result["source_identity"] != expected_identity:
            result.update(status="fail", error="Qualification inputs changed since run started")
            return result
        runtime_cache = runtime_cache if runtime_cache is not None else {}
        result["runtime_identity"] = runtime_identity(row["distribution"], runtime_cache)
        if expected_runtime is not None and result["runtime_identity"] != expected_runtime:
            result.update(status="fail", error="Runtime artifacts changed since run started")
            return result
        with (evidence / "console.log").open("wb") as output:
            process = subprocess.Popen(
                command,
                cwd=DESKTOP_ROOT,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            reason = None
            while process.poll() is None:
                if stopped.is_set() or time.monotonic() - started >= timeout:
                    reason = "interrupted" if stopped.is_set() else "timeout"
                    # The adapter handles SIGTERM and removes its owned VM/files.
                    process.terminate()
                    try:
                        process.wait(timeout=60)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                        reason += "; forced termination; inspect runtime cleanup"
                    break
                stopped.wait(0.25)
            result.update(
                status="pass" if process.returncode == 0 and reason is None else "fail",
                exit_code=process.returncode,
            )
            if reason:
                result["error"] = reason
            if source_identity() != result["source_identity"]:
                result.update(
                    status="fail",
                    error="Qualification inputs changed while row was running",
                )
            if runtime_identity(row["distribution"], runtime_cache) != result["runtime_identity"]:
                result.update(
                    status="fail",
                    error="Runtime artifacts changed while row was running",
                )
    except (OSError, ValueError, KeyError) as error:
        result.update(status="fail", error=str(error))
    finally:
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        (evidence / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list", action="store_true", help="List selected rows without starting VMs"
    )
    parser.add_argument("--distribution", choices=DISTRIBUTIONS, action="append")
    parser.add_argument("--desktop", choices=(*DESKTOPS, "kde"), action="append")
    parser.add_argument("--browser", choices=BROWSERS, action="append")
    parser.add_argument(
        "--jobs",
        type=int,
        choices=range(1, 4),
        default=1,
        help="Concurrent disposable VMs, 1–3 (default: serial)",
    )
    parser.add_argument("--timeout", type=int, default=1800, help="Seconds per row (default: 1800)")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DESKTOP_ROOT / "build/qualification",
        help="Parent for a new unique evidence directory",
    )
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    desktops = ["plasma" if name == "kde" else name for name in args.desktop or []]
    rows = selections(args.distribution, desktops, args.browser)
    if args.list:
        print(json.dumps(report(rows), indent=2))
        return 0
    args.output_root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="run-", dir=args.output_root)).resolve()
    destination = root / "report.json"
    save_report(destination, rows)
    print(
        f"Desktop and selected-browser lifecycle qualification evidence: {root}",
        flush=True,
    )
    stopped = threading.Event()
    expected_identity = source_identity()
    runtime_cache = {}
    expected_runtimes = {}
    for distribution in sorted(
        {row["distribution"] for row in rows if row["status"] != "unsupported"}
    ):
        try:
            expected_runtimes[distribution] = runtime_identity(distribution, runtime_cache)
        except (OSError, ValueError, KeyError) as error:
            for row in rows:
                if row["status"] != "unsupported":
                    row.update(
                        status="fail",
                        error=f"Cannot identify runtime artifacts: {error}",
                    )
            save_report(destination, rows)
            print(f"Cannot identify runtime artifacts: {error}", flush=True)
            return 1
    previous = {}

    def interrupt(_number, _frame):
        stopped.set()

    for number in (signal.SIGINT, signal.SIGTERM):
        previous[number] = signal.signal(number, interrupt)
    try:
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            futures = {
                executor.submit(
                    run_row,
                    row,
                    root,
                    stopped,
                    args.timeout,
                    expected_identity,
                    expected_runtimes.get(row["distribution"]),
                    runtime_cache,
                ): index
                for index, row in enumerate(rows)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    rows[index] = future.result()
                except Exception as error:
                    rows[index] = {**rows[index], "status": "fail", "error": str(error)}
                save_report(destination, rows)
                row = rows[index]
                print(
                    f"{row['distribution']}/{row['desktop']}/{row['browser']}: {row['status']}",
                    flush=True,
                )
    finally:
        save_report(destination, rows)
        for number, handler in previous.items():
            signal.signal(number, handler)
    print(f"Report: {destination}", flush=True)
    return 130 if stopped.is_set() else (0 if report(rows)["status"] == "pass" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
