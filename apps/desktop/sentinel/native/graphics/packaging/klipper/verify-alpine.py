"""Check the complete native owner package against the signed distro baseline."""

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile


def metadata(archive):
    value = subprocess.check_output(["tar", "-xOzf", str(archive), ".PKGINFO"], text=True)
    fields = {}
    for line in value.splitlines():
        if " = " in line:
            name, item = line.split(" = ", 1)
            fields.setdefault(name, []).append(item)
    return fields


def libraries(archive):
    expected = {}
    for name in (
        "batterycontrol",
        "kfontinst",
        "kfontinstui",
        "klipper",
        "klookandfeel",
        "kmpris",
        "kworkspace6",
        "notificationmanager",
        "taskmanager",
    ):
        soname = "1" if name == "notificationmanager" else "6"
        real = f"lib{name}.so.6.6.6"
        expected[f"usr/lib/{real}"] = (False, f"lib{name}.so.{soname}")
        expected[f"usr/lib/lib{name}.so.{soname}"] = (True, real)
    # APK v2 has concatenated signature/control/data gzip members. Continue
    # through tar padding as well; never extract archive paths onto the host.
    with tarfile.open(archive, "r:gz", ignore_zeros=True) as contents:
        members = [
            entry
            for entry in contents.getmembers()
            if not entry.isdir()
            and entry.name != ".PKGINFO"
            and not entry.name.startswith(".SIGN.")
        ]
        payload = {entry.name for entry in members}
        if len(members) != len(payload) or payload != set(expected):
            raise ValueError(
                f"Native owner payload changed or duplicated: {payload ^ set(expected)}"
            )
        with tempfile.TemporaryDirectory(prefix="klipper-elf-check-") as temporary:
            for entry in members:
                is_link, target = expected[entry.name]
                if is_link:
                    if not entry.issym() or entry.linkname != target:
                        raise ValueError(f"Wrong SONAME symlink: {entry.name}")
                    continue
                if not entry.isfile():
                    raise ValueError(f"Expected regular library file: {entry.name}")
                binary = contents.extractfile(entry).read()
                if (
                    len(binary) < 64
                    or binary[:6] != b"\x7fELF\x02\x01"
                    or int.from_bytes(binary[16:18], "little") != 3
                    or int.from_bytes(binary[18:20], "little") != 183
                ):
                    raise ValueError(f"Expected aarch64 shared ELF: {entry.name}")
                staged = Path(temporary) / Path(entry.name).name
                staged.write_bytes(binary)
                dynamic = subprocess.check_output(["readelf", "--dynamic", str(staged)], text=True)
                if re.findall(r"\(SONAME\).*?\[([^\]]+)\]", dynamic) != [target]:
                    raise ValueError(f"Wrong ELF SONAME: {entry.name}")
    return payload


def verify(directory, baseline):
    package = directory / "plasma-workspace-libs-6.6.6-r1.apk"
    original, current = metadata(baseline), metadata(package)
    for name, expected in {
        "pkgname": "plasma-workspace-libs",
        "pkgver": "6.6.6-r1",
        "arch": "aarch64",
        "origin": "plasma-workspace",
    }.items():
        if current.get(name) != [expected]:
            raise ValueError(f"Unexpected {name}: {current.get(name)}")
    for name in ("license", "provides", "depend", "replaces"):
        if set(current.get(name, [])) != set(original.get(name, [])):
            raise ValueError(f"Native package {name} changed; review ABI/dependency closure")
    if libraries(package) != libraries(baseline):
        raise ValueError("Native package ownership changed")
    key = directory / "keys/sentinel-klipper.rsa.pub"
    manifest = dict(
        schema=1,
        distribution="alpine",
        architecture="aarch64",
        name="plasma-workspace-libs",
        version="6.6.6-r1",
        apk=package.name,
        sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
        dependencies=current.get("depend", []),
        key=str(key.relative_to(directory)),
        key_sha256=hashlib.sha256(key.read_bytes()).hexdigest(),
        qualified=False,
    )
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    verify(*(Path(value).resolve() for value in sys.argv[1:]))
