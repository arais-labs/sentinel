"""Install bundled distro packages without changing repository trust or pinning.

The owning graphics archive is already verified before extraction. Package and
public-key checksums catch damaged assets; APK still verifies the signature.
Dependencies must be installed from the normal distribution repositories first.
"""

import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess


def install_bundled_deb(name, root=Path("/opt/sentinel/graphics/packages")):
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        raise ValueError("Invalid bundled package name")
    directory = root / name
    manifest = json.loads((directory / "manifest.json").read_text())
    release = platform.freedesktop_os_release()
    if (
        manifest.get("schema") != 1
        or release.get("ID") not in ("debian", "ubuntu")
        or manifest.get("distribution") != release.get("ID")
        or not release.get("VERSION_ID")
        or manifest.get("release") != release["VERSION_ID"]
        or manifest.get("architecture") != "arm64"
        or manifest.get("name") != name
    ):
        raise ValueError("Incompatible bundled DEB manifest")
    version = manifest.get("version", "")
    if not isinstance(version, str) or not re.fullmatch(
        r"(?:[0-9]+:)?[0-9][a-zA-Z0-9.+~\-]*", version
    ):
        raise ValueError("Invalid bundled DEB version")
    filename = f"{name}_{version.split(':')[-1]}_arm64.deb"
    if manifest.get("deb") != filename or not isinstance(manifest.get("dependencies"), str):
        raise ValueError("Invalid bundled DEB filename or dependencies")
    package = directory / filename
    if not package.resolve().is_relative_to(directory.resolve()):
        raise ValueError("Bundled DEB asset escapes its package directory")
    with package.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != manifest.get("sha256"):
            raise ValueError("Bundled DEB asset checksum mismatch")

    def text(*command):
        return subprocess.run(
            list(command), check=True, stdout=subprocess.PIPE, text=True
        ).stdout.strip()

    if text("dpkg", "--print-architecture") != "arm64":
        raise ValueError("Bundled DEB requires native arm64")
    for field, expected in (
        ("Package", name),
        ("Version", version),
        ("Architecture", "arm64"),
        ("Depends", manifest["dependencies"]),
    ):
        if text("dpkg-deb", "--field", str(package), field) != expected:
            raise ValueError(f"Bundled DEB control mismatch: {field}")
    # Dependencies must already exist; never repair failures through APT, force
    # dependency checks, or change repository/pinning configuration here.
    subprocess.run(["dpkg", "--install", str(package.resolve())], check=True)
    installed = text(
        "dpkg-query",
        "--show",
        "--showformat=${Status}\n${Package}\n${Version}\n${Architecture}",
        name,
    )
    if installed != f"install ok installed\n{name}\n{version}\narm64":
        raise ValueError("Bundled DEB installed-state mismatch")


def install_bundled_apk(name, root=Path("/opt/sentinel/graphics/packages")):
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        raise ValueError("Invalid bundled package name")
    directory = root / name
    manifest = json.loads((directory / "manifest.json").read_text())
    if (
        manifest.get("schema") != 1
        or manifest.get("distribution") != "alpine"
        or manifest.get("architecture") != "aarch64"
        or manifest.get("name") != name
    ):
        raise ValueError("Incompatible bundled APK manifest")
    version = manifest.get("version", "")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9][a-zA-Z0-9._+-]*", version):
        raise ValueError("Invalid bundled APK version")
    if manifest.get("apk") != f"{name}-{version}.apk":
        raise ValueError("Invalid bundled APK filename")
    key = manifest.get("key", "")
    if not isinstance(key, str) or not re.fullmatch(r"keys/[a-zA-Z0-9_.-]+\.pub", key):
        raise ValueError("Invalid bundled APK public key")
    for relative, expected in (
        (manifest["apk"], manifest.get("sha256")),
        (key, manifest.get("key_sha256")),
    ):
        path = directory / relative
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Bundled APK asset escapes its package directory")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"Bundled APK asset checksum mismatch: {relative}")
    command = [
        "apk",
        "--keys-dir",
        str(directory / "keys"),
        "--no-network",
        "--repositories-file",
        "/dev/null",
        "add",
    ]
    subprocess.run([*command, str(directory / manifest["apk"])], check=True)
    # Adding an APK filename puts an identity-checksum constraint in apk/world.
    # Normalize only this package through apk itself; preserve every other
    # administrator constraint and permit future distribution security updates.
    subprocess.run([*command, name], check=True)
    subprocess.run(
        ["apk", "info", "--installed", f"{name}={version}"], check=True, stdout=subprocess.DEVNULL
    )
