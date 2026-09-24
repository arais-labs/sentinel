"""Build an unqualified, distro-native Klipper DEB in a retained Linux builder.

No production hook calls this script. It never installs the resulting package.
"""

import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

TOOLS = [
    "python3",
    "curl",
    "ca-certificates",
    "dpkg-dev",
    "build-essential",
    "cmake",
    "ninja-build",
    "ccache",
    "devscripts",
    "xvfb",
    "xauth",
    "xdotool",
    "dbus-x11",
    "wayland-protocols",
    "patch",
    "binutils",
    "util-linux",
]
COMPILATION_INPUTS = ["build-deb.sh"]
PACKAGING_INPUTS = ["package-deb.sh", "build-package-deb.py"]
INPUTS = [
    "sources.lock.json",
    "prepare-deb.py",
    *COMPILATION_INPUTS,
    *PACKAGING_INPUTS,
    "register-startup-test.patch",
    "startuprestoretest.cpp",
]
PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(*command, **kwargs):
    return subprocess.run([str(item) for item in command], check=True, **kwargs)


def text(*command, **kwargs):
    return run(*command, capture_output=True, text=True, **kwargs).stdout.strip()


def logged(log_path, *command, **kwargs):
    """Retain evidence without hiding compiler/test progress from the builder."""
    arguments = [str(item) for item in command]
    with (
        log_path.open("wb") as log,
        subprocess.Popen(
            arguments, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs
        ) as process,
    ):
        for line in process.stdout:
            log.write(line)
            log.flush()
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
        if process.wait():
            raise subprocess.CalledProcessError(process.returncode, arguments)


def validate_builder(distribution, pin, release, architecture, image, uid):
    if release.get("ID") != distribution or release.get("VERSION_ID") != pin["release"]:
        raise ValueError("Builder distribution/release does not match the source pin")
    if architecture != "arm64" or pin["architecture"] != architecture:
        raise ValueError("Klipper DEB builds require native arm64")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[0-9a-f]{64}", image):
        raise ValueError("Builder image must be digest-pinned")
    if uid == 0:
        raise ValueError("Regression user must be an existing ordinary account")


def identity(pin, files, toolchain, image):
    # Complete package identity, retained for transport/provenance verification.
    value = {
        "schema": 1,
        "source": pin,
        "inputs": {name: sha(path) for name, path in files.items() if name != "sources.lock.json"},
        "toolchain": toolchain,
        "declared_builder_image": image,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest(), value


def compilation_identity(source_key, files, toolchain, image, environment):
    # prepare-deb owns the source key: upstream/distro pins, preparation logic,
    # every applied patch and regression source. Do not duplicate that policy.
    if not re.fullmatch(r"[0-9a-f]{64}", source_key):
        raise ValueError("Missing or invalid prepared source identity")
    value = {
        "schema": 1,
        "source_key": source_key,
        "inputs": {name: sha(files[name]) for name in COMPILATION_INPUTS},
        "toolchain": toolchain,
        "declared_builder_image": image,
        "environment": dict(environment),
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest(), value


def control(package, env):
    fields = [
        "Package",
        "Version",
        "Architecture",
        "Depends",
        "Pre-Depends",
        "Provides",
        "Conflicts",
        "Breaks",
        "Replaces",
        "Multi-Arch",
    ]
    return {field: text("dpkg-deb", "--field", package, field, env=env) for field in fields}


def verify_package(current, baseline, pin):
    for field, expected in {
        "Package": pin["package"],
        "Architecture": "arm64",
        "Version": pin["sourceVersion"] + "+sentinel1",
    }.items():
        if current[field] != expected:
            raise ValueError(f"Unexpected package {field}: {current[field]}")
    for field in (
        "Depends",
        "Pre-Depends",
        "Provides",
        "Conflicts",
        "Breaks",
        "Replaces",
        "Multi-Arch",
    ):
        if normalized_dependencies(current[field]) != normalized_dependencies(baseline[field]):
            raise ValueError(
                f"Native package {field} changed; review the distro ABI/dependency closure"
            )


def normalized_dependencies(value):
    return sorted(" ".join(part.split()) for part in value.split(",") if part.strip())


def payload_inventory(contents):
    inventory = {}
    with tarfile.open(fileobj=io.BytesIO(contents)) as archive:
        for member in archive:
            if member.isdir():
                continue
            path = member.name.removeprefix("./")
            if path.startswith("/") or ".." in Path(path).parts or path in inventory:
                raise ValueError("Unsafe or duplicate native package path")
            if member.issym():
                inventory[path] = ("symlink", member.linkname)
            elif member.isfile():
                inventory[path] = ("file", "")
                if "/libklipper.so." in path:
                    header = archive.extractfile(member).read(20)
                    if (
                        len(header) != 20
                        or header[:6] != b"\x7fELF\x02\x01"
                        or int.from_bytes(header[16:18], "little") != 3
                        or int.from_bytes(header[18:20], "little") != 183
                    ):
                        raise ValueError("Klipper payload must be an AArch64 shared ELF library")
            else:
                raise ValueError("Unexpected native package file type")
    return inventory


def build(args):
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ValueError("Run only as root in a dedicated native Linux builder")
    pin = json.loads((args.inputs / "sources.lock.json").read_text())[args.distribution]
    account = pwd.getpwnam(args.test_user)
    env = {
        "PATH": PATH,
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "DEBIAN_FRONTEND": "noninteractive",
        "DEB_BUILD_MAINT_OPTIONS": "hardening=+all",
    }
    validate_builder(
        args.distribution,
        pin,
        platform.freedesktop_os_release(),
        text("dpkg", "--print-architecture", env=env),
        args.builder_image,
        account.pw_uid,
    )
    if platform.machine() not in {"aarch64", "arm64"}:
        raise ValueError("Cross-distro/cross-architecture compilation is not supported")
    root = args.work / args.distribution
    root.mkdir(parents=True, exist_ok=True)
    with (root / "build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        build_locked(args, pin, account, env, root)


def build_locked(args, pin, account, env, root):
    updated = False

    def update_indexes():
        nonlocal updated
        if not updated:
            run(
                "apt-get",
                "-o",
                "Acquire::Retries=3",
                "-o",
                "APT::Update::Error-Mode=any",
                "update",
                env=env,
            )
            updated = True

    missing = [
        name
        for name in TOOLS
        if subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", name], env=env, capture_output=True, text=True
        ).stdout.strip()
        != "install ok installed"
    ]
    if missing:
        update_indexes()
        run("apt-get", "install", "-y", "--no-install-recommends", *missing, env=env)
    source_cache = root / "source"
    source = Path(
        text(
            "python3",
            args.inputs / "prepare-deb.py",
            args.distribution,
            "--inputs",
            args.inputs,
            "--patch",
            args.patch,
            "--work",
            source_cache,
            env=env,
        )
    )
    # Resolve build dependencies from the pinned local distro control, not the
    # repository's latest plasma-workspace source or a blanket upgrade.
    if subprocess.run(["dpkg-checkbuilddeps"], cwd=source, env=env).returncode:
        update_indexes()
        run("apt-get", "build-dep", "-y", "--no-install-recommends", source, env=env)
    run("dpkg-checkbuilddeps", cwd=source, env=env)

    # Keep an APT-authenticated exact-version owner as the ABI/dependency oracle.
    baseline_dir = root / "baselines" / pin["sourceVersion"]
    baseline_dir.mkdir(parents=True, exist_ok=True)
    baseline = baseline_dir / "owner.deb"
    checksum = baseline_dir / "sha256"
    if (
        not baseline.is_file()
        or not checksum.is_file()
        or sha(baseline) != checksum.read_text().strip()
    ):
        update_indexes()
        with tempfile.TemporaryDirectory(prefix="download-", dir=baseline_dir) as directory:
            run(
                "apt-get",
                "download",
                pin["package"] + "=" + pin["sourceVersion"],
                cwd=directory,
                env=env,
            )
            packages = list(Path(directory).glob("*.deb"))
            if len(packages) != 1:
                raise ValueError("Expected one authenticated native owner package")
            packages[0].replace(baseline)
        checksum.write_text(sha(baseline) + "\n")
    original = control(baseline, env)
    if any(
        original[field] != value
        for field, value in {
            "Package": pin["package"],
            "Version": pin["sourceVersion"],
            "Architecture": "arm64",
        }.items()
    ):
        raise ValueError("Baseline owner does not match pinned distro source")
    # In particular, reject a newer incompatible Qt private ABI before building.
    run("dpkg-checkbuilddeps", "-d", original["Depends"], "-c", "", cwd=source, env=env)

    files = {name: args.inputs / name for name in INPUTS}
    files[args.patch.name] = args.patch
    if args.distribution == "debian":
        files["debian-arm64-symbols.patch"] = args.inputs / "debian-arm64-symbols.patch"
    inventory = text(
        "dpkg-query",
        "-W",
        "-f=${binary:Package}\t${Version}\t${Architecture}\t${Status}\n",
        env=env,
    )
    toolchain = {
        "packages": sorted(inventory.splitlines()),
        "baseline_sha256": sha(baseline),
        "compiler": text("c++", "--version", env=env),
        "cmake": text("cmake", "--version", env=env),
        "flags": text("dpkg-buildflags", "--export=sh", env=env),
    }
    toolchain["executables"] = {}
    for name in ("cc", "c++", "ld", "cmake", "ninja"):
        executable = Path(shutil.which(name, path=PATH)).resolve()
        toolchain["executables"][name] = {"path": str(executable), "sha256": sha(executable)}
    key, provenance = identity(pin, files, toolchain, args.builder_image)
    source_key = (source / ".sentinel-source").read_text().strip()
    compile_key, compilation = compilation_identity(
        source_key, files, toolchain, args.builder_image, env
    )
    # The distro-wide lock covers shared source and compiler trees as well as
    # packaging. Package-only edits get fresh evidence/staging, not new objects.
    build_tree = root / "compilations" / compile_key / "build"
    build_tree.parent.mkdir(parents=True, exist_ok=True)
    component = root / "components" / key
    component.mkdir(parents=True, exist_ok=True)
    env.update(
        CCACHE_DIR=str(args.ccache),
        CCACHE_COMPILERCHECK="content",
        CCACHE_MAXSIZE="4G",
        SENTINEL_BUILD_JOBS=str(args.jobs),
    )
    logged(component / "build.log", "sh", args.inputs / "build-deb.sh", source, build_tree, env=env)
    # Leave the account's real HOME/profile entirely alone.
    with tempfile.TemporaryDirectory(prefix="sentinel-klipper-test-", dir="/tmp") as directory:
        home = Path(directory)
        runtime = home / "runtime"
        runtime.mkdir(mode=0o700)
        for path in (home, runtime):
            os.chown(path, account.pw_uid, account.pw_gid)
        logged(
            component / "regression.log",
            "runuser",
            "-u",
            args.test_user,
            "--",
            "env",
            "-i",
            "PATH=" + PATH,
            "HOME=" + str(home),
            "XDG_RUNTIME_DIR=" + str(runtime),
            "LANG=C.UTF-8",
            "QT_QPA_PLATFORM=xcb",
            "dbus-run-session",
            "--",
            "xvfb-run",
            "-a",
            build_tree / "bin/klipper-startuprestoretest",
            env=env,
            cwd=directory,
        )
    # The original helper retains strict symbols (-c4) and native shlibdeps.
    with tempfile.TemporaryDirectory(prefix="package-", dir=component) as directory:
        package_output = Path(directory)
        logged(
            component / "package.log",
            "sh",
            args.inputs / "package-deb.sh",
            source,
            build_tree,
            package_output,
            env=env,
        )
        packages = list(package_output.glob("*.deb"))
        if len(packages) != 1:
            raise ValueError("Expected only the native libklipper6 package")
        package = packages[0]
        current = control(package, env)
        verify_package(current, original, pin)
        original_files = payload_inventory(
            run("dpkg-deb", "--fsys-tarfile", baseline, env=env, capture_output=True).stdout
        )
        current_files = payload_inventory(
            run("dpkg-deb", "--fsys-tarfile", package, env=env, capture_output=True).stdout
        )
        if current_files != original_files:
            raise ValueError(
                "Native package file inventory changed; review owning package completeness"
            )
        provenance.update(
            component_key=key,
            source_key=source_key,
            compile_key=compile_key,
            compilation=compilation,
            baseline_control=original,
            package_control=current,
            files=current_files,
            regression={"startuprestore": "passed", "user_uid": account.pw_uid},
            qualified=False,
        )
        provenance_path = component / "provenance.json"
        provenance_path.write_text(json.dumps(provenance, indent=2) + "\n")
        source_relative = (
            Path("share/sources") / ("klipper-" + args.distribution) / "corresponding-source.tar.xz"
        )
        source_bundle = component / "corresponding-source.tar.xz"
        with tarfile.open(source_bundle, "w:xz") as archive:
            for name, path in files.items():
                archive.add(path, arcname="recipe/" + name)
            for name in pin["files"]:
                downloaded = source_cache / "downloads" / name
                if sha(downloaded) != pin["files"][name]:
                    raise ValueError("Corresponding source checksum mismatch: " + name)
                archive.add(downloaded, arcname="upstream/" + name)
            changelogs = list(package_output.glob("package.*/debian/changelog"))
            if len(changelogs) != 1:
                raise ValueError("Missing downstream package changelog")
            archive.add(changelogs[0], arcname="packaging/changelog")
            for name in ("provenance.json", "build.log", "regression.log", "package.log"):
                archive.add(component / name, arcname="provenance/" + name)
        destination = args.output / "packages/libklipper6"
        destination.mkdir(parents=True, exist_ok=True)
        for original_path, target in (
            (package, destination / package.name),
            (source_bundle, args.output / source_relative),
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            staged = target.with_name(target.name + ".next")
            shutil.copy2(original_path, staged)
            staged.replace(target)
        manifest = dict(
            schema=1,
            distribution=args.distribution,
            release=pin["release"],
            architecture="arm64",
            name=pin["package"],
            version=current["Version"],
            deb=package.name,
            sha256=sha(package),
            component_key=key,
            source_key=source_key,
            compile_key=compile_key,
            declared_builder_image=args.builder_image,
            source=str(source_relative),
            source_sha256=sha(source_bundle),
            dependencies=current["Depends"],
            regression="passed",
            qualified=False,
        )
        staged = destination / "manifest.json.next"
        staged.write_text(json.dumps(manifest, indent=2) + "\n")
        staged.replace(destination / "manifest.json")
    print(json.dumps(manifest, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("distribution", choices=("ubuntu", "debian"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument(
        "--builder-image", required=True, help="Caller-declared image digest, recorded as a claim"
    )
    parser.add_argument("--test-user", required=True, help="Existing non-root account")
    parser.add_argument("--ccache", type=Path, default=Path("/var/cache/sentinel-build/ccache"))
    parser.add_argument("--inputs", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument(
        "--patch",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "patches/klipper/0001-startup-restore-only-empty.patch",
    )
    parser.add_argument(
        "--jobs", type=int, default=int(os.environ.get("SENTINEL_BUILD_JOBS", os.cpu_count() or 4))
    )
    args = parser.parse_args()
    if args.jobs <= 0 or not all(
        path.is_absolute() for path in (args.output, args.work, args.ccache)
    ):
        parser.error("Output/work/ccache must be absolute paths and jobs must be positive")
    args.inputs, args.patch = args.inputs.resolve(), args.patch.resolve()
    build(args)


if __name__ == "__main__":
    main()
