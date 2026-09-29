"""Prepare an isolated, pinned native recipe; no builds or installations here."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def prepare(inputs, patch, work, *, cache=None, compile_only=False):
    if (work / "prepared.json").exists():
        raise ValueError("Prepared compiler trees are immutable; reuse through the locked builder")
    pins = json.loads((inputs / "sources.lock.json").read_text())["alpine"]
    if (pins["version"], pins["packageRevision"]) != ("6.6.6", 1):
        raise ValueError("Review the native recipe before changing its version")
    recipe = work / "aports/sentinel/plasma-workspace"
    cache = cache or work / "cache"
    recipe.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    destinations = {
        "source": cache / "plasma-workspace-6.6.6.tar.xz",
        "recipe": recipe / "APKBUILD.alpine",
        "distroPatch": recipe / pins["distroPatch"]["url"].rsplit("/", 1)[1],
        "owner": cache / "plasma-workspace-libs-6.6.6-r0.apk",
    }
    for name, destination in destinations.items():
        expected = pins[name]["sha256"]
        cached = cache / destination.name
        if not cached.is_file() or hashlib.sha256(cached.read_bytes()).hexdigest() != expected:
            pending = cached.with_suffix(cached.suffix + ".part")
            subprocess.run(
                [
                    "curl",
                    "--fail",
                    "--location",
                    "--retry",
                    "2",
                    "--connect-timeout",
                    "15",
                    "--max-time",
                    "180",
                    pins[name]["url"],
                    "-o",
                    str(pending),
                ],
                check=True,
            )
            if hashlib.sha256(pending.read_bytes()).hexdigest() != expected:
                raise ValueError(f"Checksum mismatch: {name}")
            pending.replace(cached)
        if destination != cached:
            shutil.copy2(cached, destination)
    # The baseline owner must also authenticate with the distro's normal keys.
    subprocess.run(["apk", "verify", str(destinations["owner"])], check=True)
    additions = [
        patch,
        inputs / "register-startup-test.patch",
        inputs / "startuprestoretest.cpp",
        inputs / "compile-alpine.sh",
    ]
    if not compile_only:
        additions.append(inputs / "normalize-alpine.cmake")
    for addition in additions:
        shutil.copy2(addition, recipe / addition.name)
    original = (recipe / "APKBUILD.alpine").read_text()
    override = "" if compile_only else (inputs / "alpine.APKBUILD.inc").read_text()
    # Appending retains each original source checksum. Native prepare applies
    # both patches; the C++ source is copied before configuration.
    extra = '\nsource="$source\n' + "\n".join(p.name for p in additions) + '\n"\n'
    extra += 'sha512sums="$sha512sums\n'
    extra += "\n".join(
        hashlib.sha512(p.read_bytes()).hexdigest() + "  " + p.name for p in additions
    )
    extra += '\n"\nprepare() {\n\tdefault_prepare\n'
    extra += '\tinstall -m644 "$srcdir/startuprestoretest.cpp" "$builddir/klipper/autotests/"\n}\n'
    extra += 'patch_args="-p1 --fuzz=0 --batch --forward"\n'
    extra += 'build() {\n\tCFLAGS="${CFLAGS:-}" CXXFLAGS="${CXXFLAGS:-}" JOBS="$JOBS" sh "$startdir/compile-alpine.sh"\n}\n'
    content = original + "\n" + override + extra
    generated = recipe / "APKBUILD"
    # A reused work directory must never silently mix patched sources and a
    # changed recipe. The caller selects a fresh component key for input changes.
    if (work / "prepared").exists() and generated.read_text() != content:
        raise ValueError(
            "Prepared work directory belongs to different inputs; select a new component key"
        )
    generated.write_text(content)


if __name__ == "__main__":
    prepare(*(Path(value).resolve() for value in sys.argv[1:]))
