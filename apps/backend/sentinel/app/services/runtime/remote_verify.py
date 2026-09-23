"""On-demand, read-only checks of an enrolled remote runtime.

No service starts, workspace boots, or repairs happen here. The installed release
hash covers the helper, kernel, graphics assets and bundled native OCI image
files; it does not prove that a guest can boot or render a desktop.
"""

import asyncio
import json
import re
from pathlib import PurePosixPath
from shlex import quote

import asyncssh

from app.services.runtime.remote_mac import (
    RemoteMacError,
    RuntimeUnavailable,
    check_maintenance,
    runtime_assets,
    service_request,
)
from app.services.runtime.ssh_client import SSHClient
from app.services.runtime.workspace_image_assets import runtime_version


async def verify_installation(machine):
    check_maintenance(machine)
    client = SSHClient(machine.credentials())
    checks = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    def result():
        return {"checks": checks}

    try:
        conn = await client._ensure_conn()
        async with conn.start_sftp_client() as sftp:

            async def read(name):
                try:
                    async with sftp.open(machine.runtime_root + "/" + name) as file:
                        value = json.loads(await file.read())
                        if not isinstance(value, dict):
                            raise ValueError("Expected a JSON object")
                        return value
                except asyncssh.SFTPNoSuchFile:
                    return {}

            try:
                manifest = await read("manifest.json")
                journal = await read("update.json")
                root = PurePosixPath(machine.runtime_root)
                executable = PurePosixPath(manifest["executable"])
                release = executable.parent
                if (
                    not root.is_absolute()
                    or ".." in executable.parts
                    or ".." in root.parts
                    or any(ord(c) < 32 for c in str(executable) + str(root))
                    or release.parent != root / "releases"
                    or executable.name != "sentinel-workspace-runtime"
                    or manifest["kernel"] != str(release / "kernel")
                    or not re.fullmatch(r"[a-f0-9]{16}", manifest["version"])
                    or not isinstance(manifest["initImage"], str)
                ):
                    raise ValueError("Invalid release metadata")
                files = runtime_assets(manifest, installed=True)
            except (KeyError, TypeError, ValueError) as exc:
                add(
                    "Installation metadata",
                    "failed",
                    f"Missing or invalid installation metadata: {exc}",
                )
                return result()

            phase = journal.get("phase")
            if phase and phase not in {"complete", "rolled_back"}:
                add(
                    "Update state",
                    "warning",
                    "An update is incomplete. Recover it before verifying the runtime.",
                )
                return result()
            add(
                "Installation metadata",
                "passed",
                "Installed release metadata is readable.",
            )

            paths = [str(release / name) for _, name in files]
            # Bound argv size even when OCI images acquire many small layers.
            lines = []
            checksums_ok = True
            for offset in range(0, len(paths), 64):
                hashed = await conn.run(
                    "/usr/bin/shasum -a 256 " + " ".join(map(quote, paths[offset : offset + 64])),
                    check=False,
                    timeout=35,
                )
                lines.extend(hashed.stdout.splitlines())
                checksums_ok = checksums_ok and hashed.exit_status == 0
            hashes = [line.split()[0] for line in lines if line.split()]
            valid_hashes = len(hashes) == len(paths) and all(
                re.fullmatch(r"[a-f0-9]{64}", value) for value in hashes
            )
            version = (
                runtime_version([name for _, name in files], hashes, manifest["initImage"])
                if valid_hashes
                else None
            )
            if not checksums_ok or not valid_hashes or version != manifest["version"]:
                add(
                    "Runtime files",
                    "failed",
                    "Runtime files are missing, unreadable, or differ from the installed release. Reinstall the runtime to replace them.",
                )
                return result()
            add(
                "Runtime files",
                "passed",
                "Helper, kernel, graphics and bundled native Linux images match the installed release checksum.",
            )

            for label, path in (
                ("Runtime helper", str(executable)),
                (
                    "Graphics renderer",
                    str(release / "graphics/sentinel-desktop-renderer"),
                ),
            ):
                verified = await conn.run(
                    f"/bin/test -x {quote(path)} && /usr/bin/codesign --verify --strict {quote(path)}",
                    check=False,
                    timeout=10,
                )
                if verified.exit_status != 0:
                    add(
                        label,
                        "failed",
                        "Executable permission or code signature check failed. Reinstall the runtime to replace it.",
                    )
                else:
                    add(
                        label,
                        "passed",
                        "Executable permission and code signature verified.",
                    )
            if any(check["status"] == "failed" for check in checks):
                return result()

            try:
                status = await asyncio.wait_for(
                    service_request(conn, manifest, str(root), "status"), 10
                )
                running = sum(value == "running" for value in status.get("states", {}).values())
                add(
                    "Runtime service",
                    "passed",
                    f"Responded to a read-only status request; {running} running workspace(s).",
                )
            except RuntimeUnavailable:
                add(
                    "Runtime service",
                    "warning",
                    "The runtime is stopped. Verification did not start it; connect to a workspace to check startup.",
                )
            except (RemoteMacError, TimeoutError) as exc:
                add(
                    "Runtime service",
                    "failed",
                    f"The runtime did not respond successfully: {str(exc) or 'status request timed out'}. Verification did not restart it.",
                )

            # An update from another desktop can overlap these read-only checks.
            if await read("manifest.json") != manifest or await read("update.json") != journal:
                checks.clear()
                add(
                    "Installation changed",
                    "warning",
                    "The installation changed during verification. Run verification again after the update finishes.",
                )
            return result()
    finally:
        await client.close()
