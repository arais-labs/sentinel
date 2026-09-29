"""Host checks for native Alpine identity boundaries; no APK installs or builds."""

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent
SCRIPT = (ROOT / "build-alpine.sh").read_text()
spec = importlib.util.spec_from_file_location(
    "build_package_alpine", ROOT / "build-package-alpine.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class AlpineBuildTests(unittest.TestCase):
    account = SimpleNamespace(pw_name="sentinel-build", pw_uid=1000, pw_gid=1000)

    def test_regression_runs_as_ordinary_user_with_private_profile_and_retained_log(self):
        sockets = Mock()
        sockets.lstat.return_value = SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o1777)
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "regression.log"

            def run(command, **kwargs):
                self.assertEqual(command[:4], ["runuser", "-u", "sentinel-build", "--"])
                arguments = command[4:]
                self.assertEqual(arguments[:2], ["env", "-i"])
                environment = dict(arg.split("=", 1) for arg in arguments[2:7])
                home, runtime = Path(environment["HOME"]), Path(environment["XDG_RUNTIME_DIR"])
                self.assertEqual(runtime.parent, home)
                self.assertTrue(home.is_dir() and runtime.is_dir())
                self.assertEqual(stat.S_IMODE(runtime.stat().st_mode), 0o700)
                self.assertEqual(kwargs["cwd"], str(home))
                self.assertEqual(
                    arguments[-5:], ["dbus-run-session", "--", "xvfb-run", "-a", "/build/test"]
                )
                self.assertTrue(kwargs["check"])
                self.assertEqual(kwargs["timeout"], 120)
                kwargs["stdout"].write(b"3 passed\n")

            with (
                patch.object(builder, "X11_SOCKET_DIR", sockets),
                patch.object(builder.os, "chown") as chown,
                patch.object(builder.subprocess, "run", side_effect=run),
            ):
                result = builder.run_regression(Path("/build/test"), log, self.account)
                self.assertEqual(chown.call_count, 2)
                private_home = chown.call_args_list[0].args[0]
            self.assertFalse(private_home.exists())
            self.assertEqual(log.read_bytes(), b"3 passed\n")
            self.assertEqual(
                result, dict(startuprestore="passed", user_uid=1000, log_sha256=builder.sha(log))
            )

    def test_regression_root_and_unsafe_x11_directory_rejected(self):
        with self.assertRaises(ValueError):
            builder.run_regression(Path("/binary"), Path("/unused"), SimpleNamespace(pw_uid=0))
        sockets = Mock()
        sockets.lstat.return_value = SimpleNamespace(st_uid=1000, st_mode=stat.S_IFDIR | 0o1777)
        with patch.object(builder, "X11_SOCKET_DIR", sockets), self.assertRaises(ValueError):
            builder.run_regression(Path("/binary"), Path("/unused"), self.account)
        with (
            patch.dict(os.environ, {"SENTINEL_BUILD_TEST_USER": "root-alias"}),
            patch.object(builder.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=0)) as lookup,
        ):
            with self.assertRaises(ValueError):
                builder.build_locked(
                    Path("/unused"), Path("/unused"), Path("/unused"), Path("/unused")
                )
            lookup.assert_called_once_with("root-alias")

    def test_regression_nonzero_and_timeout_fail_closed_with_log_retained(self):
        sockets = Mock()
        sockets.lstat.return_value = SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o1777)
        for failure in (
            subprocess.CalledProcessError(1, "test"),
            subprocess.TimeoutExpired("test", 120),
        ):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                log = Path(directory) / "regression.log"

                def run(*args, **kwargs):
                    kwargs["stdout"].write(b"failed evidence\n")
                    raise failure

                with (
                    patch.object(builder, "X11_SOCKET_DIR", sockets),
                    patch.object(builder.os, "chown"),
                    patch.object(builder.subprocess, "run", side_effect=run),
                    self.assertRaises(type(failure)),
                ):
                    builder.run_regression(Path("/build/test"), log, self.account)
                self.assertEqual(log.read_bytes(), b"failed evidence\n")

    def test_all_pinned_downloads_are_shared_but_verified_for_fresh_recipes(self):
        spec = importlib.util.spec_from_file_location(
            "prepare_alpine_test", ROOT / "prepare-alpine.py"
        )
        preparer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(preparer)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs, cache = root / "inputs", root / "cache"
            inputs.mkdir()
            for name in (*builder.PACKAGE_INPUTS, "startup.patch"):
                (inputs / name).write_text(name)
            pins = json.loads((ROOT / "sources.lock.json").read_text())
            downloads = {}
            for name in ("source", "recipe", "distroPatch", "owner"):
                value = pins["alpine"][name]
                data = ("pinned fixture " + name).encode()
                value["sha256"] = hashlib.sha256(data).hexdigest()
                downloads[value["url"]] = data
            (inputs / "sources.lock.json").write_text(json.dumps(pins))
            fetched = []

            def run(command, **kwargs):
                if command[0] == "curl":
                    url = command[command.index("-o") - 1]
                    fetched.append(url)
                    Path(command[-1]).write_bytes(downloads[url])

            with patch.object(preparer.subprocess, "run", side_effect=run):
                for name in ("first", "second"):
                    preparer.prepare(inputs, inputs / "startup.patch", root / name, cache=cache)
                self.assertEqual(len(fetched), 4)
                (cache / "APKBUILD.alpine").write_text("corrupted")
                preparer.prepare(inputs, inputs / "startup.patch", root / "third", cache=cache)
                self.assertEqual(len(fetched), 5)
                (root / "first/prepared.json").write_text("{}")
                with self.assertRaisesRegex(ValueError, "immutable"):
                    preparer.prepare(inputs, inputs / "startup.patch", root / "first", cache=cache)

    def test_job_override_is_preserved_and_invalid_counts_rejected(self):
        prefix = SCRIPT.split("output=$1", 1)[0] + '\nprintf "%s" "$JOBS"\n'
        for jobs in ("1", "4", "16"):
            result = subprocess.run(
                ["sh", "-c", prefix],
                env={**os.environ, "SENTINEL_BUILD_JOBS": jobs},
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(result.stdout, jobs)
        for jobs in ("0", "00", "-1", "1.5", "all"):
            result = subprocess.run(
                ["sh", "-c", prefix],
                env={**os.environ, "SENTINEL_BUILD_JOBS": jobs},
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)

    def test_reuse_requires_exact_prepared_identity_and_leaves_tree_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "compile"
            identity = {"source_key": "a", "toolchain": {"compiler": "b"}}
            self.assertFalse(builder.reuse(root, identity))
            root.mkdir()
            marker = root / "prepared.json"
            for content in (None, "", "{}", json.dumps(identity)):
                if content is not None:
                    marker.write_text(content)
                if content == json.dumps(identity):
                    self.assertTrue(builder.reuse(root, identity))
                else:
                    with self.assertRaises(ValueError):
                        builder.reuse(root, identity)
                self.assertEqual(marker.read_text() if marker.exists() else None, content)

    def test_source_compile_and_package_inputs_have_separate_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (*builder.PACKAGE_INPUTS, "startup.patch"):
                (root / name).write_text(name)
            pin = json.loads((ROOT / "sources.lock.json").read_text())["alpine"]
            tools = {"packages": ["qt-6"], "tools": {"c++": {"sha256": "a"}}}

            def keys(current_pin=pin, current_tools=tools):
                source, compilation = builder.identities(
                    root, root / "startup.patch", current_pin, current_tools
                )
                return builder.digest(source), builder.digest(compilation)

            initial = keys()
            (root / "APKBUILD").write_text("generated recipe")
            (root / "key.pub").write_text("signing key")

            def package_key(current_pin=pin):
                source, compilation = builder.identities(
                    root, root / "startup.patch", current_pin, tools
                )
                return builder.package_identity(
                    root,
                    root / "startup.patch",
                    current_pin,
                    source,
                    compilation,
                    root / "APKBUILD",
                    root / "key.pub",
                )["package_key"]

            original_package = package_key()
            for name in (
                "alpine.APKBUILD.inc",
                "normalize-alpine.cmake",
                "build-package-alpine.py",
                "verify-alpine.py",
            ):
                before = builder.sha(root / name)
                (root / name).write_text("packaging change")
                self.assertNotEqual(builder.sha(root / name), before)
                self.assertEqual(keys(), initial)
                self.assertNotEqual(package_key(), original_package)
                (root / name).write_text(name)
            self.assertEqual(keys({**pin, "packageRevision": 2}), initial)
            self.assertNotEqual(package_key({**pin, "packageRevision": 2}), original_package)
            for name in (*builder.SOURCE_INPUTS, "startup.patch"):
                original = (root / name).read_text()
                (root / name).write_text("source change")
                self.assertNotEqual(keys()[0], initial[0])
                self.assertNotEqual(keys()[1], initial[1])
                (root / name).write_text(original)
            (root / "compile-alpine.sh").write_text("new flags and targets")
            self.assertEqual(keys()[0], initial[0])
            self.assertNotEqual(keys()[1], initial[1])
            (root / "compile-alpine.sh").write_text("compile-alpine.sh")
            self.assertNotEqual(keys(current_tools={**tools, "packages": ["qt-7"]})[1], initial[1])
            self.assertNotEqual(
                keys({**pin, "sourceDateEpoch": pin["sourceDateEpoch"] + 1})[1], initial[1]
            )

    def test_observed_toolchain_tracks_binary_content_flags_and_package_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compiler = root / "compiler"
            compiler.write_text("compiler one")
            with (
                patch.object(builder.shutil, "which", return_value=str(compiler)),
                patch.object(builder.subprocess, "check_output", return_value="qt-6\ngcc-1\n"),
                patch.object(builder.Path, "home", return_value=root),
                patch.dict(os.environ, {"CFLAGS": "-O2"}, clear=True),
            ):
                first = builder.observed_toolchain()
                self.assertEqual(first["packages"], ["gcc-1", "qt-6"])
                compiler.write_text("compiler two")
                self.assertNotEqual(builder.observed_toolchain()["tools"], first["tools"])
                os.environ["CFLAGS"] = "-O3"
                self.assertNotEqual(
                    builder.observed_toolchain()["environment"], first["environment"]
                )

    def test_packaging_retry_links_same_compiled_source_without_preparing_it_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs, work, output = root / "inputs", root / "work", root / "output"
            inputs.mkdir()
            work.mkdir()
            for name in (*builder.PACKAGE_INPUTS, "startup.patch"):
                (inputs / name).write_text(name)
            pin = json.loads((ROOT / "sources.lock.json").read_text())
            (inputs / "sources.lock.json").write_text(json.dumps(pin))
            # A tiny source-preparation stand-in; no downloads or patch commands.
            (inputs / "prepare-alpine.py").write_text("""
def prepare(inputs, patch, work, *, cache, compile_only=False):
    recipe = work / 'aports/sentinel/plasma-workspace'
    recipe.mkdir(parents=True)
    cache.mkdir(exist_ok=True)
    (cache / 'plasma-workspace-6.6.6.tar.xz').write_text('upstream')
    (recipe / 'APKBUILD').write_text('compile recipe' if compile_only else (inputs / 'alpine.APKBUILD.inc').read_text())
    (recipe / 'APKBUILD.alpine').write_text('original')
""")
            actions, linked_sources = [], []
            fail_regression = False

            def run(command, **kwargs):
                if command[:2] == ["openssl", "genrsa"]:
                    Path(command[command.index("-out") + 1]).write_text("private")
                elif command[:2] == ["openssl", "rsa"]:
                    Path(command[command.index("-out") + 1]).write_text("public")
                elif command[0] == "abuild":
                    recipe = Path(command[command.index("-C") + 1])
                    action = command[command.index("-s") + 2 :]
                    actions.append(action)
                    if "prepare" in action:
                        (recipe / "src").mkdir()
                    if "rootpkg" in action:
                        linked_sources.append((recipe / "src").resolve())
                        package = (
                            Path(command[command.index("-P") + 1])
                            / "sentinel/aarch64/plasma-workspace-libs-6.6.6-r1.apk"
                        )
                        package.parent.mkdir(parents=True)
                        package.write_text("candidate")
                elif command[0] == "python3":
                    (Path(command[2]) / "manifest.json").write_text('{"qualified": false}')
                elif command[0] == "runuser":
                    actions.append(["regression"])
                    kwargs["stdout"].write(b"regression evidence")
                    if fail_regression:
                        raise subprocess.CalledProcessError(1, command)

            sockets = Mock()
            sockets.lstat.return_value = SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o1777)
            with (
                patch.object(builder.subprocess, "run", side_effect=run),
                patch.object(builder.pwd, "getpwnam", return_value=self.account),
                patch.object(builder.os, "chown"),
                patch.object(builder, "X11_SOCKET_DIR", sockets),
                patch.object(builder, "observed_toolchain", return_value={"packages": ["fixture"]}),
            ):
                builder.build(inputs, inputs / "startup.patch", work, output)
                (inputs / "alpine.APKBUILD.inc").write_text("new package only recipe")
                builder.build(inputs, inputs / "startup.patch", work, output)
                fail_regression = True
                with self.assertRaises(subprocess.CalledProcessError):
                    builder.build(inputs, inputs / "startup.patch", work, root / "failed-output")
                self.assertFalse((root / "failed-output").exists())
            self.assertEqual(linked_sources[0], linked_sources[1])
            self.assertEqual(sum("prepare" in action for action in actions), 1)
            self.assertEqual(sum("builddeps" in action for action in actions), 1)
            self.assertEqual(sum("build" in action for action in actions), 3)
            self.assertEqual(sum("regression" in action for action in actions), 3)
            self.assertEqual(sum("rootpkg" in action for action in actions), 2)
            self.assertEqual(len(list((work / "compilations").iterdir())), 1)
            self.assertEqual(len(list((work / "packages").iterdir())), 2)
            provenance = json.loads(
                (output / "share/sources/plasma-workspace-6.6.6/provenance.json").read_text()
            )
            self.assertEqual(provenance["regression"]["user_uid"], 1000)
            self.assertEqual(provenance["regression"]["startuprestore"], "passed")
            self.assertFalse(provenance["qualified"])
            self.assertTrue(
                (output / "share/sources/plasma-workspace-6.6.6/regression.log").is_file()
            )


if __name__ == "__main__":
    unittest.main()
