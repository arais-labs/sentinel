"""Deterministic native OCI inventory for local staging and remote verification."""

import hashlib
import json
from pathlib import Path
import re
import stat

_DISTRIBUTIONS = ("alpine", "debian", "ubuntu")
_IMAGE_FILE = re.compile(
    r"workspace-images/(?:manifest\.json|(?:alpine|debian|ubuntu)/"
    r"(?:index\.json|oci-layout|blobs/sha256/[a-f0-9]{64}))"
)


def validate_image_files(names):
    if (
        not isinstance(names, list)
        or not names
        or len(names) > 4096
        or any(not isinstance(name, str) or not _IMAGE_FILE.fullmatch(name) for name in names)
        or names != sorted(set(names))
    ):
        raise ValueError("Invalid native workspace image inventory")
    required = {"workspace-images/manifest.json"}
    for distribution in _DISTRIBUTIONS:
        prefix = f"workspace-images/{distribution}/"
        required.update((prefix + "index.json", prefix + "oci-layout"))
        if not any(name.startswith(prefix + "blobs/sha256/") for name in names):
            raise ValueError(f"Missing native {distribution} image blobs")
    if not required.issubset(names):
        raise ValueError("Incomplete native workspace image inventory")
    return names


def local_image_files(runtime_directory):
    directory = Path(runtime_directory) / "workspace-images"
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise ValueError("Native workspace images must be a real directory")
    names = []
    for path in sorted(directory.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise ValueError(
                "Native workspace image assets must not contain links or special files"
            )
        if stat.S_ISREG(mode):
            names.append(path.relative_to(runtime_directory).as_posix())
    return validate_image_files(names)


def runtime_version(names, hashes, init_image):
    """Bind filenames and bytes, including the complete native-image inventory."""
    if len(names) != len(hashes):
        raise ValueError("Incomplete runtime asset checksums")
    payload = {"files": list(zip(names, hashes)), "initImage": init_image}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
