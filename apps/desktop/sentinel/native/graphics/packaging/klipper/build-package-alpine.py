"""Locked native Alpine source/compiler cache and fresh package staging."""

import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile

SOURCE_INPUTS = ("prepare-alpine.py", "register-startup-test.patch", "startuprestoretest.cpp")
PACKAGE_INPUTS = (
    *SOURCE_INPUTS,
    "compile-alpine.sh",
    "alpine.APKBUILD.inc",
    "normalize-alpine.cmake",
    "build-alpine.sh",
    "build-package-alpine.py",
    "verify-alpine.py",
    "test_verify_alpine.py",
)
X11_SOCKET_DIR = Path("/tmp/.X11-unix")
TEST_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def identities(inputs, patch, pin, toolchain):
    source = dict(
        schema=1,
        pins={
            name: pin[name]
            for name in ("release", "architecture", "version", "source", "recipe", "distroPatch")
        },
        inputs={name: sha(inputs / name) for name in SOURCE_INPUTS},
        patch=sha(patch),
    )
    source_key = digest(source)
    compilation = dict(
        schema=1,
        source_key=source_key,
        helper=sha(inputs / "compile-alpine.sh"),
        source_date_epoch=pin["sourceDateEpoch"],
        toolchain=toolchain,
    )
    return source, compilation


def observed_toolchain():
    tools = {}
    for name in ("cc", "c++", "ld", "cmake", "ninja", "abuild"):
        executable = shutil.which(name)
        if executable is None:
            raise ValueError("Incomplete native toolchain: " + name)
        path = Path(executable).resolve()
        tools[name] = dict(path=str(path), sha256=sha(path))
    configs = (
        Path("/etc/abuild.conf"),
        Path(os.environ.get("ABUILD_USERDIR", str(Path.home() / ".abuild"))) / "abuild.conf",
    )
    return dict(
        packages=sorted(subprocess.check_output(["apk", "info", "-v"], text=True).splitlines()),
        tools=tools,
        configs={str(p): sha(p) if p.is_file() else None for p in configs},
        environment={
            name: os.environ.get(name)
            for name in (
                "CC",
                "CXX",
                "CFLAGS",
                "CXXFLAGS",
                "CPPFLAGS",
                "LDFLAGS",
                "CHOST",
                "CBUILD",
                "CTARGET",
                "CMAKE_PREFIX_PATH",
                "CMAKE_TOOLCHAIN_FILE",
                "PKG_CONFIG_PATH",
                "PKG_CONFIG_LIBDIR",
                "ABUILD_USERDIR",
            )
        },
    )


def package_identity(inputs, patch, pin, source, compilation, recipe, public):
    value = dict(
        schema=1,
        source_key=digest(source),
        source=source,
        compile_key=digest(compilation),
        compilation=compilation,
        pins=pin,
        inputs={name: sha(inputs / name) for name in PACKAGE_INPUTS},
        patch=sha(patch),
        recipe_sha256=sha(recipe),
        signing_key_sha256=sha(public),
        qualified=False,
    )
    return dict(value, package_key=digest(value))


def reuse(compiled, compilation):
    if not compiled.exists():
        return False
    marker = compiled / "prepared.json"
    if not marker.is_file() or json.loads(marker.read_text()) != compilation:
        raise ValueError("Retained compiler tree has no matching preparation identity")
    return True


def run_regression(binary, log_path, account):
    if account.pw_uid == 0:
        raise ValueError("Klipper regression requires an ordinary test user")
    sockets = X11_SOCKET_DIR.lstat()
    if (
        sockets.st_uid != 0
        or not stat.S_ISDIR(sockets.st_mode)
        or stat.S_IMODE(sockets.st_mode) != 0o1777
    ):
        raise ValueError("Builder must provide a root-owned mode-1777 /tmp/.X11-unix directory")
    with tempfile.TemporaryDirectory(prefix="sentinel-klipper-test-", dir="/tmp") as directory:
        home = Path(directory)
        runtime = home / "runtime"
        runtime.mkdir(mode=0o700)
        for path in (home, runtime):
            os.chown(path, account.pw_uid, account.pw_gid)
        command = [
            "env",
            "-i",
            "PATH=" + TEST_PATH,
            "HOME=" + str(home),
            "XDG_RUNTIME_DIR=" + str(runtime),
            "LANG=C.UTF-8",
            "QT_QPA_PLATFORM=xcb",
            "dbus-run-session",
            "--",
            "xvfb-run",
            "-a",
            str(binary),
        ]
        with log_path.open("wb") as log:
            subprocess.run(
                ["runuser", "-u", account.pw_name, "--", *command],
                check=True,
                timeout=120,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=directory,
                env={"PATH": TEST_PATH, "LANG": "C.UTF-8"},
            )
    return dict(startuprestore="passed", user_uid=account.pw_uid, log_sha256=sha(log_path))


def build(inputs, patch, work, output):
    work.mkdir(parents=True, exist_ok=True)
    with (work / "build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        build_locked(inputs, patch, work, output)


def build_locked(inputs, patch, work, output):
    account = pwd.getpwnam(os.environ.get("SENTINEL_BUILD_TEST_USER", "sentinel-build"))
    if account.pw_uid == 0:
        raise ValueError("Klipper regression requires an ordinary test user")
    pin = json.loads((inputs / "sources.lock.json").read_text())["alpine"]
    spec = importlib.util.spec_from_file_location("prepare_alpine", inputs / "prepare-alpine.py")
    preparer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preparer)
    cache = work / "cache"
    keys, trusted = work / "keys", work / "trusted-keys"
    keys.mkdir(exist_ok=True)
    trusted.mkdir(exist_ok=True)
    private = keys / "sentinel-klipper.rsa"
    public = keys / "sentinel-klipper.rsa.pub"

    def run(*args, **kwargs):
        subprocess.run([str(arg) for arg in args], check=True, **kwargs)

    if not private.exists():
        run("openssl", "genrsa", "-out", private, "2048")
    private.chmod(0o600)
    run("openssl", "rsa", "-in", private, "-pubout", "-out", public)
    for key in [*Path("/etc/apk/keys").glob("*.pub"), public]:
        shutil.copy2(key, trusted / key.name)
    env = dict(
        os.environ,
        PACKAGER_PRIVKEY=str(private),
        PACKAGER_PUBKEY=str(public),
        APK="apk --keys-dir " + str(trusted),
        SOURCE_DATE_EPOCH=str(pin["sourceDateEpoch"]),
        USE_CCACHE="1",
        CCACHE_DIR="/var/cache/sentinel-build/ccache",
        CCACHE_MAXSIZE="8G",
        CCACHE_COMPILERCHECK="content",
    )

    def abuild(recipe, *actions):
        run(
            "abuild",
            "-F",
            "-K",
            "-r",
            "-C",
            recipe,
            "-P",
            staging / "packages",
            "-s",
            cache,
            *actions,
            env=env,
        )

    # New package staging is never allowed to unpack or patch retained sources.
    with tempfile.TemporaryDirectory(prefix="package-", dir=work) as temporary:
        staging = Path(temporary)
        preparer.prepare(inputs, patch, staging, cache=cache)
        recipe = staging / "aports/sentinel/plasma-workspace"
        abuild(recipe, "validate")
        # Installed dependency state is itself part of the compiler identity.
        # A matching warm tree needs no unconditional APK add/index refresh.
        try:
            source, compilation = identities(inputs, patch, pin, observed_toolchain())
        except ValueError:  # bootstrap tools exist, full build dependencies may not
            compilation = None
        if compilation is None or not (work / "compilations" / digest(compilation)).exists():
            abuild(recipe, "builddeps")
            source, compilation = identities(inputs, patch, pin, observed_toolchain())
        compile_key = digest(compilation)
        compiled = work / "compilations" / compile_key
        compiled.parent.mkdir(exist_ok=True)
        if not reuse(compiled, compilation):
            # Prepare at its final path: abuild can create absolute source links.
            # An interrupted preparation has no valid marker and fails closed;
            # never unpack/repatch that retained tree on a later invocation.
            preparer.prepare(inputs, patch, compiled, cache=cache, compile_only=True)
            abuild(
                compiled / "aports/sentinel/plasma-workspace",
                "fetch",
                "unpack",
                "prepare",
                "mkusers",
            )
            marker = compiled / "prepared.json.next"
            marker.write_text(json.dumps(compilation, sort_keys=True) + "\n")
            marker.replace(compiled / "prepared.json")
        compiler_recipe = compiled / "aports/sentinel/plasma-workspace"
        abuild(compiler_recipe, "build")
        regression_log = compiled / "regression.log"
        regression = run_regression(
            compiler_recipe
            / "src"
            / ("plasma-workspace-" + pin["version"])
            / "build/bin/klipper-startuprestoretest",
            regression_log,
            account,
        )
        (recipe / "src").symlink_to(compiler_recipe / "src", target_is_directory=True)
        provenance = package_identity(
            inputs, patch, pin, source, compilation, recipe / "APKBUILD", public
        )
        provenance["regression"] = regression
        package_key = provenance["package_key"]
        abuild(recipe, "rootpkg", "update_abuildrepo_index")
        destination = output / "packages/plasma-workspace-libs"
        (destination / "keys").mkdir(parents=True, exist_ok=True)
        filename = f"plasma-workspace-libs-{pin['version']}-r{pin['packageRevision']}.apk"
        package = staging / "packages/sentinel/aarch64" / filename
        shutil.copy2(package, destination / filename)
        shutil.copy2(public, destination / "keys" / public.name)
        run(
            "apk",
            "--keys-dir",
            destination / "keys",
            "--no-network",
            "--repositories-file",
            "/dev/null",
            "verify",
            destination / filename,
        )
        run(
            "python3",
            inputs / "verify-alpine.py",
            destination,
            cache / "plasma-workspace-libs-6.6.6-r0.apk",
        )
        source_dir = output / ("share/sources/plasma-workspace-" + pin["version"])
        source_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cache / "plasma-workspace-6.6.6.tar.xz", source_dir / "upstream.tar.xz")
        for path in [
            recipe / "APKBUILD",
            recipe / "APKBUILD.alpine",
            *recipe.glob("*.patch"),
            patch,
            inputs / "sources.lock.json",
            *(inputs / name for name in PACKAGE_INPUTS),
        ]:
            shutil.copy2(path, source_dir / path.name)
        (source_dir / "builder-packages.txt").write_text(
            "\n".join(compilation["toolchain"]["packages"]) + "\n"
        )
        (source_dir / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
        shutil.copy2(regression_log, source_dir / "regression.log")
        manifest_path = destination / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update(
            source_key=digest(source),
            compile_key=compile_key,
            package_key=package_key,
            regression=regression,
        )
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        retained = work / "packages" / package_key
        retained.mkdir(parents=True, exist_ok=True)
        for path in (
            destination / filename,
            manifest_path,
            source_dir / "provenance.json",
            regression_log,
        ):
            shutil.copy2(path, retained / path.name)
    print(
        "Built unqualified Alpine library package; native/Wayland qualification remains required."
    )


if __name__ == "__main__":
    build(*(Path(value).resolve() for value in sys.argv[1:]))
