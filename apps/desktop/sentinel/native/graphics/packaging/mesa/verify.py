"""Verify native-prefix compiler staging and exact signed APK family metadata."""

import hashlib
from pathlib import Path
import tarfile

VERSION = "26.1.6-r1"
FAMILY = ("mesa", "mesa-egl", "mesa-gl", "mesa-gles", "mesa-gbm", "mesa-dri-gallium", "mesa-dev")
REQUIRED = (
    "libEGL.so.1",
    "libGL.so.1",
    "libGLESv1_CM.so.1",
    "libGLESv2.so.2",
    "libgbm.so.1",
    "libgallium-26.1.6.so",
    "gbm/dri_gbm.so",
    "dri/virtio_gpu_dri.so",
)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_stage(stage):
    stage = Path(stage).resolve(strict=True)
    if stage == Path("/") or (stage / "opt").exists() or (stage / "etc").exists():
        raise ValueError("Expected isolated native-prefix Mesa staging")
    for name in REQUIRED:
        path = stage / "usr/lib" / name
        if not path.is_file() or not path.resolve().is_relative_to(stage):
            raise ValueError(f"Missing library or escaping link: {name}")
        with path.open("rb") as stream:
            header = stream.read(20)
        if header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(header[18:20], "little") != 183:
            raise ValueError(f"Expected AArch64 shared library: {name}")
    for path in (stage / "usr/lib").rglob("*"):
        if path.is_symlink() and (
            Path(path.readlink()).is_absolute() or not path.resolve().is_relative_to(stage)
        ):
            raise ValueError(f"Non-native staging symlink: {path}")
    for name in (
        "EGL/egl.h",
        "GL/gl.h",
        "GLES/gl.h",
        "GLES2/gl2.h",
        "GLES3/gl3.h",
        "KHR/khrplatform.h",
        "gbm.h",
    ):
        if not (stage / "usr/include" / name).is_file():
            raise ValueError(f"Missing development header: {name}")
    for name in ("dri", "egl", "gl", "glx", "gbm", "glesv1_cm", "glesv2"):
        path = stage / "usr/lib/pkgconfig" / (name + ".pc")
        if not path.is_file() or "prefix=/usr\n" not in path.read_text():
            raise ValueError(f"Missing native pkg-config file: {name}")


def package_metadata(archive, name):
    with tarfile.open(archive, "r:gz", ignore_zeros=True) as contents:
        fields = {}
        for line in contents.extractfile(".PKGINFO").read().decode().splitlines():
            if " = " in line:
                key, value = line.split(" = ", 1)
                fields.setdefault(key, []).append(value)
        for key, value in {
            "pkgname": name,
            "pkgver": VERSION,
            "origin": "mesa",
            "arch": "aarch64",
        }.items():
            if fields.get(key) != [value]:
                raise ValueError(f"Invalid {name} {key}")
        dependencies = fields.get("depend", [])
        if name != "mesa" and f"mesa={VERSION}" not in dependencies:
            raise ValueError(f"Missing exact Mesa family dependency: {name}")
        if name == "mesa-egl" and f"mesa-gles={VERSION}" not in dependencies:
            raise ValueError("Missing EGL/GLES family dependency")
        if name == "mesa-dev" and any(
            f"{sibling}={VERSION}" not in dependencies for sibling in FAMILY if sibling != name
        ):
            raise ValueError("Missing development/runtime family dependency")
        prefixes = ("pc:",) if name == "mesa-dev" else ("so:",)
        if fields.get("replaces") or any(
            not item.startswith(prefixes) for item in fields.get("provides", [])
        ):
            raise ValueError("Native packages must not fake ownership or capabilities")
        for member in contents.getmembers():
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or path.parts[:1] in [("opt",), ("etc",)]:
                raise ValueError(f"Invalid native APK path: {member.name}")
    return dependencies
