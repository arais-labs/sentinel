"""DXMT (D3D11 on Metal, LGPL-2.1) in its native mode: one libdxmt-native.dylib that the Neptune
renderer in virglrenderer dlopens at run time (never links). Its shader converter (airconv) links
LLVM 15 statically; that LLVM is built here from the pinned release, with no code generator
targets (DXMT emits AIR bitcode itself). The Windows headers it needs are DXMT's own submodule
(mingw-directx-headers), fetched as a pinned archive. CMake (for LLVM only) is a pinned release.
Nothing needs a Metal compiler (Xcode's Metal Toolchain, a separate download): the patched DXMT
compiles its internal Metal library from source at run time and keeps airconv's AIR helpers as
LLVM IR text, which this LLVM's llvm-as assembles (patches/dxmt-01-runtime-shaders.patch).
"""

import hashlib
import shutil

from toolchain import run


def check_air_sources(tree):
    """Each airconv helper's LLVM IR names the sha256 of the .metal source it was made from; a
    DXMT update that changes a source needs its IR made again (see the .ll's header)."""
    for ir in sorted((tree / "src/airconv/shaders").glob("*.ll")):
        source = ir.with_suffix(".metal")
        expected = hashlib.sha256(source.read_bytes()).hexdigest()
        if f"(sha256 {expected})" not in ir.read_text().split("\nsource_filename", 1)[0]:
            raise RuntimeError(
                f"dxmt: {ir.name} was not made from this {source.name}; make it again"
            )


def dxmt(work, env, venv, pins, fetch):
    """Builds and returns WORK/local/lib/libdxmt-native.dylib; each step reruns only when its
    sources change."""
    cmake = fetch(work, "cmake", pins["cmake"]) / "CMake.app/Contents/bin/cmake"

    llvm_source = fetch(work, "llvm-project", pins["llvm-project"])
    llvm = work / "llvm15"
    stamp = llvm / ".sentinel-source"
    key = (llvm_source / ".sentinel-source").read_text()
    if not (stamp.is_file() and stamp.read_text() == key):
        folder = work / "llvm15-build"
        shutil.rmtree(folder, ignore_errors=True)
        shutil.rmtree(llvm, ignore_errors=True)
        run(
            cmake,
            "-G",
            "Ninja",
            "-S",
            llvm_source / "llvm",
            "-B",
            folder,
            "-DCMAKE_MAKE_PROGRAM=" + str(venv / "bin/ninja"),
            "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_INSTALL_PREFIX=" + str(llvm),
            "-DCMAKE_C_COMPILER=" + env["CC"],
            "-DCMAKE_CXX_COMPILER=" + env["CXX"],
            "-DCMAKE_OSX_ARCHITECTURES=arm64",
            "-DCMAKE_OSX_DEPLOYMENT_TARGET=13.0",
            "-DLLVM_TARGETS_TO_BUILD=",
            "-DLLVM_BUILD_TOOLS=OFF",
            "-DLLVM_INCLUDE_TESTS=OFF",
            "-DLLVM_INCLUDE_EXAMPLES=OFF",
            "-DLLVM_INCLUDE_BENCHMARKS=OFF",
            "-DLLVM_INCLUDE_DOCS=OFF",
            "-DLLVM_INCLUDE_UTILS=OFF",
            "-DLLVM_ENABLE_BINDINGS=OFF",
            "-DLLVM_ENABLE_ZLIB=OFF",
            "-DLLVM_ENABLE_ZSTD=OFF",
            "-DLLVM_ENABLE_LIBXML2=OFF",
            "-DLLVM_ENABLE_TERMINFO=OFF",
            "-DLLVM_VERSION_PRINTER_SHOW_HOST_TARGET_INFO=OFF",
            "-DCMAKE_EXE_LINKER_FLAGS=-Wl,-no_warn_duplicate_libraries",
            env=env,
        )
        run(venv / "bin/ninja", "-C", folder, "install", env=env)
        stamp.write_text(key)
    # llvm-as, which LLVM_BUILD_TOOLS=OFF leaves out, assembles airconv's AIR helpers.
    if not (llvm / "bin/llvm-as").is_file():
        folder = work / "llvm15-build"
        run(venv / "bin/ninja", "-C", folder, "llvm-as", env=env)
        (llvm / "bin").mkdir(exist_ok=True)
        shutil.copy2(folder / "bin/llvm-as", llvm / "bin/llvm-as")

    tree = fetch(work, "dxmt", pins["dxmt"])
    check_air_sources(tree)
    headers = tree / "include/native/directx"
    if not (headers / "d3d11.h").is_file():
        shutil.rmtree(headers, ignore_errors=True)
        shutil.copytree(
            fetch(work, "mingw-directx-headers", pins["mingw-directx-headers"]), headers
        )

    folder = work / "dxmt-build"
    local = work / "local"
    configured = folder / "meson-private/coredata.dat"
    fresh = (
        configured.is_file()
        and configured.stat().st_mtime >= (tree / ".sentinel-source").stat().st_mtime
        and configured.stat().st_mtime >= stamp.stat().st_mtime
    )
    if not fresh:
        run(
            venv / "bin/meson",
            "setup",
            "--wipe" if configured.is_file() else "--reconfigure",
            folder,
            tree,
            "--buildtype=release",
            "--prefix=" + str(local),
            "-Dnative_llvm_path=" + str(llvm),
            # Upstream's own warnings (hundreds, none ours) and its repeated LLVM libraries.
            *(f"-D{lang}_args=-w" for lang in ("c", "cpp", "objc", "objcpp")),
            *(f"-D{lang}_link_args=-Wl,-no_warn_duplicate_libraries" for lang in ("c", "cpp")),
            env=env,
        )
    run(venv / "bin/ninja", "-C", folder, "install", env=env)
    return local / "lib/libdxmt-native.dylib"
