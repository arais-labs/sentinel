import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


def run(command, *, env=None, timeout=120):
    print("+", " ".join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), env=env, check=True, timeout=timeout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mesa-source", type=Path, required=True)
    parser.add_argument("--mesa-build", type=Path, required=True)
    parser.add_argument("--epoxy-source", type=Path, required=True)
    parser.add_argument("--epoxy-build", type=Path, required=True)
    parser.add_argument("--library-dir", type=Path, required=True)
    parser.add_argument("--icd", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--cc", default="clang")
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("the host Metal regression requires macOS")
    if not 1 <= args.repetitions <= 100:
        parser.error("repetitions must be between 1 and 100")
    for name in (
        "MESA_GL_VERSION_OVERRIDE",
        "MESA_GLSL_VERSION_OVERRIDE",
        "MESA_EXTENSION_OVERRIDE",
        "MESA_KK_EXPERIMENTAL",
    ):
        if name in os.environ:
            parser.error(f"unset {name}; qualification must use fixed driver capabilities")
    source = Path(__file__).resolve().parent
    output = args.build_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    libraries = args.library_dir.resolve()
    egl = libraries / "libEGL.dylib"
    epoxy = libraries / "libepoxy.0.dylib"
    icd = args.icd.resolve()
    for path in (egl, epoxy, icd):
        if not path.is_file():
            parser.error(f"missing build artifact: {path}")
    private_tools = source.parents[3] / "build/graphics-sources/venv/bin"
    environment = os.environ.copy()
    environment["PATH"] = str(private_tools) + os.pathsep + environment.get("PATH", "")
    meson = shutil.which("meson", path=environment["PATH"])
    if not meson:
        parser.error("Meson is required; prepare the graphics build toolchain first")
    environment["CC"] = args.cc
    # Match queue header layout to the archive's actual feature configuration.
    entries = json.loads((args.mesa_build / "compile_commands.json").read_text())
    entry = next(item for item in entries if item["file"].endswith("/u_queue.c"))
    command = entry.get("arguments") or shlex.split(entry["command"])
    defines = [arg for arg in command if arg.startswith("-D")]
    setup = [meson, "setup"]
    if (output / "meson-private/coredata.dat").exists():
        setup.append("--reconfigure")
    setup += [output, source]
    for name in ("mesa_source", "mesa_build", "epoxy_source", "epoxy_build", "library_dir", "icd"):
        setup.append(f"-D{name}={getattr(args, name).resolve()}")
    setup += [f"-Dqueue_defines={json.dumps(defines)}", f"-Drepetitions={args.repetitions}"]
    run(setup, env=environment)
    run([meson, "compile", "-C", output], env=environment)
    # Bundled epoxy may retain an absolute or @rpath install name. Normalize only
    # freshly linked binaries; unchanged executables are left untouched.
    for name in ("mip-views", "prefragment-storage", "sparse-copies"):
        dependencies = subprocess.check_output(["otool", "-L", str(output / name)], text=True)
        for line in dependencies.splitlines()[1:]:
            dependency = line.strip().split(" (", 1)[0]
            if "libepoxy" in Path(dependency).name and dependency != str(epoxy):
                run(["install_name_tool", "-change", dependency, epoxy, output / name])
    run(
        [meson, "test", "-C", output, "--no-rebuild", "--print-errorlogs"],
        env=environment,
        timeout=120 * (args.repetitions + 6),
    )


if __name__ == "__main__":
    main()
