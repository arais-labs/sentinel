"""Install the verified, strictly confined graphics content provider for Chromium."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path("/opt/sentinel/browser-graphics")
PROVIDER = "sentinel-gpu-2404"
PLUG = "chromium:gpu-2404"
SLOT = PROVIDER + ":gpu-2404"


def snap(*arguments):
    result = subprocess.run(
        ["snap", *arguments],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "LC_ALL": "C"},
        timeout=180,
    )
    if result.returncode:
        raise RuntimeError(f"Snap {arguments[0]} failed: {result.stderr[-8192:]}")
    return result.stdout


def installed(version):
    try:
        return any(
            fields[:2] == [PROVIDER, version]
            for fields in map(str.split, snap("list", PROVIDER).splitlines())
        )
    except RuntimeError:
        return False


def connections():
    return [
        fields[2]
        for fields in map(str.split, snap("connections", "chromium").splitlines())
        if len(fields) >= 3 and fields[1] == PLUG and fields[2] != "-"
    ]


def checked(digest, version):
    try:
        receipt = json.loads((ROOT / "installed.json").read_text())
        return (
            receipt == {"sha256": digest, "version": version}
            and installed(version)
            and connections() == [SLOT]
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return False


def receive(stream, destination, size, digest):
    checksum = hashlib.sha256()
    with destination.open("wb") as output:
        while size:
            chunk = stream.read(min(size, 65536))
            if not chunk:
                raise RuntimeError("Browser graphics transfer interrupted")
            output.write(chunk)
            checksum.update(chunk)
            size -= len(chunk)
    if checksum.hexdigest() != digest:
        raise RuntimeError("Browser graphics checksum mismatch")


def main():
    mode, digest, version, *arguments = sys.argv[1:]
    if not re.fullmatch("[a-f0-9]{64}", digest) or not re.fullmatch(
        "[A-Za-z0-9.+~-]{1,32}", version
    ):
        raise ValueError("Invalid browser graphics identity")
    if mode == "check":
        return 0 if checked(digest, version) else 1
    if mode != "install" or len(arguments) != 1 or not 0 < int(arguments[0]) <= 1_073_741_824:
        raise ValueError("Invalid browser graphics transfer")
    ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".install-", dir=ROOT) as temporary:
        archive = Path(temporary) / "provider.snap"
        receive(sys.stdin.buffer, archive, int(arguments[0]), digest)
        # --dangerous accepts our checksum-verified local artifact; it does not
        # change the provider's strict confinement or the browser's sandbox.
        if not installed(version):
            snap("install", "--dangerous", str(archive))
        if not installed(version):
            raise RuntimeError("Installed browser graphics version differs")
        if connections() != [SLOT]:
            if connections():
                snap("disconnect", PLUG)
            snap("connect", PLUG, SLOT)
        if connections() != [SLOT]:
            raise RuntimeError("Chromium graphics provider connection differs")
        receipt = Path(temporary) / "installed.json"
        receipt.write_text(json.dumps({"sha256": digest, "version": version}) + "\n")
        receipt.replace(ROOT / "installed.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
