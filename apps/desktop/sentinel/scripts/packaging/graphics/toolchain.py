"""Checksum-pinned, privately extracted graphics build tools; no host installs."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relocate(root, entries):
    cellar = root / "cellar"
    prefix = root / "prefix"
    (prefix / "opt").mkdir(parents=True, exist_ok=True)
    for entry in entries:
        link = prefix / "opt" / entry["name"]
        if not link.is_symlink():
            link.symlink_to(cellar / entry["name"] / entry["version"])

    def replace(value):
        return (
            value.replace("@@HOMEBREW_CELLAR@@", str(cellar))
            .replace("@@HOMEBREW_PREFIX@@", str(prefix))
            .replace("/opt/homebrew/Cellar", str(cellar))
            .replace("/opt/homebrew/opt/", str(prefix / "opt") + "/")
        )

    for file in sorted(cellar.rglob("*")):
        if not file.is_file() or file.is_symlink():
            continue
        if file.suffix == ".pc":
            text = file.read_text()
            changed = replace(text)
            if changed != text:
                file.chmod(file.stat().st_mode | 0o200)
                file.write_text(changed)
            continue
        with file.open("rb") as stream:
            if stream.read(4) != b"\xcf\xfa\xed\xfe":
                continue
        deps = subprocess.check_output(["otool", "-L", str(file)], text=True)
        args = []
        for line in deps.splitlines()[1:]:
            old = line.strip().split(" (", 1)[0]
            new = replace(old)
            if new != old:
                if not Path(new).exists():
                    raise RuntimeError(f"Unbundled build-tool dependency: {new}")
                args.extend(["-change", old, new])
        if args:
            file.chmod(file.stat().st_mode | 0o200)
            if file.suffix == ".dylib":
                args.extend(["-id", str(file)])
            run("install_name_tool", *args, file)
            run("codesign", "--force", "--sign", "-", file)


def prepare_toolchain(work, manifest):
    entries = json.loads(manifest.read_text())
    key = hashlib.sha256(manifest.read_bytes() + Path(__file__).read_bytes()).hexdigest()
    root = work / "toolchain" / hashlib.sha256(manifest.read_bytes()).hexdigest()[:16]
    marker = root / "ready"
    if marker.is_file() and marker.read_text() == key:
        return root / "prefix"
    cellar = root / "cellar"
    cellar.mkdir(parents=True, exist_ok=True)
    cache = work / "downloads"
    cache.mkdir(exist_ok=True)
    for entry in entries:
        name, sha = entry["name"], entry["sha256"]
        archive = cache / f"{name}-{sha}.tar.gz"
        if not archive.exists():
            token = json.loads(
                subprocess.check_output(
                    [
                        "curl",
                        "-fsSL",
                        "--max-time",
                        "60",
                        f"https://ghcr.io/token?service=ghcr.io&scope=repository:homebrew/core/{name}:pull",
                    ]
                )
            )["token"]
            partial = archive.with_suffix(".download")
            run(
                "curl",
                "-fsSL",
                "--retry",
                "2",
                "--connect-timeout",
                "15",
                "--max-time",
                "900",
                "-H",
                "Authorization: Bearer " + token,
                entry["url"],
                "-o",
                partial,
            )
            if digest(partial) != sha:
                partial.unlink()
                raise RuntimeError(f"{name}: archive checksum mismatch")
            partial.replace(archive)
        if digest(archive) != sha:
            raise RuntimeError(f"{name}: cached archive checksum mismatch")
        target = cellar / name / entry["version"]
        extracted = root / (name + ".extracted")
        if not extracted.is_file():

            def safe_member(member, destination):
                try:
                    return tarfile.data_filter(member, destination)
                except tarfile.LinkOutsideDestinationError:
                    if member.issym():
                        return None
                    raise

            with tarfile.open(archive) as bundle:
                bundle.extractall(cellar, filter=safe_member)
            if not target.is_dir():
                raise RuntimeError(f"{name}: pinned package missing from archive")
            extracted.touch()
    relocate(root, entries)
    marker.write_text(key)
    return root / "prefix"


def build_environment(work, prefix, venv):
    paths = [
        venv / "bin",
        *(prefix / "opt" / name / "bin" for name in ("bison", "llvm", "spirv-tools")),
    ]
    pkg_paths = [work / "local/lib/pkgconfig", work / "pkgconfig"]
    pkg_paths.extend(
        prefix / "opt" / name / "lib/pkgconfig"
        for name in ("spirv-llvm-translator", "spirv-tools", "libclc")
    )
    pkg_paths.append(prefix / "opt/spirv-headers/share/pkgconfig")
    return {
        **os.environ,
        "PATH": ":".join(map(str, paths)) + ":/usr/bin:/bin:/usr/sbin:/sbin",
        "CC": "/usr/bin/clang",
        "CXX": "/usr/bin/clang++",
        "OBJC": "/usr/bin/clang",
        "OBJCXX": "/usr/bin/clang++",
        "SDKROOT": subprocess.check_output(["xcrun", "--show-sdk-path"], text=True).strip(),
        "PKG_CONFIG": str(prefix / "opt/pkgconf/bin/pkgconf"),
        "PKG_CONFIG_PATH": ":".join(map(str, pkg_paths)),
        "BISON_PKGDATADIR": str(prefix / "opt/bison/share/bison"),
    }
