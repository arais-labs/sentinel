"""Build the privately bundled Metal renderer. Never installs host packages."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from angle import prepare_angle

source = Path(__file__).resolve().parent
desktop = source.parents[2]
work = desktop / "build/graphics-sources"
dest = (
    Path(sys.argv[1]).resolve()
    if len(sys.argv) > 1
    else desktop / "build/macos-arm64/runtime/workspace-runtime/graphics"
)
runtime_lock = (desktop / "runtime.lock.json").read_bytes()
angle_config = json.loads(runtime_lock)["platforms"]["macos-arm64"]["angle"]
stamp = hashlib.sha256(
    b"".join(p.read_bytes() for folder in [source, desktop / "native/graphics/guest", desktop / "native/graphics/patches"] for p in sorted(folder.iterdir()) if p.is_file())
    + (desktop / "package-lock.json").read_bytes()
    + runtime_lock
).hexdigest()
required = [
    "sentinel-graphics-renderer",
    "libEGL.dylib",
    "libGLESv2.dylib",
    "libepoxy.0.dylib",
    "libvirglrenderer.1.dylib",
    "install-guest.py",
    "guest-bridge.py",
    "ANGLE-LICENSE",
    "ANGLE-LICENSES.chromium.html",
]
if (
    (dest / "stamp").exists()
    and (dest / "stamp").read_text() == stamp
    and all((dest / name).is_file() for name in required)
):
    subprocess.run(
        [sys.executable, str(source / "build-guest.py"), str(dest)], check=True
    )
    subprocess.run(
        [sys.executable, str(source / "build-guest.py"), str(dest), "glibc"], check=True
    )
    sys.exit(0)
work.mkdir(parents=True, exist_ok=True)
angle = prepare_angle(work, angle_config)


def run(*args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def download(url, target):
    run(
        "curl",
        "--fail",
        "--location",
        "--retry",
        "2",
        "--connect-timeout",
        "15",
        "--max-time",
        "180",
        "--silent",
        "--show-error",
        url,
        "-o",
        target,
    )


def checkout(repo, revision, folder):
    target = work / folder
    if not target.exists():
        archive = work / (folder + ".tar.gz")
        download(f"https://codeload.github.com/{repo}/tar.gz/{revision}", archive)
        target.mkdir()
        run("tar", "xf", archive, "--strip-components=1", "-C", target)
    return target


epoxy = checkout("utmapp/libepoxy", "bf98587477fe68d07b93319ece7b40a7d0e2eabe", "epoxy")
virgl = checkout(
    "utmapp/virglrenderer", "71a67414013f120c158729da7f56f29b55bf4f6c", "virgl"
)
headers = checkout(
    "KhronosGroup/EGL-Registry",
    "5961a7fe64cf8a126890ced6f13d69e0a1e1b83e",
    "egl-registry",
)
venv = work / "venv"
if not venv.exists():
    run(sys.executable, "-m", "venv", venv)
    run(venv / "bin/pip", "install", "meson==1.12.0", "ninja==1.13.0", "PyYAML==6.0.3")

# pkgconf is a build tool, extracted privately and verified, never brew-installed.
pkg = work / "pkgconf/3.0.7"
if not pkg.exists():
    digest = "338c66b0d559ed78c2f77c038e141cd7b0060d6fcdf80affd1034509e8ab180c"
    token = json.loads(
        subprocess.check_output(
            [
                "curl",
                "-fsSL",
                "https://ghcr.io/token?service=ghcr.io&scope=repository:homebrew/core/pkgconf:pull",
            ]
        )
    )["token"]
    archive = work / "pkgconf.tar.gz"
    run(
        "curl",
        "-fsSL",
        "-H",
        "Authorization: Bearer " + token,
        "https://ghcr.io/v2/homebrew/core/pkgconf/blobs/sha256:" + digest,
        "-o",
        archive,
    )
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise RuntimeError("pkgconf checksum mismatch")
    run("tar", "xf", archive, "-C", work)
    run(
        "install_name_tool",
        "-change",
        "@@HOMEBREW_CELLAR@@/pkgconf/3.0.7/lib/libpkgconf.8.dylib",
        pkg / "lib/libpkgconf.8.dylib",
        pkg / "bin/pkgconf",
    )
    run("codesign", "-f", "-s", "-", pkg / "bin/pkgconf")

local = work / "local"
env = {
    **os.environ,
    "PATH": str(venv / "bin") + ":" + os.environ["PATH"],
    "PKG_CONFIG": str(pkg / "bin/pkgconf"),
    "PKG_CONFIG_PATH": str(local / "lib/pkgconfig"),
}


def patch(file, before, after):
    text = file.read_text()
    if after in text:
        return
    if before not in text:
        raise RuntimeError(f"Upstream patch no longer applies: {file}")
    file.write_text(text.replace(before, after, 1))


patch(
    epoxy / "src/dispatch_common.c",
    "EGL.framework/Versions/Current/EGL",
    "@loader_path/libEGL.dylib",
)
patch(
    epoxy / "src/dispatch_common.c",
    "GLESv2.framework/Versions/Current/GLESv2",
    "@loader_path/libGLESv2.dylib",
)
# Vtest's shared-memory protocol cannot pass a Linux fd into a macOS process.
patch(
    virgl / "vtest/vtest_renderer.c",
    "ctx->protocol_version = version;",
    "version = 0; /* Cross-VM byte transport, no shared file descriptors. */\n   ctx->protocol_version = version;",
)
patch(
    virgl / "src/virglrenderer.c",
    "if (!state.proxy_initialized) {",
    "if ((flags & VIRGL_RENDERER_VENUS) && !state.proxy_initialized) {",
)
patch(
    virgl / "src/vrend/vrend_winsys.c",
    "      use_context = CONTEXT_EGL;\n#else\n      (void)preferred_fd;",
    "      use_context = CONTEXT_EGL;\n#elif defined(HAVE_EPOXY_EGL_H)\n      (void)preferred_fd;\n      egl = virgl_egl_init(EGL_DEFAULT_DISPLAY, true, true);\n      if (!egl) return -1;\n      use_context = CONTEXT_EGL;\n#else\n      (void)preferred_fd;",
)
patch(
    virgl / "src/vrend/vrend_winsys_egl.c",
    "   success = eglInitialize(egl->egl_display, &major, &minor);",
    "   /* Select hardware Metal explicitly; never select SwiftShader. */\n   const EGLint metal_attrs[] = {0x3203, 0x3489, EGL_NONE};\n   egl->egl_display = egl->funcs.eglGetPlatformDisplay(0x3202, NULL, metal_attrs);\n   success = eglInitialize(egl->egl_display, &major, &minor);",
)
shutil.copy2(desktop / "native/graphics/patches/angle-multisample.h", virgl / "src/vrend/angle-multisample.h")
for name in ("vrend_formats.c", "vrend_renderer.c"):
    file = virgl / "src/vrend" / name
    patch(
        file,
        '#include "vrend_renderer.h"',
        '#include "vrend_renderer.h"\n#include "angle-multisample.h"',
    )
    file.write_text(
        file.read_text().replace(
            "glTexStorage2DMultisample(", "sentinel_tex_storage_multisample("
        )
    )
patch(
    virgl / "src/vrend/vrend_renderer.c",
    'FEAT(storage_multisample, 43, 31,  "GL_ARB_texture_storage_multisample" )',
    'FEAT(storage_multisample, 43, 31,  "GL_ARB_texture_storage_multisample", "GL_ANGLE_texture_multisample" )',
)
patch(
    virgl / "src/vrend/vrend_renderer.c",
    "if ((is_metal || is_angle) && caps->v1.max_samples > 1)",
    "if ((is_metal || is_angle) && caps->v1.max_samples > 1 && !has_feature(feat_storage_multisample))",
)
patch(
    virgl / "src/vrend/vrend_formats.c",
    "if (epoxy_gl_version() >= 31) {\n         has_tex_storage_ms = true;",
    'if (epoxy_gl_version() >= 31 || epoxy_has_gl_extension("GL_ANGLE_texture_multisample")) {\n         has_tex_storage_ms = true;',
)
# Use explicit multisample resources. The implicit resolve extension cannot
# combine the guest's depth/color attachment layouts on ANGLE Metal.
patch(
    virgl / "src/vrend/vrend_renderer.c",
    "if (has_feature(feat_implicit_msaa))\n       caps->v2.capability_bits_v2",
    "if (has_feature(feat_implicit_msaa) && !vrend_state.use_gles)\n       caps->v2.capability_bits_v2",
)
patch(
    virgl / "src/vrend/vrend_renderer.c",
    "if (pr->nr_samples <= 1 && gr->target == GL_TEXTURE_CUBE_MAP)",
    "if (pr->nr_samples > 1) { /* Multisample storage was allocated above. */\n      } else if (gr->target == GL_TEXTURE_CUBE_MAP)",
)
meson = venv / "bin/meson"
run(
    meson,
    "setup",
    "--reconfigure",
    work / "epoxy-build",
    epoxy,
    "--prefix=" + str(local),
    "-Degl=yes",
    "-Dglx=no",
    "-Dx11=false",
    "-Dtests=false",
    "-Dc_args=-I" + str(headers / "api"),
    env=env,
)
run("ninja", "-C", work / "epoxy-build", "-j2", "install", env=env)
run(
    meson,
    "setup",
    "--reconfigure",
    work / "virgl-build",
    virgl,
    "-Dplatforms=egl",
    "-Dvenus=false",
    "-Dtests=false",
    "-Dc_args=-I" + str(headers / "api"),
    "-Dobjc_link_args=['-framework','Foundation','-lobjc']",
    env=env,
)
run("ninja", "-C", work / "virgl-build", "-j2", env=env)
dest.mkdir(parents=True, exist_ok=True)
for src, name in [
    (work / "virgl-build/vtest/virgl_test_server", "sentinel-graphics-renderer"),
    (work / "virgl-build/src/libvirglrenderer.1.dylib", "libvirglrenderer.1.dylib"),
    (local / "lib/libepoxy.0.dylib", "libepoxy.0.dylib"),
    (angle / "libEGL.dylib", "libEGL.dylib"),
    (angle / "libGLESv2.dylib", "libGLESv2.dylib"),
]:
    shutil.copy2(src, dest / name)
for name in [
    "sentinel-graphics-renderer",
    "libvirglrenderer.1.dylib",
    "libepoxy.0.dylib",
]:
    file = dest / name
    if name.endswith(".dylib"):
        run("install_name_tool", "-id", "@loader_path/" + name, file)
    deps = subprocess.check_output(["otool", "-L", str(file)], text=True).splitlines()[
        1:
    ]
    for line in deps:
        dep = line.strip().split(" (", 1)[0]
        if "libepoxy" in dep:
            run(
                "install_name_tool",
                "-change",
                dep,
                "@loader_path/libepoxy.0.dylib",
                file,
            )
        elif "libvirglrenderer" in dep and name != "libvirglrenderer.1.dylib":
            run(
                "install_name_tool",
                "-change",
                dep,
                "@loader_path/libvirglrenderer.1.dylib",
                file,
            )
    run("codesign", "-f", "-s", "-", file)
for src, name in [
    (epoxy / "COPYING", "libepoxy-LICENSE"),
    (virgl / "COPYING", "virglrenderer-LICENSE"),
    (angle / "ANGLE-LICENSE", "ANGLE-LICENSE"),
    (angle / "ANGLE-LICENSES.chromium.html", "ANGLE-LICENSES.chromium.html"),
]:
    if src.exists():
        shutil.copy2(src, dest / name)
for name in ["guest-bridge.py", "install-guest.py"]:
    shutil.copy2(desktop / "native/graphics/guest" / name, dest / name)
(dest / "install-guest.sh").unlink(missing_ok=True)
run(sys.executable, source / "build-guest.py", dest)
run(sys.executable, source / "build-guest.py", dest, "glibc")
(dest / "stamp").write_text(stamp)
print("Bundled Metal renderer ready:", dest)
