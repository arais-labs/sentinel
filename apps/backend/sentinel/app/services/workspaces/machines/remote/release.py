"""The runtime release a remote Mac installs: sentineld and the workspace VM process the desktop app runs.

A release is two trees, uploaded under ROOT/releases/<version>-<id>/:
  sentineld/           bin/sentineld, bin/sentineld-media, tools/{mke2fs,resize2fs}, share/ (the guest share)
  sentinel-workspace/  bin/sentinel-workspace, lib/ (libkrun, virglrenderer, KosmicKrisp), share/firmware/
sentineld installs each distribution itself, from the share's recipes.
Both trees hold regular files only: a symlink is rejected, never shipped.
The installed manifest records the inventory, so verification never trusts the remote tree's shape.
"""

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat

# What each tree ships, relative to its build directory.
_TREES = {
    "sentineld": (
        "bin/sentineld",
        "bin/sentineld-media",
        "tools/mke2fs",
        "tools/resize2fs",
        "tools/wimlib-imagex",
        "tools/mkntfs",
        "tools/hive",
        "share",
    ),
    "sentinel-workspace": ("bin/sentinel-workspace", "lib", "share/firmware"),
}
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
        "sentinel-workspace/bin/sentinel-workspace",
        "sentinel-workspace/lib/libkrun.dylib",  # linked by the workspace binary
        "sentinel-workspace/lib/libvulkan_kosmickrisp.dylib",
        "sentinel-workspace/share/firmware/SENTINEL_EFI.fd",
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
    "sentinel-workspace/bin/sentinel-workspace",
)
_NAME = re.compile(r"(?:sentineld|sentinel-workspace)/[A-Za-z0-9._+/-]+")


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


def release_files(sentineld, workspace):
    """The two build trees: [(local path, release name)] sorted by name."""
    files = {}
    for tree, directory in (
        ("sentineld", Path(sentineld)),
        ("sentinel-workspace", Path(workspace)),
    ):
        for entry in _TREES[tree]:
            top = directory / entry
            if not top.exists():
                raise ValueError(f"Missing runtime build output: {top}")
            # The entry itself too: a symlinked directory is rejected like any other link.
            paths = [top, *sorted(top.rglob("*"))] if top.is_dir() else [top]
            for path in paths:
                mode = path.lstat().st_mode
                if stat.S_ISDIR(mode) or "__pycache__" in path.parts:
                    continue
                name = str(PurePosixPath(tree, path.relative_to(directory).as_posix()))
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
