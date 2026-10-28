"""The runtime release a remote Mac installs: the sentineld the desktop app runs.

A release is one tree, uploaded under ROOT/releases/<version>-<id>/sentineld/:
  bin/sentineld (the daemon and, as `sentineld vm`, each workspace VM), bin/sentineld-media,
  lib/ (libkrun, virglrenderer, KosmicKrisp), tools/, share/ (the guest share and firmware),
  distributions/ (the recipes sentineld installs each distribution from)
The tree holds regular files only: a symlink is rejected, never shipped.
The installed manifest records the inventory, so verification never trusts the remote tree's shape.
"""

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat

# What the tree ships, relative to its build directory.
_TREE = "sentineld"
_ENTRIES = (
    "bin/sentineld",
    "bin/sentineld-media",
    "lib",
    "tools/mke2fs",
    "tools/resize2fs",
    "tools/wimlib-imagex",
    "tools/mkntfs",
    "tools/hive",
    "share",
    "distributions",
)
REQUIRED = frozenset(
    {
        "sentineld/bin/sentineld",
        "sentineld/bin/sentineld-media",
        "sentineld/tools/mke2fs",
        "sentineld/tools/resize2fs",
        "sentineld/tools/wimlib-imagex",
        "sentineld/tools/mkntfs",
        "sentineld/tools/hive",
        "sentineld/share/sentineld-agent",
        "sentineld/share/catalog.json",
        "sentineld/lib/libkrun.dylib",  # linked by sentineld for its VMs
        "sentineld/lib/libvulkan_kosmickrisp.dylib",
        "sentineld/share/firmware/SENTINEL_EFI.fd",
    }
)
# Mach-O executables the installer marks executable and checks the signature of.
SIGNED = (
    "sentineld/bin/sentineld",
    "sentineld/bin/sentineld-media",
    "sentineld/tools/mke2fs",
    "sentineld/tools/resize2fs",
    "sentineld/tools/wimlib-imagex",
    "sentineld/tools/mkntfs",
    "sentineld/tools/hive",
)
_NAME = re.compile(r"sentineld/[A-Za-z0-9._+/-]+")


def validate_release_files(names):
    if (
        not isinstance(names, list)
        or len(names) > 4096
        or any(not isinstance(name, str) or not _NAME.fullmatch(name) for name in names)
        or any(part in ("", ".", "..") for name in names for part in name.split("/"))
        or names != sorted(set(names))
    ):
        raise ValueError("Invalid runtime release inventory")
    missing = REQUIRED.difference(names)
    if missing:
        raise ValueError("Incomplete runtime release: missing " + ", ".join(sorted(missing)))
    return names


def release_files(sentineld):
    """The sentineld build tree: [(local path, release name)] sorted by name."""
    directory = Path(sentineld)
    files = {}
    for entry in _ENTRIES:
        top = directory / entry
        if not top.exists():
            raise ValueError(f"Missing runtime build output: {top}")
        # The entry itself too: a symlinked directory is rejected like any other link.
        paths = [top, *sorted(top.rglob("*"))] if top.is_dir() else [top]
        for path in paths:
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode) or "__pycache__" in path.parts:
                continue
            name = str(PurePosixPath(_TREE, path.relative_to(directory).as_posix()))
            if stat.S_ISLNK(mode):
                raise ValueError(f"Runtime release trees must not contain symlinks: {path}")
            if not stat.S_ISREG(mode):
                raise ValueError(f"Runtime release files must be regular files: {path}")
            files[name] = path
    names = validate_release_files(sorted(files))
    return [(files[name], name) for name in names]


def runtime_version(names, hashes):
    """Bind every file name to its bytes."""
    if len(names) != len(hashes):
        raise ValueError("Incomplete runtime asset checksums")
    payload = {"files": list(zip(names, hashes))}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
