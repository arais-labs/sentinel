"""Qualify the shipped automation workers inside an isolated Linux workspace.

Arguments: start.py stop.py [display] [selection]. Requires the provisioned browser and user.
The caller stages the production sources; this fixture does not install packages.
"""

import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import urllib.request


def main():
    start, stop = sys.argv[1:3]
    selection = Path("/etc/sentinel/browser-selection").read_text().strip()
    assert selection in {"chromium", "firefox", "chrome"}, selection
    if len(sys.argv) > 4:
        assert selection == sys.argv[4], (selection, sys.argv[4])
    automation = "chrome" if selection == "chrome" else "chromium"
    control = Path("/var/lib/sentinel/control")
    control.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="qualification-", dir=control))
    request = {
        "browser": str(root / "browser"),
        "runtime": str(root / "runtime"),
        "logs": str(root / "logs"),
        "display": sys.argv[3] if len(sys.argv) > 3 else "",
    }
    metadata_path = root / "runtime/browser.json"
    descriptor = None
    stopped = False

    def invoke(worker):
        return subprocess.run(
            [sys.executable, worker, json.dumps(request)],
            text=True,
            capture_output=True,
            timeout=40,
        )

    def success(worker):
        result = invoke(worker)
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload.get("ok") is True, payload
        return payload

    try:
        first = success(start)
        assert not first["reused"], first
        assert first["uid"] != 0, first
        descriptor = os.pidfd_open(first["pid"])
        process = Path(f"/proc/{first['pid']}")
        executable = str((process / "exe").resolve())
        assert (
            "/opt/google/chrome/" in executable
            if automation == "chrome"
            else "chromium" in executable
        ), (automation, executable)
        assert process.stat().st_uid == first["uid"]
        assert b"--no-sandbox" not in (process / "cmdline").read_bytes()
        confinement = None
        if str((process / "exe").resolve()).startswith("/snap/"):
            status = dict(
                line.split(":", 1)
                for line in (process / "status").read_text().splitlines()
                if ":" in line
            )
            confinement = {
                "apparmor": (process / "attr/current").read_text().strip(),
                "seccomp": int(status["Seccomp"]),
            }
            assert confinement["apparmor"] == "snap.chromium.chromium (enforce)", confinement
            assert confinement["seccomp"] == 2, confinement
        with urllib.request.urlopen(
            f"http://127.0.0.1:{first['port']}/json/version", timeout=5
        ) as response:
            assert json.load(response)["webSocketDebuggerUrl"]
        reused = success(start)
        assert reused["reused"] and reused["pid"] == first["pid"], reused

        # A stale saved PID must not kill a different process or erase evidence.
        saved = metadata_path.read_text()
        altered = json.loads(saved)
        altered["process_identity"]["start_ticks"] = "0"
        metadata_path.write_text(json.dumps(altered))
        try:
            rejected = invoke(stop)
            assert rejected.returncode != 0, rejected.stdout
            assert metadata_path.exists(), "Rejected stop erased process metadata"
            poller = select.poll()
            poller.register(descriptor, select.POLLIN)
            assert not poller.poll(0), "Rejected stop terminated the browser"
        finally:
            metadata_path.write_text(saved)

        success(stop)
        assert poller.poll(1000), "Worker returned success without browser exit"
        assert not metadata_path.exists()
        stopped = True
        print(
            json.dumps(
                {
                    "gate": "browser-lifecycle",
                    "status": "pass",
                    "uid": first["uid"],
                    "display": request["display"],
                    "selection": selection,
                    "automation": automation,
                    "executable": executable,
                    "snap_confinement": confinement,
                    "checks": [
                        "launch",
                        "cdp",
                        "regular-user",
                        "reuse",
                        "stale-identity-refusal",
                        "confirmed-exit",
                    ],
                }
            )
        )
    except BaseException:
        for log in (root / "logs").glob("*.log"):
            print(
                f"--- {log.name} ---\n{log.read_text(errors='replace')[-24000:]}",
                file=sys.stderr,
                flush=True,
            )
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if not stopped and metadata_path.exists():
            # Preserve logs and metadata if cleanup fails; never hide the failure.
            success(stop)
        if stopped:
            shutil.rmtree(root)


if __name__ == "__main__":
    main()
