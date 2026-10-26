#!/usr/bin/env python3
"""Builds the Windows guest's Vulkan driver (DESIGN.md §16, "Vulkan (Windows)"): Mesa's Venus ICD,
vulkan_virtio.dll for Windows ARM64 and vulkan_virtio_x64.dll for x64 applications under its emulation,
from osy/virtio-win-mesa at a pinned commit plus patches/, cross-compiled on the host with the pinned zig
toolchain's clang and mingw-w64 (no MSVC, no Windows).

  build.py --zig ZIG --work DIR --out DIR

Everything it fetches is pinned by SHA-256 (sources.json): the Mesa tree, the DirectX headers its
Win32 WSI includes, and the Windows SDK and WDK packages (NuGet) for the few D3DKMT/DDI headers
mingw-w64 lacks; they are only read at build time. Meson, Ninja and Mako run from a venv pinned by
requirements.txt. Writes OUT/vulkan_virtio.dll and OUT/vulkan_virtio_x64.dll, and their loader
manifests OUT/venus_icd.json and OUT/venus_icd_x64.json."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MESON_OPTIONS = [
    "--buildtype=release",
    "-Db_ndebug=true",
    "--wrap-mode=nodownload",
    "-Dplatforms=windows",
    "-Dvulkan-drivers=virtio",
    "-Dgallium-drivers=",
    "-Dopengl=false",
    "-Dgles1=disabled",
    "-Dgles2=disabled",
    "-Degl=disabled",
    "-Dglx=disabled",
    "-Dgbm=disabled",
    "-Dllvm=disabled",
    "-Dzstd=disabled",
    "-Dzlib=disabled",
    "-Dexpat=disabled",
    "-Dxmlconfig=disabled",
    "-Dbuild-tests=false",
    "-Dmicrosoft-clc=disabled",
    "-Dspirv-to-dxil=false",
    "-Dvideo-codecs=",
    "-Dshader-cache=disabled",
    "-Dvulkan-layers=",
    "-Dtools=",
    "-Dneptune=false",
    "-Dspirv-tools=disabled",
    "-Dvalgrind=disabled",
    "-Dlibunwind=disabled",
    "-Dgallium-d3d12-video=disabled",
    "-Dgallium-d3d12-graphics=disabled",
]
# The builds from the one tree: Meson's CPU family, zig's target, dlltool's machine, the ICD, its manifest.
TARGETS = [
    ("aarch64", "aarch64-windows-gnu", "arm64", "vulkan_virtio.dll", "venus_icd.json"),
    ("x86_64", "x86_64-windows-gnu", "i386:x86-64", "vulkan_virtio_x64.dll", "venus_icd_x64.json"),
]


def run(*argv, **kw):
    subprocess.run([str(a) for a in argv], check=True, **kw)


def fetch(source, dl):
    """The source's file in dl/, downloaded once and checked against its pinned digest."""
    path = dl / source["sha256"]
    if not path.is_file():
        dl.mkdir(parents=True, exist_ok=True)
        part, digest = path.with_suffix(".part"), hashlib.sha256()
        run("curl", "-fsSL", "-o", part, source["url"])
        with open(part, "rb") as f:
            while chunk := f.read(1 << 20):
                digest.update(chunk)
        if digest.hexdigest() != source["sha256"]:
            part.unlink()
            sys.exit(f"{source['url']}: SHA-256 {digest.hexdigest()}, expected {source['sha256']}")
        part.rename(path)
    return path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--zig", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    zig, work, out = a.zig.resolve(), a.work.resolve(), a.out.resolve()
    sources = json.loads((HERE / "sources.json").read_text())
    patches = sorted((HERE / "patches").glob("*.patch"))
    work.mkdir(parents=True, exist_ok=True)

    venv = work / "venv"
    requirements = (HERE / "requirements.txt").read_bytes()
    if (
        not (venv / ".requirements").is_file()
        or (venv / ".requirements").read_bytes() != requirements
    ):
        run(sys.executable, "-m", "venv", "--clear", venv)
        run(venv / "bin/pip", "install", "-q", "-r", HERE / "requirements.txt")
        (venv / ".requirements").write_bytes(requirements)

    # The tree: Mesa, its DirectX-Headers subproject, the DDK headers in include/winddk (where Mesa's
    # Windows drivers expect them), then the patches; made afresh when any of them changes.
    src = work / "mesa"
    stamp = hashlib.sha256(
        b"".join(f.read_bytes() for f in [HERE / "build.py", HERE / "sources.json", *patches])
    ).hexdigest()
    if not (src / ".stamp").is_file() or (src / ".stamp").read_text() != stamp:
        shutil.rmtree(src, ignore_errors=True)
        for cpu, *_ in TARGETS:
            shutil.rmtree(work / cpu / "out", ignore_errors=True)
        src.mkdir()
        run("tar", "-xzf", fetch(sources["mesa"], work / "dl"), "-C", src, "--strip-components=1")
        dxh = src / "subprojects/DirectX-Headers-1.0"
        dxh.mkdir()
        run(
            "tar",
            "-xzf",
            fetch(sources["directx-headers"], work / "dl"),
            "-C",
            dxh,
            "--strip-components=1",
        )
        for kit in ("windows-sdk", "windows-wdk"):
            names = sources[kit]["headers"]
            run(
                "tar",
                "-xf",
                fetch(sources[kit], work / "dl"),
                "-C",
                src / "include/winddk",
                "--strip-components=4",
                *names,
            )
        for patch in patches:
            run("patch", "-s", "-p1", "--fuzz=0", "-i", patch, cwd=src)
        (src / ".stamp").write_text(stamp)

    # zig's clang for each target. zig reads "-shared -static" as a static link, and Meson adds -static
    # to the ICD's link (to keep libgcc out of it, which zig never links), so DLL links drop it. Mesa's
    # futexes need synchronization.lib (WaitOnAddress), which zig's mingw has as an API set .def.
    env = dict(
        os.environ,
        ZIG_GLOBAL_CACHE_DIR=str(work / "zig-cache"),
        ZIG_LOCAL_CACHE_DIR=str(work / "zig-cache"),
        PATH=f"{venv / 'bin'}:{os.environ['PATH']}",
    )
    out.mkdir(parents=True, exist_ok=True)
    for cpu, target, machine, dll, manifest in TARGETS:
        tools = work / cpu / "bin"
        tools.mkdir(parents=True, exist_ok=True)
        for tool in ("cc", "c++"):
            (tools / tool).write_text(
                "#!/bin/sh\n"
                'shared=; for a in "$@"; do [ "$a" = -shared ] && shared=1; done\n'
                'for a in "$@"; do shift; [ -n "$shared" ] && [ "$a" = -static ] && continue; set -- "$@" "$a"; done\n'
                f'exec "{zig}" {tool} -target {target} "$@"\n'
            )
            (tools / tool).chmod(0o755)
        implib = work / cpu / "implib"
        implib.mkdir(exist_ok=True)
        run(
            zig,
            "dlltool",
            "-m",
            machine,
            "-d",
            zig.parent / "lib/libc/mingw/lib-common/api-ms-win-core-synch-l1-2-0.def",
            "-l",
            implib / "libsynchronization.a",
            env=env,
        )
        cross = work / cpu / "cross.ini"
        cross.write_text(f"""[binaries]
c = '{tools / "cc"}'
cpp = '{tools / "c++"}'
ar = ['{zig}', 'ar']
ranlib = ['{zig}', 'ranlib']
windres = ['{zig}', 'rc']
strip = 'true'
pkg-config = 'false'

[host_machine]
system = 'windows'
cpu_family = '{cpu}'
cpu = '{cpu}'
endian = 'little'

[built-in options]
c_link_args = ['-L{implib}', '-s']
cpp_link_args = ['-L{implib}', '-s']
""")

        build = work / cpu / "out"
        if not (build / "build.ninja").is_file():
            run(
                venv / "bin/meson",
                "setup",
                build,
                src,
                "--cross-file",
                cross,
                *MESON_OPTIONS,
                env=env,
            )
        run(venv / "bin/ninja", "-C", build, "src/virtio/vulkan/vulkan_virtio.dll", env=env)
        shutil.copyfile(build / "src/virtio/vulkan/vulkan_virtio.dll", out / dll)
        shutil.copyfile(HERE / manifest, out / manifest)


if __name__ == "__main__":
    main()
