"""Fetch standalone ANGLE libraries independently of the Electron app version."""

import hashlib
import shutil
import subprocess
import zipfile
from pathlib import Path


def prepare_angle(work: Path, config: dict) -> Path:
    """Verify the pinned archive before extracting only libraries and licenses."""
    digest = config["sourceSha256"]
    folder = work / "angle" / digest
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / "source.zip"
    if not archive.exists():
        partial = folder / "source.download"
        subprocess.run(
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
                "--silent",
                "--show-error",
                config["sourceUrl"],
                "-o",
                str(partial),
            ],
            check=True,
        )
        with partial.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                partial.unlink()
                raise RuntimeError("ANGLE archive checksum mismatch")
        partial.replace(archive)
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
            archive.unlink()
            raise RuntimeError(
                "ANGLE archive checksum mismatch; retry to download it again"
            )
    prefix = config["archivePrefix"]
    members = {
        "libEGL.dylib": prefix + "/libEGL.dylib",
        "libGLESv2.dylib": prefix + "/libGLESv2.dylib",
        "ANGLE-LICENSE": "LICENSE",
        "ANGLE-LICENSES.chromium.html": "LICENSES.chromium.html",
    }
    with zipfile.ZipFile(archive) as bundle:
        # Check the complete set before copying. Never extract arbitrary archive paths.
        for member in members.values():
            bundle.getinfo(member)
        for name, member in members.items():
            with bundle.open(member) as src, (folder / name).open("wb") as dest:
                shutil.copyfileobj(src, dest)
            (folder / name).chmod(0o755 if name.endswith(".dylib") else 0o644)
    return folder
