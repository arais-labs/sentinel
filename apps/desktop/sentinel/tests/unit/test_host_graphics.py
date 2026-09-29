"""Host packages are self-contained and refuse undeclared native dependencies."""

import json
import runpy
import shutil
import sys
import tarfile
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from host import (
    bundle_libraries,
    build_identity,
    bundle_identity,
    apple_environment_identity,
    LIBRARIES,
)
from toolchain import build_environment
from artifacts import publish_directory


class HostGraphicsTests(unittest.TestCase):
    def host_fixture(self, root):
        source = root / "scripts/packaging/graphics"
        native = root / "native/graphics"
        source.mkdir(parents=True)
        native.mkdir(parents=True)
        for name in (
            "host.py",
            "build-desktop.py",
            "upstream.py",
            "toolchain.py",
            "requirements.txt",
        ):
            (source / name).write_text(name)
        for name in ("meson.build", "meson.options", "toolchain.lock.json"):
            (native / name).write_text(name)
        for name in ("renderer", "video", "transport", "patches/host"):
            folder = native / name
            folder.mkdir(parents=True)
            (folder / "fixture.c").write_text(name)
        (native / "sources.lock.json").write_text(
            json.dumps(
                {name: {"revision": name} for name in ("mesa", "epoxy", "virgl", "egl", "klipper")}
            )
        )
        return source, native

    def test_host_bundle_tracks_host_inputs_not_guest_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, native = self.host_fixture(root)
            first = bundle_identity(root, {})
            for relative in (
                "runtime.lock.json",
                "native/graphics/guest/session.py",
                "native/graphics/patches/klipper/fix.patch",
                "native/graphics/packaging/klipper/build.py",
                "scripts/packaging/graphics/guest_assets.py",
                "scripts/packaging/graphics/build-guest.py",
            ):
                file = root / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("guest-only edit")
                self.assertEqual(first, bundle_identity(root, {}), relative)
            pins = native / "sources.lock.json"
            data = json.loads(pins.read_text())
            data["klipper"]["revision"] = "new"
            pins.write_text(json.dumps(data))
            self.assertEqual(first, bundle_identity(root, {}))
            for file in (
                source / "host.py",
                source / "build-desktop.py",
                native / "renderer/fixture.c",
                native / "video/fixture.c",
                native / "transport/fixture.c",
                native / "patches/host/fixture.c",
                native / "meson.build",
                native / "toolchain.lock.json",
            ):
                original = file.read_bytes()
                file.write_bytes(original + b"changed")
                self.assertNotEqual(first, bundle_identity(root, {}), str(file))
                file.write_bytes(original)
            data["mesa"]["revision"] = "new"
            pins.write_text(json.dumps(data))
            self.assertNotEqual(first, bundle_identity(root, {}))

    def test_host_bundle_invalidates_for_selected_sdk_and_compilers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.host_fixture(root)
            with patch(
                "host.subprocess.check_output",
                side_effect=[
                    "/Xcode/SDKs/MacOSX.sdk\n",
                    "26.0\n",
                    "Apple clang 17\n",
                    "Apple clang++ 17\n",
                ],
            ) as query:
                environment = apple_environment_identity()
            self.assertEqual(
                [call.args[0] for call in query.call_args_list],
                [
                    ["xcrun", "--show-sdk-path"],
                    ["xcrun", "--show-sdk-version"],
                    ["/usr/bin/clang", "--version"],
                    ["/usr/bin/clang++", "--version"],
                ],
            )
            first = bundle_identity(root, environment)
            self.assertEqual(
                first, bundle_identity(root, dict(reversed(list(environment.items()))))
            )
            for name in environment:
                changed = {**environment, name: environment[name] + "-updated"}
                self.assertNotEqual(first, bundle_identity(root, changed), name)
                tree = root / "native/graphics"
                (tree / ".sentinel-upstream").write_text("source")
                tools = tree / "toolchain.lock.json"
                requirements = root / "scripts/packaging/graphics/requirements.txt"
                self.assertNotEqual(
                    build_identity(tree, [], tools, requirements, environment),
                    build_identity(tree, [], tools, requirements, changed),
                    name,
                )

    def test_renderer_reconfigures_unchanged_environment_and_wipes_changed_environment(self):
        script = Path(__file__).resolve().parents[2] / "scripts/packaging/graphics/build-desktop.py"
        build = runpy.run_path(str(script))["build"]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            objects = work / "desktop-renderer"
            (objects / "meson-private").mkdir(parents=True)
            (objects / "sentinel-desktop-renderer").write_text("executable")
            (work / "LICENSE.md").write_text("license")
            environment = {"sdkPath": "/sdk", "sdkVersion": "26", "clang": "Apple clang 17"}
            stamp = objects / ".sentinel-environment"
            for state, expected in (
                ("fresh", None),
                ("missing", "--wipe"),
                ("same", "--reconfigure"),
                ("changed", "--wipe"),
            ):
                with self.subTest(state=state):
                    if state != "fresh":
                        (objects / "meson-private/coredata.dat").touch()
                    if state == "missing":
                        stamp.unlink()
                    elif state == "changed":
                        stamp.write_text(
                            json.dumps({**environment, "sdkVersion": "25"}, sort_keys=True)
                        )
                    run = Mock()
                    with (
                        patch.dict(
                            build.__globals__,
                            {
                                "prepare_source": Mock(return_value=work),
                                "apple_environment_identity": Mock(return_value=environment),
                                "run": run,
                            },
                        ),
                        patch("subprocess.check_output", return_value="binary:\n"),
                    ):
                        build(work, work / "libraries", work / "output/renderer")
                    arguments = run.call_args_list[0].args
                    self.assertEqual("--wipe" in arguments, expected == "--wipe")
                    self.assertEqual("--reconfigure" in arguments, expected == "--reconfigure")
                    self.assertEqual(json.loads(stamp.read_text()), environment)

    def test_warm_host_bundle_publishes_fresh_guest_session_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, native = self.host_fixture(root)
            script = source / "build.py"
            shutil.copy2(
                Path(__file__).resolve().parents[2] / "scripts/packaging/graphics/build.py", script
            )
            guest = native / "guest"
            guest.mkdir()
            for name in (
                "install-guest.py",
                "install-browser-graphics.py",
                "gpu-start.sh",
                "session.py",
            ):
                (guest / name).write_text("fresh " + name)
            destination = root / "bundle/graphics"
            destination.mkdir(parents=True)
            for name in ("sentinel-desktop-renderer", *LIBRARIES, "icd.json", "Mesa-LICENSE"):
                (destination / name).write_text("cached host")
            environment = {"sdkPath": "/test-sdk", "clang": "Apple clang fixture"}
            (destination / "stamp").write_text(bundle_identity(root, environment))
            for distribution in ("alpine", "debian", "ubuntu"):
                package = (
                    root
                    / "build/graphics-sources/native-package-assets"
                    / ("klipper-" + distribution)
                    / "package"
                )
                package.parent.mkdir(parents=True)
                package.write_text("verified " + distribution)
            target = Mock()
            target.verify.side_effect = lambda path: (path / "package",)
            with (
                patch.object(sys, "argv", [str(script), str(destination)]),
                patch(
                    "host.apple_environment_identity", return_value=environment
                ) as environment_query,
                patch("host.build_host") as compile_host,
                patch("subprocess.run") as build_guest,
                patch("guest_assets.publish_kernel") as kernel,
                patch("klipper.KlipperTarget", return_value=target),
                patch("klipper.AlpineKlipperTarget", return_value=target),
            ):
                with self.assertRaises(SystemExit) as exit_status:
                    runpy.run_path(str(script), run_name="__main__")
                self.assertEqual(exit_status.exception.code, 0)
            compile_host.assert_not_called()
            environment_query.assert_called_once_with()
            self.assertEqual(
                [call.args[0][3] for call in build_guest.call_args_list],
                [
                    "musl",
                    "glibc",
                    "gpu-2404",
                    "kernel",
                    "klipper-alpine",
                    "klipper-debian",
                    "klipper-ubuntu",
                ],
            )
            kernel.assert_called_once_with(destination.resolve())
            self.assertEqual(
                (destination / "install-guest.py").read_text(), "fresh install-guest.py"
            )
            self.assertEqual((destination / "gpu-start.sh").read_text(), "fresh gpu-start.sh")
            with tarfile.open(destination / "desktop-runtime.tar.xz") as archive:
                self.assertEqual(archive.extractfile("./session.py").read(), b"fresh session.py")
                for distribution in ("alpine", "debian", "ubuntu"):
                    self.assertEqual(
                        archive.extractfile(f"./native-packages/{distribution}/package").read(),
                        ("verified " + distribution).encode(),
                    )
            self.assertEqual((destination / "sentinel-desktop-renderer").read_text(), "cached host")

    def test_source_patches_reuse_objects_but_configuration_changes_invalidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".sentinel-source").write_text("patched")
            (root / ".sentinel-upstream").write_text("revision")
            tools, requirements = root / "tools", root / "requirements"
            tools.write_text("toolchain")
            requirements.write_text("meson")
            first = build_identity(root, ["option"], tools, requirements, "sdk")
            self.assertEqual(first, build_identity(root, ["option"], tools, requirements, "sdk"))
            (root / ".sentinel-source").write_text("pristine")
            self.assertEqual(first, build_identity(root, ["option"], tools, requirements, "sdk"))
            self.assertNotEqual(
                build_identity(root, [], tools, requirements, "sdk"),
                build_identity(root, ["option"], tools, requirements, "sdk"),
            )
            for file in (root / ".sentinel-upstream", tools, requirements, root / "meson.build"):
                original = file.read_bytes() if file.exists() else None
                file.write_text("changed")
                self.assertNotEqual(
                    first, build_identity(root, ["option"], tools, requirements, "sdk")
                )
                if original is None:
                    file.unlink()
                else:
                    file.write_bytes(original)
            link = root / "include-link"
            link.symlink_to("first")
            linked = build_identity(root, ["option"], tools, requirements, "sdk")
            self.assertNotEqual(first, linked)
            link.unlink()
            link.symlink_to("second")
            self.assertNotEqual(
                linked, build_identity(root, ["option"], tools, requirements, "sdk")
            )

    def test_complete_bundle_replaces_old_files_without_mutating_open_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new = root / "published", root / "staged"
            old.mkdir()
            new.mkdir()
            (old / "driver").write_bytes(b"old")
            (old / "retired").touch()
            (new / "driver").write_bytes(b"new")
            with (old / "driver").open("rb") as running:
                publish_directory(new, old)
                self.assertEqual(running.read(), b"old")
            self.assertEqual((old / "driver").read_bytes(), b"new")
            self.assertFalse((old / "retired").exists())

    def test_failed_publication_preserves_existing_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "published"
            old.mkdir()
            (old / "driver").write_bytes(b"old")
            with self.assertRaises(FileNotFoundError):
                publish_directory(root / "missing", old)
            self.assertEqual((old / "driver").read_bytes(), b"old")

    def test_no_legacy_backend_in_bundle(self):
        self.assertNotIn("libGLESv2.dylib", LIBRARIES)
        self.assertIn("libvulkan_kosmickrisp.dylib", LIBRARIES)
        self.assertIn("libgallium-26.3.0-devel.dylib", LIBRARIES)

    def test_dependency_closure_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "libtest.dylib"
            source.write_bytes(b"fixture")
            with patch(
                "host.subprocess.check_output",
                return_value="file:\n\t/opt/homebrew/lib/libmissing.dylib (x)\n",
            ):
                with self.assertRaisesRegex(RuntimeError, "unbundled dependency"):
                    bundle_libraries({source.name: source}, root / "bundle")

    def test_relocation_removes_build_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "libtest.dylib"
            source.write_bytes(b"fixture")
            with (
                patch(
                    "host.subprocess.check_output",
                    side_effect=[
                        "file:\n\t/build/libtest.dylib (x)\n\t/usr/lib/libSystem.B.dylib (x)\n",
                        "cmd LC_RPATH\ncmdsize 48\npath /build/private/lib (offset 12)\n",
                    ],
                ),
                patch("host.run") as run,
            ):
                bundle_libraries({source.name: source}, root / "bundle")
                arguments = run.call_args_list[0].args
                self.assertIn("@loader_path/libtest.dylib", arguments)
                self.assertIn("-delete_rpath", arguments)
                self.assertEqual(
                    run.call_args_list[-1].args[:4], ("codesign", "--force", "--sign", "-")
                )

    def test_build_environment_does_not_select_host_homebrew(self):
        with patch("toolchain.subprocess.check_output", return_value="/sdk\n"):
            env = build_environment(Path("/build"), Path("/build/tools"), Path("/build/venv"))
        self.assertNotIn("/opt/homebrew", env["PATH"])
        self.assertTrue(env["PKG_CONFIG"].startswith("/build/tools/"))

    def test_pinned_sources_and_tools(self):
        root = Path(__file__).resolve().parents[2] / "native/graphics"
        for name, entry in json.loads((root / "sources.lock.json").read_text()).items():
            if "repository" in entry:
                self.assertEqual(set(entry), {"repository", "revision"})
                self.assertRegex(entry["revision"], r"^[a-f0-9]{40}$")
            else:
                fields = {"url", "sha256"} if name == "mesa-guest" else {"url", "sha256", "version"}
                if name == "labwc":
                    fields.add("wlrootsAbi")
                    self.assertRegex(entry["wlrootsAbi"], r"^\d+\.\d+$")
                if name == "localsearch":
                    fields.update(
                        {"packageRevision", "sourceDateEpoch", "alpineRecipe", "alpinePatch"}
                    )
                    self.assertGreater(entry["packageRevision"], 0)
                    self.assertGreater(entry["sourceDateEpoch"], 0)
                    for component in ("alpineRecipe", "alpinePatch"):
                        self.assertEqual(set(entry[component]), {"url", "sha256"})
                        self.assertRegex(
                            entry[component]["url"],
                            r"^https://raw\.githubusercontent\.com/alpinelinux/aports/[a-f0-9]{40}/",
                        )
                        self.assertRegex(entry[component]["sha256"], r"^[a-f0-9]{64}$")
                self.assertEqual(set(entry), fields)
                self.assertTrue(entry["url"].startswith("https://"))
                self.assertRegex(entry["sha256"], r"^[a-f0-9]{64}$")
                if name != "mesa-guest":
                    self.assertTrue(entry["version"])
        entries = json.loads((root / "toolchain.lock.json").read_text())
        self.assertEqual(len(entries), len({entry["name"] for entry in entries}))
        for entry in entries:
            self.assertRegex(entry["sha256"], r"^[a-f0-9]{64}$")
            self.assertTrue(entry["url"].endswith(entry["sha256"]))
