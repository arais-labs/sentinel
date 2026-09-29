"""Build and package the sole host GPU path: VirGL → Zink → Metal."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from toolchain import prepare_toolchain, build_environment, run
from upstream import prepare_source

LIBRARIES = (
    "libEGL.dylib",
    "libgallium-26.3.0-devel.dylib",
    "libepoxy.0.dylib",
    "libvirglrenderer.1.dylib",
    "libvulkan_kosmickrisp.dylib",
    "libvulkan.1.dylib",
    "libSPIRV-Tools.dylib",
)


def apple_environment_identity():
    """Query the same SDK selection and compiler drivers used by both builders."""
    commands = {
        "sdkPath": ["xcrun", "--show-sdk-path"],
        "sdkVersion": ["xcrun", "--show-sdk-version"],
        "clang": ["/usr/bin/clang", "--version"],
        "clang++": ["/usr/bin/clang++", "--version"],
    }
    return {
        name: subprocess.check_output(command, text=True).strip()
        for name, command in commands.items()
    }


def bundle_identity(desktop, environment):
    """Hash host-owned inputs only; guest artifacts have independent build keys.

    Keep these roots aligned with build_host and build-desktop.py's Meson graph.
    Session scripts, distro packages and the VM runtime lock do not compile or
    link into the host renderer and must not invalidate its bundle.
    """
    source = desktop / "scripts/packaging/graphics"
    native = desktop / "native/graphics"
    files = [
        source / name
        for name in (
            "host.py",
            "build-desktop.py",
            "upstream.py",
            "toolchain.py",
            "requirements.txt",
        )
    ]
    files.extend(native / name for name in ("meson.build", "meson.options", "toolchain.lock.json"))
    for name in ("renderer", "video", "transport", "patches/host"):
        files.extend(
            p
            for p in (native / name).rglob("*")
            if p.is_file() and p.suffix != ".md" and "__pycache__" not in p.parts
        )
    digest = hashlib.sha256()
    digest.update(json.dumps(environment, sort_keys=True).encode() + b"\0")
    for file in sorted(files):
        digest.update(file.relative_to(desktop).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(file.read_bytes()).digest())
    pins = json.loads((native / "sources.lock.json").read_text())
    digest.update(
        json.dumps(
            {name: pins[name] for name in ("mesa", "epoxy", "virgl", "egl")}, sort_keys=True
        ).encode()
    )
    return digest.hexdigest()


def build_identity(tree, options, toolchain, requirements, environment):
    # Source patches are Ninja inputs, not reasons to discard all objects.
    # Configure definitions and symlink topology remain conservative wipe
    # boundaries: Meson may cache dependency discovery across reconfiguration.
    configuration = hashlib.sha256()
    for file in sorted(tree.rglob("*")):
        if file.is_symlink():
            data = os.readlink(file).encode()
        elif file.is_file() and (
            file.name in {"meson.build", "meson.options", "meson_options.txt"}
            or file.suffix == ".wrap"
        ):
            data = file.read_bytes()
        else:
            continue
        configuration.update(file.relative_to(tree).as_posix().encode() + b"\0" + data + b"\0")
    return hashlib.sha256(
        b"\0".join(
            [
                (tree / ".sentinel-upstream").read_bytes(),
                configuration.digest(),
                json.dumps(options, sort_keys=True).encode(),
                toolchain.read_bytes(),
                requirements.read_bytes(),
                json.dumps(environment, sort_keys=True).encode(),
                Path(__file__).read_bytes(),
                Path(__file__).with_name("toolchain.py").read_bytes(),
            ]
        )
    ).hexdigest()


def bundle_libraries(files, destination):
    destination.mkdir(parents=True, exist_ok=True)
    names = {source.name: name for name, source in files.items()}
    names.update({source.resolve().name: name for name, source in files.items()})
    names.update({name: name for name in files})
    for name, source in files.items():
        shutil.copy2(source, destination / name)
    for name in files:
        target = destination / name
        args = ["-id", "@loader_path/" + name]
        dependencies = subprocess.check_output(["otool", "-L", str(target)], text=True)
        for line in dependencies.splitlines()[1:]:
            dependency = line.strip().split(" (", 1)[0]
            if dependency.startswith(("/usr/lib/", "/System/Library/")):
                continue
            basename = names.get(Path(dependency).name)
            if basename is None:
                raise RuntimeError(f"{name}: unbundled dependency {dependency}")
            args.extend(["-change", dependency, "@loader_path/" + basename])
        load = subprocess.check_output(["otool", "-l", str(target)], text=True)
        rpaths = re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.+?) \(offset", load)
        for path in rpaths:
            if path != "@loader_path":
                args.extend(["-delete_rpath", path])
        if "@loader_path" not in rpaths:
            args.extend(["-add_rpath", "@loader_path"])
        run("install_name_tool", *args, target)
        run("codesign", "--force", "--sign", "-", target)


def build_host(desktop, work, destination, environment=None):
    environment = apple_environment_identity() if environment is None else environment
    native = desktop / "native/graphics"
    pins = json.loads((native / "sources.lock.json").read_text())
    prefix = prepare_toolchain(work, native / "toolchain.lock.json")
    venv = work / "venv"
    requirements = Path(__file__).with_name("requirements.txt")
    if not venv.exists():
        run(sys.executable, "-m", "venv", venv)
    requirement_stamp = venv / ".graphics-requirements"
    if (
        not requirement_stamp.exists()
        or requirement_stamp.read_bytes() != requirements.read_bytes()
    ):
        run(venv / "bin/pip", "install", "-r", requirements)
        requirement_stamp.write_bytes(requirements.read_bytes())
    env = build_environment(work, prefix, venv)
    env["SDKROOT"] = environment["sdkPath"]

    def source(key, name, patches=()):
        pin = pins[key]
        return prepare_source(
            work,
            pin["repository"],
            pin["revision"],
            name,
            [native / "patches/host" / patch for patch in patches],
        )

    epoxy = source("epoxy", "epoxy", ["epoxy-bundled-libraries.patch"])
    virgl = source("virgl", "virgl", ["virgl-metal.patch"])
    headers = source("egl", "egl-registry")
    mesa = source(
        "mesa",
        "mesa-host",
        [
            "mesa-0001-kosmickrisp-metal.patch",
            "mesa-0002-zink-metal-interop.patch",
            "mesa-0003-zink-buffer-ranges.patch",
            "mesa-0004-queue-finish-barrier-lifetime.patch",
        ],
    )
    local = work / "local"
    pkgconfig = work / "pkgconfig"
    pkgconfig.mkdir(exist_ok=True)
    loader = prefix / "opt/vulkan-loader/lib"
    (pkgconfig / "vulkan.pc").write_text(
        f"prefix={mesa}\nlibdir={loader}\nincludedir=${{prefix}}/include\n"
        "Name: Vulkan\nDescription: Bundled Vulkan loader\nVersion: 1.4.357\n"
        "Libs: -L${libdir} -lvulkan.1\nCflags: -I${includedir}\n"
    )

    def build(name, tree, options, targets=()):
        folder = work / name
        key = build_identity(
            tree, options, native / "toolchain.lock.json", requirements, environment
        )
        stamp = folder / ".sentinel-build"
        same = stamp.is_file() and stamp.read_text() == key
        configured = (folder / "meson-private/coredata.dat").is_file()
        mode = "--reconfigure" if same or not configured else "--wipe"
        run(
            venv / "bin/meson",
            "setup",
            mode,
            folder,
            tree,
            "--buildtype=debugoptimized",
            "--prefix=" + str(local),
            *options,
            env=env,
        )
        run(
            venv / "bin/ninja",
            "-C",
            folder,
            "-j" + os.environ.get("SENTINEL_BUILD_JOBS", "4"),
            *targets,
            env=env,
        )
        stamp.write_text(key)
        return folder

    build(
        "epoxy-build",
        epoxy,
        [
            "-Degl=yes",
            "-Dglx=no",
            "-Dx11=false",
            "-Dtests=false",
            "-Dc_args=-I" + str(headers / "api"),
        ],
        ["install"],
    )
    build(
        "virgl-build",
        virgl,
        [
            "-Dplatforms=egl",
            "-Dvenus=false",
            "-Dtests=false",
            "-Dc_args=-I" + str(headers / "api"),
            "-Dobjc_link_args=['-framework','Foundation','-lobjc']",
        ],
    )
    common = ["-Dplatforms=macos", "-Dzstd=disabled", "-Dbuild-tests=false"]
    metal = build(
        "mesa-metal-build",
        mesa,
        [
            *common,
            "-Dvulkan-drivers=kosmickrisp",
            "-Dgallium-drivers=",
            "-Dopengl=false",
            "-Dprefer_static=true",
        ],
        ["src/kosmickrisp/vulkan/libvulkan_kosmickrisp.dylib"],
    )
    zink = build(
        "mesa-zink-build",
        mesa,
        [
            *common,
            "-Dvulkan-drivers=",
            "-Dgallium-drivers=zink",
            "-Dopengl=true",
            "-Dglx=disabled",
            "-Degl=enabled",
            "-Dgbm=disabled",
            "-Dllvm=disabled",
            "-Dxmlconfig=disabled",
            "-Dvulkan-loader-rpath=@loader_path",
        ],
        ["src/egl/libEGL.1.dylib", "src/gallium/targets/dri/libgallium-26.3.0-devel.dylib"],
    )
    files = {
        "libEGL.dylib": zink / "src/egl/libEGL.1.dylib",
        "libgallium-26.3.0-devel.dylib": zink
        / "src/gallium/targets/dri/libgallium-26.3.0-devel.dylib",
        "libepoxy.0.dylib": local / "lib/libepoxy.0.dylib",
        "libvirglrenderer.1.dylib": work / "virgl-build/src/libvirglrenderer.1.dylib",
        "libvulkan_kosmickrisp.dylib": metal / "src/kosmickrisp/vulkan/libvulkan_kosmickrisp.dylib",
        "libvulkan.1.dylib": loader / "libvulkan.1.dylib",
        "libSPIRV-Tools.dylib": prefix / "opt/spirv-tools/lib/libSPIRV-Tools.dylib",
    }
    bundle_libraries(files, destination)
    (destination / "icd.json").write_text(
        json.dumps(
            {
                "file_format_version": "1.0.0",
                "ICD": {"library_path": "./libvulkan_kosmickrisp.dylib", "api_version": "1.4.0"},
            }
        )
        + "\n"
    )
    for tree, filename, name in [
        (epoxy, "COPYING", "libepoxy-LICENSE"),
        (virgl, "COPYING", "virglrenderer-LICENSE"),
        (mesa, "docs/license.rst", "Mesa-LICENSE"),
    ]:
        shutil.copy2(tree / filename, destination / name)
    licenses = destination / "licenses"
    licenses.mkdir(exist_ok=True)
    for name in ("vulkan-loader", "spirv-tools"):
        package = prefix / "opt" / name
        for file in package.glob("*LICENSE*"):
            if file.is_file():
                shutil.copy2(file, licenses / (name + "-" + file.name))


if __name__ == "__main__":
    desktop = Path(__file__).resolve().parents[3]
    if len(sys.argv) != 2:
        raise SystemExit("Usage: host.py OUTPUT_DIRECTORY")
    build_host(desktop, desktop / "build/graphics-sources", Path(sys.argv[1]).resolve())
