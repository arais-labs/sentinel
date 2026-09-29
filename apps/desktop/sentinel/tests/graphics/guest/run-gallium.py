"""Compile internal API regressions against an ordinary, already-built guest Mesa."""

import argparse
import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("build", type=Path, help="normal patched Mesa 26.1.6 Meson build directory")
    parser.add_argument("--compile-only", action="store_true")
    parser.add_argument("--device", help="run GPU tests against this DRM device after linking")
    args = parser.parse_args()
    if args.compile_only and args.device:
        parser.error("--device cannot be combined with --compile-only")
    build = args.build.resolve()
    entries = json.loads((build / "compile_commands.json").read_text())
    entry = next(item for item in entries if item["file"].endswith("/virgl_drm_winsys.c"))
    original = entry.get("arguments") or shlex.split(entry["command"])
    command = []
    skip = False
    for item in original:
        if skip:
            skip = False
        elif item in {"-o", "-MF", "-MT", "-MQ"}:
            skip = True
        elif item not in {"-c", "-MD", "-MMD", entry["file"]}:
            command.append(item)
    archives = [
        "src/gallium/drivers/virgl/libvirgl.a",
        "src/gallium/winsys/virgl/drm/libvirgldrm.a",
        "src/gallium/winsys/virgl/common/libvirglcommon.a",
        "src/gallium/auxiliary/libgallium.a",
        "src/gallium/auxiliary/libgalliumvl.a",
        "src/compiler/nir/libnir.a",
        "src/compiler/libcompiler.a",
        "src/compiler/spirv/libvtn.a",
        "src/util/libmesa_util.a",
        "src/util/libmesa_util_simd.a",
        "src/util/blake3/libblake3.a",
        "src/c11/impl/libmesa_util_c11.a",
        "src/util/libxmlconfig.a",
        "src/compiler/glsl/libglsl.a",
        "src/compiler/glsl/glcpp/libglcpp.a",
        "src/mesa/glapi/shared-glapi/libglapi.a",
    ]
    tests = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="sentinel-gallium-tests-") as temporary:
        output = Path(temporary)
        for name in (
            "gallium-coherent-compute",
            "gallium-coherent-draw",
            "gallium-query-persistent",
        ):
            obj = output / f"{name}.o"
            subprocess.run(
                command + ["-Werror", "-c", str(tests / f"{name}.c"), "-o", str(obj)],
                cwd=entry["directory"],
                check=True,
            )
            if args.compile_only:
                print(f"PASS compiled {name}", flush=True)
                continue
            for archive in archives:
                if not (build / archive).is_file():
                    raise FileNotFoundError(build / archive)
            binary = output / name
            subprocess.run(
                [
                    os.environ.get("CXX", "c++"),
                    "-o",
                    str(binary),
                    str(obj),
                    "-Wl,--gc-sections",
                    "-Wl,--start-group",
                    *archives,
                    "-Wl,--end-group",
                    "-ldrm",
                    "-lexpat",
                    "-lz",
                    "-lzstd",
                    "-lm",
                    "-pthread",
                    "-ldl",
                ],
                cwd=build,
                check=True,
            )
            print(f"PASS linked {name} from unmodified Meson build outputs", flush=True)
            if args.device:
                subprocess.run([str(binary), args.device], check=True, timeout=90)


if __name__ == "__main__":
    main()
