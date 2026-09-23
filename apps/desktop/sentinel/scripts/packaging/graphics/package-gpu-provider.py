#!/usr/bin/env python3
"""Package unchanged Mesa into a strict content snap; run in Linux arm64 builder.

Uses the reviewed SHA256-locked Noble dependency closure and snap pack.
No packages installed, no VM operations, no Mesa compilation, no host library copying.
"""

import argparse
import concurrent.futures
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile

# Consumer API/support roots: these may have been pruned from the application
# by gpu-2404-cleanup even when our particular Mesa build does not link them.
# https://github.com/canonical/gpu-snap/blob/main/lists/mesa-2404.arm64.list
# Hardware-specific Nvidia/DRM backends and LLVM are implementation dependencies,
# not public API roots; Sentinel's virgl vendor replaces those implementations.
API_LIBRARIES = """libGL.so.1 libEGL.so.1 libGLESv1_CM.so.1 libGLESv2.so.2
libGLX.so.0 libGLdispatch.so.0 libOpenGL.so.0 libglapi.so.0 libdrm.so.2
libvulkan.so.1 libva.so.2 libva-drm.so.2 libva-x11.so.2 libva-wayland.so.2
libvdpau.so.1 libwayland-client.so.0 libwayland-cursor.so.0 libwayland-egl.so.1
libwayland-server.so.0 libX11.so.6 libX11-xcb.so.1 libXau.so.6 libXdmcp.so.6
libXdamage.so.1 libXext.so.6 libXfixes.so.3 libXxf86vm.so.1 libxshmfence.so.1
libxcb.so.1 libxcb-dri2.so.0 libxcb-dri3.so.0 libxcb-glx.so.0
libxcb-present.so.0 libxcb-randr.so.0 libxcb-shm.so.0 libxcb-sync.so.1
libxcb-xfixes.so.0 libxml2.so.2 libsensors.so.5 libicudata.so.74
libicui18n.so.74 libicuio.so.74 libicutest.so.74 libicutu.so.74 libicuuc.so.74""".split()


def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def elf_dependencies(header, dynamic, versions):
    """Validate readelf text without requiring host ELF tools or architecture."""
    if not (
        re.search(r"Class:\s+ELF64", header)
        and re.search(r"Machine:\s+AArch64", header)
        and re.search(r"Data:.*little endian", header)
    ):
        raise RuntimeError("Provider ELF is not little-endian AArch64 ELF64")
    sonames = re.findall(r"\(SONAME\).*\[(.*?)\]", dynamic)
    needed = re.findall(r"\(NEEDED\).*\[(.*?)\]", dynamic)
    if any(not name or Path(name).name != name for name in sonames + needed):
        raise RuntimeError("Invalid provider SONAME or dependency name")
    if any(
        tuple(map(int, version.split("."))) > (2, 39)
        for version in re.findall(r"Name: GLIBC_([0-9.]+)", versions)
    ):
        raise RuntimeError("Provider requires newer than core24 GLIBC")
    return needed


def write_manifest(path, metadata):
    path.write_text(json.dumps(metadata, indent=2) + "\n")


def provider_identity(mesa_sha256, source_sha256, assets):
    return hashlib.sha256(
        mesa_sha256.encode()
        + source_sha256.encode()
        + (assets / "dependencies.lock.json").read_bytes()
        + (assets / "meta/snap.yaml.in").read_bytes()
        + (assets / "bin/gpu-2404-provider-wrapper").read_bytes()
        + Path(__file__).read_bytes()
    ).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mesa", type=Path, required=True)
    parser.add_argument("--mesa-sha256", required=True)
    parser.add_argument("--mesa-source", type=Path, required=True)
    parser.add_argument("--mesa-source-sha256", required=True)
    parser.add_argument(
        "--assets",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "native/graphics/packaging/gpu-2404",
    )
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() not in {"aarch64", "arm64"}:
        parser.error("Run in the existing Linux arm64 builder, not on macOS")
    if os.geteuid() != 0:
        parser.error("Package extraction requires root in the isolated builder")
    for name in ("dpkg-deb", "snap", "curl", "readelf"):
        if not shutil.which(name):
            parser.error(f"Builder prerequisite missing: {name}")
    for digest in (args.mesa_sha256, args.mesa_source_sha256):
        if not re.fullmatch("[a-f0-9]{64}", digest):
            parser.error("Expected a lowercase SHA256 digest")
    if sha(args.mesa) != args.mesa_sha256:
        parser.error("Mesa artifact checksum differs")
    if sha(args.mesa_source) != args.mesa_source_sha256:
        parser.error("Pinned Mesa source checksum differs")
    root = args.assets.resolve()
    cache = args.cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    with (cache / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        build(args, root, cache)


def build(args, root, cache):
    dependency_lock = root / "dependencies.lock.json"
    locked = json.loads(dependency_lock.read_text())
    if locked.get("schema") != 1 or locked.get("architecture") != "arm64":
        raise RuntimeError("Unsupported provider dependency lock")
    if not locked.get("packages"):
        raise RuntimeError("Empty provider dependency lock")
    names = set()
    for item in locked["packages"]:
        name = item["filename"]
        if (
            not name
            or Path(name).name != name
            or name in names
            or not item["url"].startswith("https://ports.ubuntu.com/ubuntu-ports/pool/")
            or not re.fullmatch("[a-f0-9]{64}", item["sha256"])
            or not isinstance(item["size"], int)
            or item["size"] <= 0
        ):
            raise RuntimeError("Invalid or duplicate locked package")
        names.add(name)
    identity = provider_identity(args.mesa_sha256, args.mesa_source_sha256, root)
    version = "1-" + identity[:16]
    output = args.output.resolve() / f"sentinel-gpu-2404_{version}_arm64.snap"
    manifest = output.with_suffix(".json")
    try:
        metadata = json.loads(manifest.read_text())
        cached = (
            metadata["identity"] == identity
            and metadata["sha256"] == sha(output)
            and metadata["mesa_sha256"] == args.mesa_sha256
            and metadata["mesa_source_sha256"] == args.mesa_source_sha256
        )
    except (OSError, ValueError, KeyError):
        cached = False
    if cached:
        print(f"Cached provider: {output}")
        return
    downloads = cache / "debs"
    downloads.mkdir(exist_ok=True)

    def fetch(item):
        destination = downloads / item["filename"]
        if (
            destination.exists()
            and destination.stat().st_size == item["size"]
            and sha(destination) == item["sha256"]
        ):
            return destination
        temporary = destination.with_suffix(".partial")
        run(
            [
                "curl",
                "--fail",
                "--location",
                "--proto",
                "=https",
                "--proto-redir",
                "=https",
                "--retry",
                "3",
                "--connect-timeout",
                "20",
                "--max-time",
                "600",
                "--output",
                str(temporary),
                item["url"],
            ]
        )
        if temporary.stat().st_size != item["size"] or sha(temporary) != item["sha256"]:
            raise RuntimeError(f'Package integrity failure: {item["filename"]}')
        temporary.replace(destination)
        return destination

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        debs = list(pool.map(fetch, locked["packages"]))
    with tempfile.TemporaryDirectory(prefix="provider-", dir=cache) as temporary:
        stage = Path(temporary) / "stage"
        extracted = Path(temporary) / "packages"
        mesa = Path(temporary) / "mesa"
        lib = stage / "usr/lib/aarch64-linux-gnu"
        lib.mkdir(parents=True)
        extracted.mkdir()
        for deb in debs:
            arch = run(
                ["dpkg-deb", "--field", str(deb), "Architecture"], capture_output=True, text=True
            ).stdout.strip()
            if arch not in {"arm64", "all"}:
                raise RuntimeError(f"Wrong locked package architecture: {deb}: {arch}")
            run(["dpkg-deb", "--extract", str(deb), str(extracted)])
        # Explicitly select runtime library files; never ship libc or its loader.
        for directory in ("lib/aarch64-linux-gnu", "usr/lib/aarch64-linux-gnu"):
            source = extracted / directory
            if not source.exists():
                continue
            for path in source.iterdir():
                if ".so" not in path.name or not (path.is_file() or path.is_symlink()):
                    continue
                if re.match(
                    r"(ld-linux|lib(c|m|pthread|dl|rt|resolv|util|anl|nss_.*)\.so)", path.name
                ):
                    continue
                shutil.copy2(path, lib / path.name, follow_symlinks=False)
        # GLVND may pull distro Mesa dependencies; remove all vendor drivers.
        for path in lib.iterdir():
            if any(
                name in path.name
                for name in ("libEGL_mesa", "libGLX_mesa", "libgallium", "libgbm", "libvulkan_")
            ):
                path.unlink()
        for relative in ("usr/share/doc", "usr/share/X11", "usr/share/libdrm"):
            source = extracted / relative
            if source.exists():
                shutil.copytree(source, stage / relative, symlinks=True, dirs_exist_ok=True)
        mesa.mkdir()
        with tarfile.open(args.mesa) as archive:
            archive.extractall(mesa, filter="data")
        for path in (mesa / "lib").iterdir():
            if path.name.startswith("librvgpu") or path.name == "pkgconfig":
                continue
            if path.is_dir():
                shutil.copytree(path, lib / path.name, symlinks=True)
            else:
                shutil.copy2(path, lib / path.name, follow_symlinks=False)
        for source, target in [("share/glvnd", "usr/share/glvnd"), ("share/drirc.d", "drirc.d")]:
            shutil.copytree(mesa / source, stage / target, symlinks=True)
        # APT's GLVND dependencies include distro Mesa. Keep only the ELF closure
        # of our replacement driver plus public API roots, not unused LLVM/etc.
        pending = [
            path
            for path in lib.rglob("*")
            if path.is_file()
            and (
                path.relative_to(lib).parts[0] in {"dri", "gbm"}
                or path.name.startswith(("libgallium", "libEGL_mesa", "libGLX_mesa", "libgbm"))
            )
        ]
        missing_api = [name for name in API_LIBRARIES if not (lib / name).exists()]
        if missing_api:
            raise RuntimeError(f"Missing gpu-2404 consumer API/support libraries: {missing_api}")
        pending += [lib / name for name in API_LIBRARIES]
        retained = set()
        while pending:
            path = pending.pop()
            if path in retained or not path.exists():
                continue
            retained.add(path)
            if path.is_symlink():
                pending.append(path.resolve())
                continue
            dynamic = run(["readelf", "-d", str(path)], capture_output=True, text=True).stdout
            pending.extend(lib / name for name in re.findall(r"\(NEEDED\).*\[(.*?)\]", dynamic))
        for path in lib.iterdir():
            if (path.is_file() or path.is_symlink()) and path not in retained:
                path.unlink()
        shutil.copytree(stage / "usr/share/X11", stage / "X11", symlinks=True)
        doc = stage / "usr/share/doc/sentinel-mesa"
        doc.mkdir(parents=True)
        # Preserve the entire verified upstream source license corpus without rebuilding.
        shutil.copyfile(args.mesa_source, doc / "mesa-source.tar.xz")
        shutil.copyfile(dependency_lock, doc / "support-packages.lock.json")
        (stage / "bin").mkdir()
        shutil.copyfile(
            root / "bin/gpu-2404-provider-wrapper", stage / "bin/gpu-2404-provider-wrapper"
        )
        (stage / "bin/gpu-2404-provider-wrapper").chmod(0o755)
        (stage / "meta").mkdir()
        (stage / "meta/snap.yaml").write_text(
            (root / "meta/snap.yaml.in").read_text().replace("@VERSION@", version)
        )
        # Check every ELF dependency against provider or core libc filenames.
        core = {
            "libc.so.6",
            "libm.so.6",
            "libpthread.so.0",
            "libdl.so.2",
            "librt.so.1",
            "ld-linux-aarch64.so.1",
            "libresolv.so.2",
            "libutil.so.1",
            "libanl.so.1",
        }
        for path in lib.rglob("*"):
            if path.is_symlink() and (
                os.path.isabs(os.readlink(path))
                or not path.resolve(strict=True).is_relative_to(stage)
            ):
                raise RuntimeError(f"Provider library symlink escapes content: {path}")
            if not path.is_file() or path.is_symlink():
                continue
            with path.open("rb") as source:
                if source.read(4) != b"\x7fELF":
                    continue
            dynamic = run(["readelf", "-d", str(path)], capture_output=True, text=True).stdout
            header = run(["readelf", "-h", str(path)], capture_output=True, text=True).stdout
            versions = run(
                ["readelf", "--version-info", str(path)], capture_output=True, text=True
            ).stdout
            needed = elf_dependencies(header, dynamic, versions)
            missing = [name for name in needed if name not in core and not (lib / name).exists()]
            if missing:
                raise RuntimeError(f"Unresolved provider dependencies in {path}: {missing}")
        run(["snap", "pack", "--check-skeleton", str(stage)])
        # Pack on the destination filesystem, then publish only complete bytes.
        # Manifest is the commit marker; failed packaging leaves old output intact.
        with tempfile.TemporaryDirectory(prefix=".provider-", dir=output.parent) as publication:
            packed = Path(publication) / output.name
            run(["snap", "pack", str(stage), publication, "--filename", output.name])
            packed_manifest = Path(publication) / manifest.name
            write_manifest(
                packed_manifest,
                {
                    "schema": 1,
                    "identity": identity,
                    "version": version,
                    "sha256": sha(packed),
                    "size": packed.stat().st_size,
                    "mesa_sha256": args.mesa_sha256,
                    "mesa_source_sha256": args.mesa_source_sha256,
                    "dependency_lock_sha256": sha(dependency_lock),
                    "packages": locked,
                },
            )
            packed.replace(output)
            packed_manifest.replace(manifest)
        print(output)


if __name__ == "__main__":
    main()
