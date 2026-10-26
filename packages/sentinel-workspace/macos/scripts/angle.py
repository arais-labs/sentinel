"""ANGLE (OpenGL ES on Metal) for virglrenderer's GL renderer: libEGL.dylib and libGLESv2.dylib.

Upstream ANGLE's own gn build, Metal backend only. The ANGLE tree and the few DEPS entries a
Metal-only build reads are pinned gitiles archives (sources.json "angle"), gn is CIPD's pinned
binary; nothing is a git checkout and nothing is installed. Compiled with the pinned LLVM of the
build tools (Chromium's build files need a recent clang) against Xcode's SDK, linked with ld64.
patches/angle-*.patch apply on top. libEGL opens libGLESv2 from its own directory at run time.
"""

import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from toolchain import run

PATCHES = Path(__file__).resolve().parent.parent / "patches"

# Chromium gclient variables ANGLE's build files read (DEPS gclient_gn_args, for a checkout
# without the optional OpenCL, Dawn and trace dependencies).
GCLIENT_ARGS = """android_ndk_version = "2@30.0.16248370"
checkout_angle_cl_deps = false
checkout_angle_dawn_deps = false
checkout_angle_internal = false
checkout_angle_mesa = false
checkout_angle_partition_alloc = false
checkout_angle_restricted_traces = false
checkout_extra_traces = false
generate_location_tags = false
"""


def _download(url, archive):
    if archive.is_file():
        return
    partial = archive.with_suffix(".download")
    run("curl", "-fsSL", "--retry", "2", "--max-time", "900", url, "-o", partial)
    partial.replace(archive)


def _source(work, pin):
    """The ANGLE tree with its pinned dependencies and patches, unpacked once per pin."""
    patches = sorted(PATCHES.glob("angle-*.patch"))
    key = json.dumps(pin, sort_keys=True) + "".join(p.name + p.read_text() for p in patches)
    tree = work / "angle"
    stamp = tree / ".sentinel-source"
    if stamp.is_file() and stamp.read_text() == key:
        return tree
    downloads = work / "angle-downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(tree, ignore_errors=True)
    tree.mkdir(parents=True)
    # Gitiles archives hold the tree's contents without a top-level folder.
    for path, dep in {".": pin, **pin["deps"]}.items():
        digest = hashlib.sha256(dep["url"].encode()).hexdigest()[:16]
        archive = downloads / f"{Path(path).name or 'angle'}-{digest}.tar.gz"
        _download(dep["url"], archive)
        target = tree / path
        target.mkdir(parents=True, exist_ok=True)
        run("tar", "xzf", archive, "-C", target)
    gn = downloads / f"gn-{pin['gn']['revision']}.zip"
    _download(pin["gn"]["url"], gn)
    gn_dir = tree / "buildtools/mac"
    gn_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(gn) as bundle:
        bundle.extract("gn", gn_dir)
    (gn_dir / "gn").chmod(0o755)
    (tree / "build/config/gclient_args.gni").write_text(GCLIENT_ARGS)
    for patch in patches:
        run("patch", "-s", "-p1", "--fuzz=0", "-i", patch, cwd=tree)
    stamp.write_text(key)
    return tree


def angle(work, env, pin):
    """Builds ANGLE into WORK/angle-build; returns (folder with libEGL.dylib and libGLESv2.dylib,
    include folder with the EGL/GLES headers)."""
    tree = _source(work, pin)
    clang = shutil.which("clang", path=env["PATH"])
    if not clang:
        raise RuntimeError("angle: the build tools' clang is not on PATH")
    llvm = Path(clang).resolve().parent.parent
    (clang_version,) = [p.name for p in (llvm / "lib/clang").iterdir()]
    args = {
        "is_debug": False,
        "is_component_build": False,
        "symbol_level": 0,
        "treat_warnings_as_errors": False,
        "clang_base_path": str(llvm),
        "clang_version": clang_version,
        "clang_use_chrome_plugins": False,
        "use_lld": False,
        "use_custom_libcxx": False,
        "use_system_xcode": True,
        "use_siso": False,
        "use_reclient": False,
        "enable_rust": False,
        "angle_enable_metal": True,
        "angle_enable_vulkan": False,
        "angle_enable_gl": False,
        "angle_enable_null": False,
        "angle_enable_swiftshader": False,
        "angle_enable_wgpu": False,
        "angle_enable_cl": False,
        "angle_enable_abseil": False,
        "angle_enable_perfetto": False,
        "angle_build_tests": False,
        "build_angle_deqp_tests": False,
        "angle_has_frame_capture": False,
        "angle_has_histograms": False,
        "angle_has_astc_encoder": False,
        "angle_build_vulkan_system_info": False,
        "angle_metal_precompile_internal_shaders": False,
    }

    def value(v):
        return ("true" if v else "false") if isinstance(v, bool) else json.dumps(v)

    folder = work / "angle-build"
    folder.mkdir(parents=True, exist_ok=True)
    text = "".join(f"{k} = {value(v)}\n" for k, v in args.items())
    args_file = folder / "args.gn"
    if not args_file.is_file() or args_file.read_text() != text:
        args_file.write_text(text)
    python = shutil.which("python3", path=env["PATH"])
    run(
        tree / "buildtools/mac/gn",
        "gen",
        folder,
        "--script-executable=" + python,
        cwd=tree,
        env=env,
    )
    run(shutil.which("ninja", path=env["PATH"]), "-C", folder, "libEGL", "libGLESv2", env=env)
    return folder, tree / "include"
