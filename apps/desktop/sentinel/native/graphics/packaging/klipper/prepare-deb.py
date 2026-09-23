"""Prepare pinned distro source for the isolated Klipper CMake target build.

This does not install dependencies, build code or change a workspace. The source
DSC, distro patch archive and upstream archive are all checksum-pinned. Keep this
component's sources/build directories outside the shared Mesa build identity.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(command, **kwargs):
    subprocess.run(command, check=True, stdout=sys.stderr, **kwargs)


def prepare(distribution, inputs, patch, work):
    pin = json.loads((inputs / "sources.lock.json").read_text())[distribution]
    patches = [patch, inputs / "register-startup-test.patch"]
    if distribution == "debian":
        patches.append(inputs / "debian-arm64-symbols.patch")
    regression = inputs / "startuprestoretest.cpp"
    additions = [*patches, regression]
    identity = hashlib.sha256(
        json.dumps(pin, sort_keys=True).encode()
        + Path(__file__).read_bytes()
        + b"".join(p.name.encode() + b"\0" + p.read_bytes() for p in additions)
    ).hexdigest()
    work.mkdir(parents=True, exist_ok=True)
    target = work / f"source-{identity}"
    if target.is_dir():
        if (target / ".sentinel-source").read_text().strip() != identity:
            raise ValueError("Prepared source identity does not match its directory")
        return target
    cache = work / "downloads"
    cache.mkdir(exist_ok=True)
    for name, expected in pin["files"].items():
        if Path(name).name != name or not name.startswith("plasma-workspace_"):
            raise ValueError("Invalid source archive filename")
        destination = cache / name
        if destination.is_file() and sha(destination) == expected:
            continue
        # An interrupted download cannot masquerade as a verified source file.
        with tempfile.TemporaryDirectory(prefix="download-", dir=cache) as temporary:
            staged = Path(temporary) / name
            run(
                [
                    "curl",
                    "--fail",
                    "--location",
                    "--retry",
                    "2",
                    "--connect-timeout",
                    "15",
                    "--max-time",
                    "180",
                    pin["baseUrl"] + name,
                    "-o",
                    str(staged),
                ]
            )
            if sha(staged) != expected:
                raise ValueError(f"Source checksum mismatch: {name}")
            staged.replace(destination)
    descriptors = [cache / name for name in pin["files"] if name.endswith(".dsc")]
    if len(descriptors) != 1:
        raise ValueError("Expected exactly one pinned distro source descriptor")
    with tempfile.TemporaryDirectory(prefix="prepare-", dir=work) as temporary:
        staged = Path(temporary) / "source"
        # Integrity is anchored in the reviewed lock, including the DSC itself;
        # do not rely on whatever uploader keys happen to be in the build VM.
        # Every input archive was independently SHA256-verified above.
        run(["dpkg-source", "--no-check", "-x", str(descriptors[0]), str(staged)])
        version = subprocess.check_output(
            ["dpkg-parsechangelog", "-l", str(staged / "debian/changelog"), "-S", "Version"],
            text=True,
        ).strip()
        if version != pin["sourceVersion"]:
            raise ValueError(f"Distro source version mismatch: {version}")
        for change in patches:
            run(["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(change)], cwd=staged)
        shutil.copy2(regression, staged / "klipper/autotests/startuprestoretest.cpp")
        (staged / ".sentinel-source").write_text(identity + "\n")
        staged.rename(target)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("distribution", choices=("ubuntu", "debian"))
    parser.add_argument("--inputs", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    print(
        prepare(args.distribution, args.inputs.resolve(), args.patch.resolve(), args.work.resolve())
    )
