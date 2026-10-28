"""Builds sentinel-workspace's libraries: Mesa KosmicKrisp (Vulkan on Metal), ANGLE (OpenGL ES on
Metal) with libepoxy, DXMT (Direct3D 11 on Metal), virglrenderer (vrend on ANGLE, Venus, Neptune;
render server as threads), libkrun (Hypervisor.framework), libslirp, and Sentinel's EFI firmware (edk2
ArmVirtSentinel: virtio-mmio and virtio-pci, a boot framebuffer, ACPI built from the device tree).
Usage: build.py WORK OUTPUT. Sources and build tools are pinned and fetched into WORK, patched with
macos/patches/<source>-*.patch; nothing is installed on the host. OUTPUT receives lib/ (libraries with
@loader_path install names, and the Vulkan driver manifest), include/ and share/firmware/.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from angle import angle
from dxmt import dxmt
from toolchain import build_environment, digest, prepare_toolchain, run

HERE = Path(__file__).resolve().parent
PATCHES = HERE.parent / "patches"
RUST = "1.99.0"
RUSTUP_SHA256 = "ec1b9233e7f72990ecd8e62063fa7f6c3dfc2bec8e97f88bff165f9100ac696a"


def fetch(work, name, pin):
    """Unpacks a pinned upstream tree once, with patches/<name>-*.patch applied in name order."""
    patches = sorted(PATCHES.glob(f"{name}-*.patch"))
    key = pin["revision"] + "".join(p.name + p.read_text() for p in patches)
    target = work / name
    stamp = target / ".sentinel-source"
    if stamp.is_file() and stamp.read_text() == key:
        return target
    archive = work / f"{name}-{pin['revision']}.tar.gz"
    if not archive.is_file():
        partial = archive.with_suffix(".download")
        run(
            "curl",
            "-fsSL",
            "--retry",
            "2",
            "--max-time",
            "900",
            pin["url"],
            "-o",
            partial,
        )
        partial.replace(archive)
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    run("tar", "xf", archive, "--strip-components=1", "-C", target)
    for patch in patches:
        run("patch", "-s", "-p1", "--fuzz=0", "-i", patch, cwd=target)
    stamp.write_text(key)
    return target


def rust(work):
    """A private rustup and pinned toolchain under WORK/rust."""
    home = work / "rust"
    env = {"RUSTUP_HOME": str(home / "rustup"), "CARGO_HOME": str(home / "cargo")}
    cargo = home / "cargo/bin/cargo"
    if not cargo.is_file():
        init = work / "rustup-init"
        run(
            "curl",
            "-fsSL",
            "https://static.rust-lang.org/rustup/archive/1.29.1/aarch64-apple-darwin/rustup-init",
            "-o",
            init,
        )
        digest = subprocess.check_output(["shasum", "-a", "256", init], text=True).split()[0]
        if digest != RUSTUP_SHA256:
            raise RuntimeError("rustup-init checksum mismatch")
        init.chmod(0o755)
        run(
            init,
            "-y",
            "-q",
            "--no-modify-path",
            "--profile",
            "minimal",
            "--default-toolchain",
            RUST,
            env={**os.environ, **env},
        )
    return env, home / "cargo/bin"


def bundle(files, destination):
    """Copies libraries next to each other: each is @rpath/<name> (bin/ links them through
    @executable_path/../lib) and finds the others through @loader_path."""
    destination.mkdir(parents=True, exist_ok=True)
    names = {}
    for name, source in files.items():
        names[source.name] = names[source.resolve().name] = names[name] = name
        target = destination / name
        target.unlink(missing_ok=True)
        shutil.copy2(source, target)
        target.chmod(0o755)
    for name in files:
        target = destination / name
        args = ["-id", "@rpath/" + name]
        own = subprocess.check_output(["otool", "-D", str(target)], text=True).splitlines()[1:]
        links = subprocess.check_output(["otool", "-L", str(target)], text=True).splitlines()[1:]
        for line in links:
            dependency = line.strip().split(" (", 1)[0]
            if dependency.startswith(("/usr/lib/", "/System/Library/")) or dependency in own:
                continue
            if Path(dependency).name not in names:
                raise RuntimeError(f"{name}: unbundled dependency {dependency}")
            args += [
                "-change",
                dependency,
                "@loader_path/" + names[Path(dependency).name],
            ]
        load = subprocess.check_output(["otool", "-l", str(target)], text=True)
        for path in re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.+?) \(offset", load):
            if path != "@loader_path":
                args += ["-delete_rpath", path]
        # Both warn that the signature is replaced: expected, so not shown.
        subprocess.run(
            ["install_name_tool", *args, str(target)],
            check=True,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["codesign", "--force", "--sign", "-", str(target)],
            check=True,
            stderr=subprocess.DEVNULL,
        )


def iasl(work, pin, env):
    """ACPICA's ASL compiler, built from its pinned release: edk2 compiles ACPI tables with it."""
    root = work / "acpica"
    tool = root / "generate/unix/bin/iasl"
    if tool.is_file():
        return tool
    archive = work / "acpica.tar.gz"
    run("curl", "-fsSL", "--retry", "2", pin["url"], "-o", archive)
    if digest(archive) != pin["sha256"]:
        raise SystemExit("acpica: checksum mismatch")
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir()
    run("tar", "xf", archive, "--strip-components=1", "-C", root)
    # Its packed tables hold unaligned pointers, which chained fixups reject.
    run("make", "-s", "-j8", "iasl", "LDFLAGS=-Wl,-no_fixup_chains", cwd=root, env=env)
    return tool


def edk2_firmware(work, pin, acpica, firmware, prefix, env):
    """Sentinel's EFI firmware: edk2's ArmVirtSentinel platform (patches/edk2-*.patch), built for
    AArch64 on the host with edk2's CLANGDWARF toolchain: the pinned clang and lld, and iasl.
    Rebuilt only when its sources change.
    """
    edk2 = fetch(work, "edk2", pin)
    for path, sub in pin["submodules"].items():
        target = edk2 / path
        stamp = target / ".sentinel-source"
        if stamp.is_file() and stamp.read_text() == sub["revision"]:
            continue
        archive = work / f"{Path(path).name}-{sub['revision']}.tar.gz"
        if not archive.is_file():
            run(
                "curl",
                "-fsSL",
                "--retry",
                "2",
                "--max-time",
                "900",
                sub["url"],
                "-o",
                archive,
            )
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        run("tar", "xf", archive, "--strip-components=1", "-C", target)
        stamp.write_text(sub["revision"])
    # The boot logo the firmware shows, which Windows keeps on screen while it starts (ACPI BGRT).
    logo = (HERE.parent / "firmware/logo.bmp").read_bytes()
    (edk2 / "MdeModulePkg/Logo/Logo.bmp").write_bytes(logo)
    output = edk2 / "Build/ArmVirtSentinel-AArch64/RELEASE_CLANGDWARF/FV/SENTINEL_EFI.fd"
    key = (
        (edk2 / ".sentinel-source").read_text()
        + acpica["sha256"]
        + hashlib.sha256(logo).hexdigest()
    )
    stamp = edk2 / "Build/.sentinel-firmware"
    if not (output.is_file() and stamp.is_file() and stamp.read_text() == key):
        # edk2 finds its tools by prefix (CLANGDWARF_BIN, IASL_PREFIX): one directory of links.
        tools = work / "edk2-tools"
        shutil.rmtree(tools, ignore_errors=True)
        tools.mkdir()
        for name, path in [
            ("clang", prefix / "opt/llvm/bin/clang"),
            ("llvm-ar", prefix / "opt/llvm/bin/llvm-ar"),
            ("llvm-objcopy", prefix / "opt/llvm/bin/llvm-objcopy"),
            ("llvm-nm", prefix / "opt/llvm/bin/llvm-nm"),
            ("ld.lld", prefix / "opt/lld/bin/ld.lld"),
            ("iasl", iasl(work, acpica, env)),
        ]:
            (tools / name).symlink_to(path)
        build = dict(
            env,
            PATH=f"{tools}:{env['PATH']}",
            CLANGDWARF_BIN=f"{tools}/",
            IASL_PREFIX=f"{tools}/",
            PYTHON_COMMAND="python3",
        )
        for generated in [
            "Conf/BuildEnv.sh",
            "Conf/tools_def.txt",
            "Conf/target.txt",
            "Conf/build_rule.txt",
        ]:
            (edk2 / generated).unlink(missing_ok=True)
        run("make", "-s", "-C", "BaseTools", "-j8", cwd=edk2, env=build)
        run(
            "bash",
            "-c",
            ". ./edksetup.sh >/dev/null && build -q -a AARCH64 -t CLANGDWARF -b RELEASE -n 8 "
            "-p ArmVirtPkg/ArmVirtSentinel.dsc -D NETWORK_ENABLE=FALSE",
            cwd=edk2,
            env=build,
        )
        stamp.write_text(key)
    shutil.copy2(output, firmware / "SENTINEL_EFI.fd")


def main(work, output):
    work, output = work.resolve(), output.resolve()
    work.mkdir(parents=True, exist_ok=True)
    pins = json.loads((HERE / "sources.json").read_text())
    prefix = prepare_toolchain(work, HERE / "toolchain.lock.json")
    venv = work / "venv"
    requirements = HERE / "requirements.txt"
    stamp = venv / ".requirements"
    if not stamp.is_file() or stamp.read_bytes() != requirements.read_bytes():
        run(sys.executable, "-m", "venv", "--clear", venv)
        run(venv / "bin/pip", "install", "-q", "-r", requirements)
        stamp.write_bytes(requirements.read_bytes())
    env = build_environment(work, prefix, venv)
    # Xcode's own SDK: the Command Line Tools may carry a newer SDK its linker cannot read.
    env["SDKROOT"] = subprocess.check_output(
        ["xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True
    ).strip()
    local = work / "local"
    loader = prefix / "opt/vulkan-loader"
    env["PKG_CONFIG_PATH"] = ":".join(
        [
            str(local / "lib/pkgconfig"),
            str(loader / "lib/pkgconfig"),
            env["PKG_CONFIG_PATH"],
        ]
    )

    def meson(name, tree, options):
        folder = work / (name + "-build")
        configured = folder / "meson-private/coredata.dat"
        fresh = (
            configured.is_file()
            and configured.stat().st_mtime >= (tree / ".sentinel-source").stat().st_mtime
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
                *options,
                env=env,
            )
        run(venv / "bin/ninja", "-C", folder, "install", env=env)

    meson(
        "mesa",
        fetch(work, "mesa", pins["mesa"]),
        [
            "-Dplatforms=macos",
            "-Dzstd=disabled",
            "-Dbuild-tests=false",
            "-Dvulkan-drivers=kosmickrisp",
            "-Dgallium-drivers=",
            "-Dopengl=false",
            "-Dprefer_static=true",
        ],
    )
    # OpenGL ES on Metal for virglrenderer's GL renderer (vrend): ANGLE's libEGL and libGLESv2,
    # which pkg-config sees as egl and glesv2, and libepoxy (vrend's GL dispatch), which opens
    # them from its own directory.
    angle_lib, angle_include = angle(work, env, pins["angle"])
    pkgconfig = local / "lib/pkgconfig"
    pkgconfig.mkdir(parents=True, exist_ok=True)
    for name, lib in (("egl", "EGL"), ("glesv2", "GLESv2")):
        (pkgconfig / f"{name}.pc").write_text(
            f"Name: {name}\nDescription: ANGLE lib{lib} (Metal)\nVersion: 1.5\n"
            f"Libs: -L{angle_lib} -l{lib}\nCflags: -I{angle_include}\n"
        )
    meson(
        "libepoxy",
        fetch(work, "libepoxy", pins["libepoxy"]),
        ["-Degl=yes", "-Dglx=no", "-Dx11=false", "-Dtests=false"],
    )
    # vrend (virgl capsets, GLES on ANGLE), Venus (Vulkan on KosmicKrisp) and Neptune (D3D11, on
    # DXMT, which it opens from its own directory). The render server runs as threads of
    # sentinel-workspace: blob memory is then mappable into the guest, and nothing forks after
    # Metal is loaded (unsafe on macOS). The Vulkan loader is linked, not opened by name.
    # Its venus-protocol subproject and Neptune's generated protocol come from pinned archives,
    # and meson may not download: nothing in the build is ever a git clone.
    virglrenderer = fetch(work, "virglrenderer", pins["virglrenderer"])
    venus = virglrenderer / "subprojects/venus-protocol-1.1.3"
    if not venus.is_dir():
        shutil.copytree(fetch(work, "venus-protocol", pins["venus-protocol"]), venus)
    neptune = virglrenderer / "src/neptune/neptune-protocol"
    if not neptune.is_dir():
        protocol = fetch(work, "neptune-protocol", pins["neptune-protocol"])
        shutil.copytree(protocol / "src/neptune/neptune-protocol", neptune)
    meson(
        "virglrenderer",
        virglrenderer,
        [
            "--wrap-mode=nodownload",
            "-Dvrend=true",
            "-Dplatforms=egl",
            "-Dvenus=true",
            "-Dneptune=true",
            "-Dtests=false",
            "-Drender-server-mode=thread",
            "-Drender-server-worker=thread",
            "-Dvulkan-dload=false",
            # venus-protocol's generated decoders pass an unset VkResult to vn_sizeof_VkResult,
            # which only takes its size: a false positive of newer clang, once per header.
            "-Dc_args=-Wno-uninitialized-const-pointer",
        ],
    )

    # Direct3D 11 on Metal for Neptune: DXMT (LGPL-2.1), which virglrenderer dlopens (never links).
    dxmt_lib = dxmt(work, env, venv, pins, fetch)

    rust_env, cargo_bin = rust(work)
    libkrun = fetch(work, "libkrun", pins["libkrun"])
    cargo_env = {
        **env,
        **rust_env,
        "PATH": f"{cargo_bin}:{env['PATH']}",
        "CARGO_TARGET_DIR": str(work / "libkrun-target"),
    }
    # libkrun's git dependency comes from a pinned archive (cargo would check it out as a git
    # repository); every ffier tag libkrun's workspace names resolves to it.
    ffier = fetch(work, "ffier", pins["ffier"])
    crates = (
        "ffier",
        "ffier-builtins",
        "ffier-impl",
        "ffier-rt",
        "ffier-schema",
        "ffier-gen-rust-client",
    )
    git_patches = [
        arg
        for crate in crates
        for arg in (
            "--config",
            f'patch."https://github.com/mtjhrc/ffier.git".{crate}.path="{ffier / crate}"',
        )
    ]
    # rutabaga_gfx (libkrun's GPU glue, a crates.io dependency) from its pinned crate archive, patched
    # (patches/rutabaga_gfx-*.patch): capset 7 is virglrenderer's Neptune.
    rutabaga = fetch(work, "rutabaga_gfx", pins["rutabaga_gfx"])
    git_patches += ["--config", f'patch.crates-io.rutabaga_gfx.path="{rutabaga}"']
    run(
        cargo_bin / "cargo",
        "build",
        *git_patches,
        "--release",
        "-p",
        "libkrun",
        "--no-default-features",
        "--features",
        "ffi,blk,net,gpu,input",
        cwd=libkrun,
        env=cargo_env,
    )

    slirp = prefix / "opt/libslirp"
    bundle(
        {
            "libkrun.dylib": work / "libkrun-target/release/libkrun.dylib",
            "libvirglrenderer.1.dylib": local / "lib/libvirglrenderer.1.dylib",
            "libepoxy.0.dylib": local / "lib/libepoxy.0.dylib",
            "libEGL.dylib": angle_lib / "libEGL.dylib",
            "libGLESv2.dylib": angle_lib / "libGLESv2.dylib",
            "libdxmt-native.dylib": dxmt_lib,
            "libvulkan.1.dylib": loader / "lib/libvulkan.1.dylib",
            "libvulkan_kosmickrisp.dylib": local / "lib/libvulkan_kosmickrisp.dylib",
            "libSPIRV-Tools.dylib": prefix / "opt/spirv-tools/lib/libSPIRV-Tools.dylib",
            "libslirp.0.dylib": slirp / "lib/libslirp.0.dylib",
            "libglib-2.0.0.dylib": prefix / "opt/glib/lib/libglib-2.0.0.dylib",
            "libintl.8.dylib": prefix / "opt/gettext/lib/libintl.8.dylib",
            "libpcre2-8.0.dylib": prefix / "opt/pcre2/lib/libpcre2-8.0.dylib",
        },
        output / "lib",
    )
    # The loader resolves a relative library path against the manifest's own directory.
    (output / "lib/kosmickrisp_icd.json").write_text(
        json.dumps(
            {
                "file_format_version": "1.0.1",
                "ICD": {
                    "library_path": "./libvulkan_kosmickrisp.dylib",
                    "api_version": "1.4.0",
                },
            },
            indent=2,
        )
        + "\n"
    )

    include = output / "include"
    shutil.rmtree(include, ignore_errors=True)
    include.mkdir(parents=True)
    for header in ("libkrun.h", "libkrun_display.h", "libkrun_input.h"):
        shutil.copy2(libkrun / "include" / header, include / header)
    shutil.copytree(slirp / "include/slirp", include / "slirp")

    firmware = output / "share/firmware"
    shutil.rmtree(firmware, ignore_errors=True)  # only the firmware this build makes
    firmware.mkdir(parents=True)
    edk2_firmware(work, pins["edk2"], pins["acpica"], firmware, prefix, env)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
