"""Install one coherent, signed Alpine Mesa family using native APK ownership.

The containing graphics archive is authenticated by the runtime. Checksums bind
its manifest to the package/key assets; APK verifies signatures normally. The
additional key is scoped to this transaction, never added to repository trust.
"""

import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

FAMILY = frozenset(
    ("mesa", "mesa-egl", "mesa-gl", "mesa-gles", "mesa-gbm", "mesa-dri-gallium", "mesa-dev")
)


def asset(directory, relative, checksum):
    path = directory / relative
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError("Mesa asset escapes package directory")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != checksum:
        raise ValueError(f"Mesa asset checksum mismatch: {relative}")
    return path.resolve()


def metadata(path):
    # APK v2 concatenates signed gzip/tar members; do not stop after the signature.
    with tarfile.open(path, "r:gz", ignore_zeros=True) as archive:
        entries = [entry for entry in archive if entry.name == ".PKGINFO"]
        if len(entries) != 1 or not entries[0].isfile() or entries[0].size > 1024 * 1024:
            raise ValueError("Invalid Mesa APK metadata")
        fields = {}
        for line in archive.extractfile(entries[0]).read().decode().splitlines():
            if " = " in line:
                name, value = line.split(" = ", 1)
                fields.setdefault(name, []).append(value)
        return fields


def install(directory, repository_keys=Path("/etc/apk/keys")):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text())
    if (
        manifest.get("schema") != 1
        or manifest.get("distribution") != "alpine"
        or manifest.get("architecture") != "aarch64"
        or manifest.get("name") != "mesa"
        or platform.freedesktop_os_release().get("ID") != "alpine"
    ):
        raise ValueError("Incompatible Mesa package manifest")
    version = manifest.get("version", "")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9][a-zA-Z0-9._+-]*-r[0-9]+", version):
        raise ValueError("Invalid Mesa package version")
    key_name = manifest.get("key", "")
    if not isinstance(key_name, str) or not re.fullmatch(r"keys/[a-zA-Z0-9_.-]+\.pub", key_name):
        raise ValueError("Invalid Mesa package key")
    key = asset(directory, key_name, manifest.get("key_sha256"))
    entries = manifest.get("packages")
    if (
        not isinstance(entries, list)
        or len(entries) != len(FAMILY)
        or any(not isinstance(entry, dict) for entry in entries)
        or {entry.get("name") for entry in entries} != FAMILY
    ):
        raise ValueError("Mesa package family is incomplete or duplicated")
    packages = []
    for entry in entries:
        name = entry["name"]
        filename = f"{name}-{version}.apk"
        dependencies = entry.get("dependencies")
        if (
            entry.get("apk") != filename
            or not isinstance(dependencies, list)
            or any(not isinstance(value, str) or not value for value in dependencies)
        ):
            raise ValueError("Invalid Mesa APK filename or dependencies")
        if name != "mesa" and f"mesa={version}" not in dependencies:
            raise ValueError("Mesa sibling is not pinned to the family version")
        for dependency in dependencies:
            dependency_name = re.split(r"[<>=~]", dependency, maxsplit=1)[0]
            if dependency_name in FAMILY and dependency != f"{dependency_name}={version}":
                raise ValueError("Incoherent Mesa family dependency")
        path = asset(directory, filename, entry.get("sha256"))
        fields = metadata(path)
        for field, expected in (
            ("pkgname", [name]),
            ("pkgver", [version]),
            ("arch", ["aarch64"]),
            ("depend", dependencies),
        ):
            if sorted(fields.get(field, [])) != sorted(expected):
                raise ValueError(f"Mesa APK metadata mismatch: {name} {field}")
        packages.append(path)
    architecture = subprocess.run(
        ["apk", "--print-arch"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if architecture != "aarch64":
        raise ValueError("Mesa packages require native aarch64")
    with tempfile.TemporaryDirectory(prefix="sentinel-mesa-keys-") as temporary:
        keys = Path(temporary)
        for trusted in repository_keys.glob("*.pub"):
            shutil.copyfile(trusted, keys / trusted.name)
        destination = keys / key.name
        if destination.exists() and destination.read_bytes() != key.read_bytes():
            raise ValueError("Mesa key conflicts with repository key")
        shutil.copyfile(key, destination)
        # Keep repositories available: initial provisioning may need dependencies.
        # APK owns every installed file and verifies the entire transaction.
        subprocess.run(["apk", "--keys-dir", str(keys), "add", *map(str, packages)], check=True)
    for name in sorted(FAMILY):
        subprocess.run(
            ["apk", "info", "--installed", f"{name}={version}"],
            check=True,
            stdout=subprocess.DEVNULL,
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: install-native-mesa.py PACKAGE_DIRECTORY")
    install(Path(sys.argv[1]))
