"""Exercise the Linux artifact handoff without Docker, a VM, or network access."""

import ast
import hashlib
import io
import json
import os
import queue
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch


class GuestGraphicsTests(unittest.TestCase):
    def test_ci_produces_every_graphics_target_consumed_by_packaging(self):
        desktop = Path(__file__).resolve().parents[2]
        tree = ast.parse((desktop / "scripts/packaging/graphics/build.py").read_text())
        prepare = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "prepare_guest_assets"
        )
        targets = ast.literal_eval(
            next(node.iter for node in prepare.body if isinstance(node, ast.For))
        )
        workflow = (desktop.parents[2] / ".github/workflows/ci.yml").read_text()
        guest_job = workflow.split("  guest-graphics:\n", 1)[1].split("\n  desktop:", 1)[0]
        built = re.findall(r"build-guest\.py build/guest-graphics ([\w-]+)", guest_job)
        self.assertEqual(set(built), set(targets))
        self.assertLess(built.index("glibc"), built.index("gpu-2404"))
        self.assertIn("build/graphics-sources/guest/*.snap", guest_job)

    def test_gpu_start_checks_mapped_memory_without_userfaultfd_access(self):
        source = Path(__file__).resolve().parents[2] / "native/graphics/guest/gpu-start.sh"
        commands = source.read_text()
        self.assertIn('"$prefix/bin/sentinel-mapped-memory-check"', commands)
        self.assertNotIn("userfaultfd", commands)
        self.assertLess(
            commands.index("sentinel-mapped-memory-check"), commands.index("udevadm settle")
        )
        self.assertLess(commands.index("udevadm settle"), commands.index("rvgpu-proxy"))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.desktop = Path(self.temp.name)
        source = Path(__file__).resolve().parents[2] / "scripts/packaging/graphics"
        self.script = self.desktop / "scripts/packaging/graphics/build-guest.py"
        self.script.parent.mkdir(parents=True)
        shutil.copy2(source / "build-guest.py", self.script)
        shutil.copy2(source / "build-component.sh", self.script.parent / "build-component.sh")
        for name in ("gpu_provider.py", "package-gpu-provider.py", "generate-display-modes.py"):
            shutil.copy2(source / name, self.script.parent / name)
        shutil.copytree(
            source.parents[2] / "native/graphics/display",
            self.desktop / "native/graphics/display",
        )
        shutil.copytree(
            source.parents[2] / "native/graphics/packaging/gpu-2404",
            self.desktop / "native/graphics/packaging/gpu-2404",
        )
        (self.script.parent / "build-guest.sh").write_text("set -eu\necho build\n")
        (self.script.parent / "build-guest-packages.sh").write_text("set -eu\necho packages\n")
        (self.script.parent / "build-kernel.sh").write_text("set -eu\necho kernel\n")
        (self.script.parent / "build-labwc.sh").write_text("set -eu\necho compositor\n")
        (self.script.parent / "kernel.config").write_text("CONFIG_VIRTIO=y\n")
        self.patches = self.desktop / "native/graphics/patches"
        self.patches.mkdir(parents=True)
        mesa_source = b"fixture Mesa archive"
        mesa_hash = hashlib.sha256(mesa_source).hexdigest()
        (self.patches.parent / "sources.lock.json").write_text(
            json.dumps(
                {"fixture": "pinned", "mesa-guest": {"url": "fixture://mesa", "sha256": mesa_hash}}
            )
            + "\n"
        )
        source_cache = self.desktop / "build/graphics-sources/guest/sources"
        source_cache.mkdir(parents=True)
        (source_cache / mesa_hash).write_bytes(mesa_source)
        (self.patches / "remote-proxy-fences.patch").write_text("fixture patch\n")
        (self.patches / "kernel-virtio-vblank.patch").write_text("fixture kernel patch\n")
        (self.patches / "kernel-namespace-order.patch").write_text("fixture namespace patch\n")
        (self.patches / "guest").mkdir()
        (self.patches / "labwc").mkdir()
        (self.patches / "labwc/0001-refresh-map-pointer-focus.patch").write_text(
            "labwc focus patch\n"
        )
        (self.patches / "guest/0001-mapped-buffer-storage.patch").write_text("Mesa storage patch\n")
        self.driver = self.desktop / "native/graphics/guest/driver"
        self.driver.mkdir(parents=True)
        (self.driver / "virgl_mapped_buffer.c").write_text("fixture driver\n")
        self.bridge = self.driver.parent / "gpu-bridge.c"
        self.bridge.write_text("fixture guest bridge\n")
        (self.driver.parent / "install-native-mesa.py").write_text("# native package installer\n")
        self.transport = self.desktop / "native/graphics/transport"
        self.transport.mkdir()
        (self.transport / "gpu_transport.c").write_text("fixture transport\n")
        (self.transport / "gpu_transport.h").write_text("fixture transport API\n")
        (self.desktop / "runtime.lock.json").write_text(
            json.dumps(
                {
                    "platforms": {
                        "macos-arm64": {
                            "workspaceRuntime": {
                                "buildImage": "example/alpine@sha256:abc",
                                "glibcBuildImage": "example/ubuntu-compiler@sha256:def",
                            }
                        }
                    }
                }
            )
        )
        distribution = (
            self.desktop / "native/macos/Sources/WorkspaceRuntime/WorkspaceDistribution.swift"
        )
        distribution.parent.mkdir(parents=True)
        distribution.write_text('case .ubuntu: return "example/ubuntu@sha256:def"')
        self.artifacts = self.desktop / "artifacts"
        self.calls = []

    def test_native_package_prebuilt_handoff_never_starts_builder(self):
        for distribution in ("debian", "ubuntu", "alpine"):
            name = "klipper-" + distribution
            supplied = self.desktop / "native-prebuilt"
            root = supplied / name
            root.mkdir(parents=True)
            package = root / "package.deb"
            package.write_bytes(b"verified by target fixture")
            target = SimpleNamespace(
                image="example/" + distribution + "@sha256:pinned",
                key="native-key",
                distribution=distribution,
                verify=Mock(return_value=(package,)),
            )
            module = SimpleNamespace(
                KlipperTarget=Mock(return_value=target),
                AlpineKlipperTarget=Mock(return_value=target),
            )
            with (
                self.subTest(distribution=distribution),
                patch.dict(sys.modules, {"klipper": module}),
                patch.dict(os.environ, {"SENTINEL_GUEST_GRAPHICS_DIR": str(supplied)}),
                patch.object(sys, "argv", [str(self.script), str(self.artifacts), name]),
                patch("subprocess.run", side_effect=AssertionError("Unexpected build")),
                patch("subprocess.Popen", side_effect=AssertionError("Unexpected VM")),
                self.assertRaises(SystemExit) as exited,
            ):
                runpy.run_path(str(self.script))
            self.assertEqual(exited.exception.code, 0)
            self.assertEqual(
                (self.artifacts / name / package.name).read_bytes(), package.read_bytes()
            )
            target.verify.assert_called_with(root)

    def test_native_package_bad_prebuilt_does_not_fall_back_to_build(self):
        target = SimpleNamespace(
            image="example/debian@sha256:pinned",
            key="native-key",
            verify=Mock(side_effect=ValueError("bad native provenance")),
        )
        with (
            patch.dict(
                sys.modules,
                {
                    "klipper": SimpleNamespace(
                        KlipperTarget=lambda *_: target, AlpineKlipperTarget=lambda *_: target
                    )
                },
            ),
            patch.dict(os.environ, {"SENTINEL_GUEST_GRAPHICS_DIR": str(self.desktop / "prebuilt")}),
            patch.object(sys, "argv", [str(self.script), str(self.artifacts), "klipper-debian"]),
            patch("subprocess.Popen", side_effect=AssertionError("Unexpected VM")),
            self.assertRaisesRegex(ValueError, "bad native provenance"),
        ):
            runpy.run_path(str(self.script))

    def test_native_package_linux_build_uses_matching_image_and_retained_cache(self):
        for distribution in ("debian", "ubuntu", "alpine"):
            name = "klipper-" + distribution
            root = self.desktop / (name + "-cache")
            package = root / "package.deb"

            def verify(path):
                if not package.exists():
                    raise FileNotFoundError(package)
                self.assertEqual(path, root)
                return (package,)

            def record(output, publish):
                root.mkdir(parents=True)
                package.write_bytes(b"native recipe output")

            target = SimpleNamespace(
                image="example/" + distribution + "@sha256:pinned",
                key=name,
                distribution=distribution,
                destination=root,
                verify=verify,
                stage=Mock(),
                commands=Mock(return_value="native-recipe-command\n"),
                record=record,
            )
            with (
                self.subTest(distribution=distribution),
                patch.dict(
                    sys.modules,
                    {
                        "klipper": SimpleNamespace(
                            KlipperTarget=lambda *_: target, AlpineKlipperTarget=lambda *_: target
                        )
                    },
                ),
                patch.dict(os.environ, {}, clear=True),
                patch("sys.platform", "linux"),
                patch("platform.machine", return_value="aarch64"),
                patch.object(sys, "argv", [str(self.script), str(self.artifacts), name]),
                patch("subprocess.run") as run,
                self.assertRaises(SystemExit) as exited,
            ):
                runpy.run_path(str(self.script))
            self.assertEqual(exited.exception.code, 0)
            command = run.call_args.args[0]
            self.assertIn(target.image, command)
            self.assertTrue(
                any(
                    "linux-builders/" in value and value.endswith(":/var/cache/sentinel-build")
                    for value in command
                )
            )
            script = run.call_args.kwargs["input"].decode()
            self.assertIn("native-recipe-command", script)
            self.assertNotIn("graphics.tar.xz", script)
            self.assertEqual(
                (self.artifacts / name / package.name).read_bytes(), package.read_bytes()
            )

    def run_native_macos(self, target):
        lock = self.desktop / "runtime.lock.json"
        config = json.loads(lock.read_text())
        kernel = b"verified build kernel"
        digest = hashlib.sha256(kernel).hexdigest()
        config["platforms"]["macos-arm64"]["workspaceRuntime"].update(
            kernelFileSha256=digest, initImage="example/init@sha256:abc"
        )
        lock.write_text(json.dumps(config))
        cache = self.desktop / "build/graphics-sources/guest"
        (cache / digest).write_bytes(kernel)
        responses = queue.Queue()
        responses.put(json.dumps({"event": "ready"}) + "\n")
        requests = []

        def write(line):
            request = json.loads(line)
            requests.append(request)
            responses.put(json.dumps({"id": request["id"], "exitCode": 0}) + "\n")

        helper = SimpleNamespace(
            stdin=SimpleNamespace(
                write=write, flush=lambda: None, close=lambda: responses.put(None)
            ),
            stdout=iter(responses.get, None),
            poll=lambda: None,
            wait=lambda timeout: 0,
        )
        module = SimpleNamespace(
            KlipperTarget=Mock(return_value=target),
            AlpineKlipperTarget=Mock(return_value=target),
        )
        with (
            patch.dict(sys.modules, {"klipper": module}),
            patch.dict(os.environ, {}, clear=True),
            patch(
                "sys.argv",
                [str(self.script), str(self.artifacts), "klipper-" + target.distribution],
            ),
            patch("sys.platform", "darwin"),
            patch("os.cpu_count", return_value=4),
            patch("subprocess.Popen", return_value=helper),
            patch("subprocess.check_output", return_value="19327352832"),
            patch("subprocess.run", side_effect=AssertionError("no external commands")),
            self.assertRaises(SystemExit) as exited,
        ):
            runpy.run_path(str(self.script), run_name="__main__")
        self.assertEqual(exited.exception.code, 0)
        self.assertEqual(requests[-2]["arguments"], ["sync"])
        self.assertEqual(requests[-1]["action"], "stop")
        return requests

    def test_native_package_macos_build_retains_distro_builder_across_source_edits(self):
        workspaces = set()
        for distribution in ("debian", "ubuntu", "alpine"):
            starts = []
            for revision in ("source-v1", "source-v2"):
                with self.subTest(distribution=distribution, revision=revision):
                    root = self.desktop / "native-cache" / distribution / revision
                    package = root / ("package.apk" if distribution == "alpine" else "package.deb")

                    def record(output, publish):
                        built = output / package.name
                        built.write_bytes(b"native recipe output")
                        publish(built, package)

                    target = SimpleNamespace(
                        image="example/" + distribution + "@sha256:pinned",
                        key=revision,
                        distribution=distribution,
                        destination=root,
                        stage=Mock(),
                        commands=Mock(return_value="native-recipe-command\n"),
                        record=Mock(side_effect=record),
                        verify=Mock(side_effect=[FileNotFoundError(package), (package,)]),
                    )
                    requests = self.run_native_macos(target)
                    start = requests[0]
                    starts.append(start)
                    output = Path(start["project"])
                    self.assertEqual(start["action"], "start")
                    self.assertEqual(start["image_reference"], target.image)
                    self.assertEqual(start["distribution"], distribution)
                    self.assertEqual(
                        output,
                        self.desktop.resolve()
                        / "build/graphics-sources/guest"
                        / "builders"
                        / start["workspace"]
                        / revision,
                    )
                    target.stage.assert_called_once_with(output)
                    target.commands.assert_called_once_with(
                        output, test_user="sentinel-build", jobs=4
                    )
                    target.record.assert_called_once()
                    self.assertEqual(target.record.call_args.args[0], output)
                    self.assertEqual(
                        [call.args for call in target.verify.call_args_list], [(root,), (root,)]
                    )
                    commands = requests[1]["arguments"][-1]
                    self.assertIn("native-recipe-command", commands)
                    self.assertNotIn("graphics.tar.xz", commands)
                    self.assertNotIn("echo build", commands)
                    self.assertEqual("apt-get" in commands, distribution != "alpine")
                    published = self.artifacts / ("klipper-" + distribution) / package.name
                    self.assertEqual(published.read_bytes(), b"native recipe output")
            self.assertEqual(starts[0]["workspace"], starts[1]["workspace"])
            workspaces.add(starts[0]["workspace"])
        self.assertEqual(len(workspaces), 3)

    def test_native_package_macos_rejects_invalid_recorded_artifact(self):
        target = SimpleNamespace(
            image="example/debian@sha256:pinned",
            key="invalid",
            distribution="debian",
            destination=self.desktop / "invalid-native-cache",
            stage=Mock(),
            commands=Mock(return_value="native-recipe-command\n"),
            record=Mock(),
            verify=Mock(side_effect=[FileNotFoundError(), ValueError("bad native provenance")]),
        )
        with self.assertRaisesRegex(ValueError, "bad native provenance"):
            self.run_native_macos(target)
        target.record.assert_called_once()
        self.assertEqual(target.verify.call_count, 2)
        self.assertFalse((self.artifacts / "klipper-debian").exists())

    def test_clipboard_feature_gate_accepts_meson_boolean_and_rejects_missing_feature(self):
        source = Path(__file__).resolve().parents[2] / "scripts/packaging/graphics/build-guest.sh"
        pattern = re.search(r"grep -Eq '([^']+)' clipboard-build/src/config.h", source.read_text())[
            1
        ]
        for header, expected in (
            ("#define HAVE_EXT_DATA_CONTROL\n", 0),
            ("#define HAVE_EXT_DATA_CONTROL 1\n", 0),
            ("#define HAVE_EXT_DATA_CONTROL 0\n", 1),
            ("#undef HAVE_EXT_DATA_CONTROL\n", 1),
            ("/* #undef HAVE_EXT_DATA_CONTROL */\n", 1),
            ("", 1),
        ):
            with self.subTest(header=header):
                result = subprocess.run(["grep", "-Eq", pattern], input=header, text=True)
                self.assertEqual(result.returncode, expected)

    def test_glvnd_artifact_contains_only_vendors_not_public_dispatch(self):
        source = Path(__file__).resolve().parents[2] / "scripts/packaging/graphics/build-guest.sh"
        script = source.read_text()
        self.assertIn("libglvnd-dev", script)
        self.assertIn("glvnd=enabled", script)
        self.assertIn("glvnd=disabled", script)
        self.assertIn('-Dglvnd="$glvnd"', script)
        check = re.search(
            r'python3 - "\$prefix" "\$glvnd" "\$work/mesa-install/usr" <<\'PY\'\n(.*?)\nPY',
            script,
            re.S,
        )[1]
        prefix = self.desktop / "vendor-test"
        library = prefix / "lib"
        library.mkdir(parents=True)
        for name in ("libEGL_mesa.so.0", "libGLX_mesa.so.0"):
            (library / name).touch()
        vendor = prefix / "share/glvnd/egl_vendor.d/50_mesa.json"
        vendor.parent.mkdir(parents=True)
        vendor.write_text(json.dumps({"ICD": {"library_path": "libEGL_mesa.so.0"}}))
        with patch("sys.argv", ["check", str(prefix), "enabled"]):
            exec(compile(check, str(source), "exec"), {})
            for name in ("libEGL.so.1", "libGL.so.1", "libOpenGL.so.0", "libGLdispatch.so.0"):
                with self.subTest(public=name):
                    (library / name).touch()
                    with self.assertRaisesRegex(AssertionError, "public GLVND dispatch"):
                        exec(compile(check, str(source), "exec"), {})
                    (library / name).unlink()
            (library / "libGLX_mesa.so.0").unlink()
            with self.assertRaisesRegex(AssertionError, "Missing GLVND vendor"):
                exec(compile(check, str(source), "exec"), {})

        native = self.desktop / "native-mesa/usr"
        (native / "lib").mkdir(parents=True)
        with patch("sys.argv", ["check", str(prefix), "disabled", str(native)]):
            with self.assertRaisesRegex(AssertionError, "Missing Alpine Mesa ABI"):
                exec(compile(check, str(source), "exec"), {})
            for name in ("libEGL.so.1", "libGL.so.1", "libGLESv1_CM.so.1", "libGLESv2.so.2"):
                (native / "lib" / name).touch()
            exec(compile(check, str(source), "exec"), {})

    def docker(self, args, **kwargs):
        self.assertEqual(args[:3], ["docker", "run", "--rm"])
        self.assertEqual(args[args.index("--platform") + 1], "linux/arm64")
        output = Path(args[args.index("--volume") + 1].removesuffix(":/output"))
        if b"echo kernel" in kwargs["input"]:
            self.assertEqual(
                (output / "kernel-virtio-vblank.patch").read_bytes(),
                (self.patches / "kernel-virtio-vblank.patch").read_bytes(),
            )
        else:
            if b"SENTINEL_GRAPHICS_LIBC=glibc" not in kwargs["input"]:
                self.assertEqual(
                    (output / "build-labwc.sh").read_bytes(),
                    (self.script.parent / "build-labwc.sh").read_bytes(),
                )
                self.assertEqual(
                    (output / "patches/labwc/0001-refresh-map-pointer-focus.patch").read_bytes(),
                    (self.patches / "labwc/0001-refresh-map-pointer-focus.patch").read_bytes(),
                )
            else:
                self.assertFalse((output / "build-labwc.sh").exists())
                self.assertFalse((output / "patches/labwc").exists())
            self.assertEqual((output / "guest/gpu-bridge.c").read_bytes(), self.bridge.read_bytes())
            for name in ("gpu_transport.c", "gpu_transport.h"):
                self.assertEqual(
                    (output / "transport" / name).read_bytes(),
                    (self.transport / name).read_bytes(),
                )
            self.assertEqual(
                (output / "guest/driver/virgl_mapped_buffer.c").read_bytes(),
                (self.driver / "virgl_mapped_buffer.c").read_bytes(),
            )
            self.assertEqual(
                (output / "patches/guest/0001-mapped-buffer-storage.patch").read_bytes(),
                (self.patches / "guest/0001-mapped-buffer-storage.patch").read_bytes(),
            )
        with tarfile.open(output / "graphics.tar.xz", "w:xz") as archive:
            data = b"fixture-graphics"
            info = tarfile.TarInfo("./version")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        if b"echo packages" in kwargs["input"] and b"echo build" in kwargs["input"]:
            shutil.copy2(output / "graphics.tar.xz", output / "core.tar.xz")
        self.calls.append((args, kwargs["input"]))

    def execute(self, libc="musl", *, prebuilt=None, system="linux", runner=None):
        env = {"CI": "true"}
        if prebuilt is not None:
            env["SENTINEL_GUEST_GRAPHICS_DIR"] = str(prebuilt)
        destination = self.artifacts if prebuilt is None else self.desktop / "packaged"
        with (
            patch.object(sys, "path", [str(self.script.parent), *sys.path]),
            patch.dict(os.environ, env, clear=True),
            patch("sys.argv", [str(self.script), str(destination), libc]),
            patch("sys.platform", system),
            patch("platform.machine", return_value="aarch64"),
            patch("subprocess.run", side_effect=runner or self.docker),
        ):
            try:
                self.namespace = runpy.run_path(str(self.script), run_name="__main__")
            except SystemExit as error:
                self.assertEqual(error.code, 0)
        return destination

    def test_provider_uses_glibc_builder_and_independent_verified_cache(self):
        self.execute("glibc")
        mesa_key = self.namespace["key"]
        mesa_core = self.namespace["core_key"]
        builder = self.namespace["builder_id"]

        def package(args, **kwargs):
            mount = args[args.index("--volume") + 1]
            output = Path(mount.removesuffix(":/output"))
            self.assertNotIn(b"echo build", kwargs["input"])
            self.assertIn(b"package-gpu-provider.py", kwargs["input"])
            packager = runpy.run_path(str(output / "package-gpu-provider.py"))
            mesa_sha = packager["sha"](output / "mesa-linux-arm64-glibc.tar.xz")
            source_sha = packager["sha"](output / "mesa.tar.xz")
            identity = packager["provider_identity"](mesa_sha, source_sha, output / "gpu-2404")
            version = "1-" + identity[:16]
            archive = output / "provider-output" / f"sentinel-gpu-2404_{version}_arm64.snap"
            archive.parent.mkdir()
            archive.write_bytes(b"fixture snap")
            archive.with_suffix(".json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "identity": identity,
                        "version": version,
                        "sha256": packager["sha"](archive),
                        "size": archive.stat().st_size,
                        "mesa_sha256": mesa_sha,
                        "mesa_source_sha256": source_sha,
                    }
                )
            )

        self.execute("gpu-2404", runner=package)
        self.assertEqual(self.namespace["builder_id"], builder)
        self.assertTrue((self.artifacts / "gpu-2404.snap").is_file())
        identity = self.namespace["key"]
        self.execute("gpu-2404", runner=AssertionError("cache hit must not start builder"))
        wrapper = self.desktop / "native/graphics/packaging/gpu-2404/bin/gpu-2404-provider-wrapper"
        wrapper.write_text(wrapper.read_text() + "\n# changed provider only\n")
        self.execute("glibc", runner=AssertionError("provider must not rebuild Mesa"))
        self.assertEqual(self.namespace["key"], mesa_key)
        self.assertEqual(self.namespace["core_key"], mesa_core)
        self.execute("gpu-2404", runner=package)
        self.assertNotEqual(self.namespace["key"], identity)
        # Handoff on macOS CI accepts only matching Mesa and provider identities.
        self.execute("glibc", prebuilt=self.artifacts, system="darwin")
        self.execute(
            "gpu-2404",
            prebuilt=self.artifacts,
            system="darwin",
            runner=AssertionError("prebuilt must not start VM"),
        )
        (self.artifacts / "gpu-2404.snap").write_bytes(b"corrupt")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            self.execute("gpu-2404", prebuilt=self.artifacts, system="darwin")

    def test_linux_failed_build_retains_intermediates_on_retry(self):
        retained = []

        def interrupted(args, **kwargs):
            mount = next(arg for arg in args if arg.endswith(":/var/cache/sentinel-build"))
            directory = Path(mount.removesuffix(":/var/cache/sentinel-build"))
            retained.append(directory)
            (directory / "partial-object").write_bytes(b"compiled object")
            raise subprocess.CalledProcessError(1, args)

        with self.assertRaises(subprocess.CalledProcessError):
            self.execute(runner=interrupted)
        self.execute()
        args, _ = self.calls[-1]
        self.assertIn(f"{retained[0]}:/var/cache/sentinel-build", args)
        self.assertEqual((retained[0] / "partial-object").read_bytes(), b"compiled object")

    def test_both_distributions_build_and_transfer_without_vm(self):
        for libc, image, suffix in [
            ("musl", "example/alpine@sha256:abc", ""),
            ("glibc", "example/ubuntu-compiler@sha256:def", "-glibc"),
        ]:
            with self.subTest(libc=libc):
                self.execute(libc)
                args, script = self.calls[-1]
                self.assertIn(image, args)
                persistent = (
                    self.namespace["cache"] / "linux-builders" / self.namespace["builder_id"]
                )
                self.assertTrue(persistent.is_dir())
                self.assertIn(f"{persistent}:/var/cache/sentinel-build", args)
                self.assertEqual(b"SENTINEL_GRAPHICS_LIBC=glibc" in script, libc == "glibc")
                output = self.execute(
                    libc,
                    prebuilt=self.artifacts,
                    system="darwin",
                    runner=AssertionError("must not launch a builder"),
                )
                name = f"mesa-linux-arm64{suffix}.tar.xz"
                self.assertEqual((output / name).read_bytes(), (self.artifacts / name).read_bytes())

    def test_corrupted_or_stale_artifact_fails_without_fallback(self):
        self.execute()
        archive = self.artifacts / "mesa-linux-arm64.tar.xz"
        original = archive.read_bytes()
        archive.write_bytes(b"damaged")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            self.execute(prebuilt=self.artifacts, system="darwin")
        archive.write_bytes(original)
        (self.script.parent / "build-guest.sh").write_text("changed build inputs")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            self.execute(prebuilt=self.artifacts, system="darwin")

    def test_workspace_release_does_not_change_compiler_image_or_artifact(self):
        self.execute("glibc")
        distribution = (
            self.desktop / "native/macos/Sources/WorkspaceRuntime/WorkspaceDistribution.swift"
        )
        distribution.write_text('case .ubuntu: return "example/new-workspace@sha256:changed"')
        self.execute("glibc", runner=AssertionError("workspace release must not rebuild graphics"))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.namespace["build_image"], "example/ubuntu-compiler@sha256:def")

    def test_compiler_image_change_invalidates_artifact(self):
        self.execute("glibc")
        lock = self.desktop / "runtime.lock.json"
        config = json.loads(lock.read_text())
        config["platforms"]["macos-arm64"]["workspaceRuntime"][
            "glibcBuildImage"
        ] = "example/new-compiler@sha256:123"
        lock.write_text(json.dumps(config))
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            self.execute("glibc", prebuilt=self.artifacts, system="darwin")

    def test_builder_and_graphics_survive_a_labwc_patch_change(self):
        self.execute()
        previous = self.namespace
        (self.patches / "labwc/0001-refresh-map-pointer-focus.patch").write_text("new cursor fix\n")
        self.execute()
        self.assertEqual(previous["builder_id"], self.namespace["builder_id"])
        self.assertEqual(previous["core_key"], self.namespace["core_key"])
        self.assertNotEqual(
            previous["package_keys"]["labwc"], self.namespace["package_keys"]["labwc"]
        )
        lock = self.desktop / "runtime.lock.json"
        config = json.loads(lock.read_text())
        config["platforms"]["macos-arm64"]["workspaceRuntime"]["buildImage"] = "new/base@sha256:def"
        lock.write_text(json.dumps(config))
        self.execute()
        self.assertNotEqual(previous["builder_id"], self.namespace["builder_id"])

    def test_component_retry_preserves_intermediates_and_success_skips_build(self):
        runner = self.script.parent / "build-component.sh"
        root = self.desktop / "component-cache"
        bundle = self.desktop / "assembled"
        env = {
            **os.environ,
            "SENTINEL_BUILD_CACHE": str(root),
            "SENTINEL_GRAPHICS_BUNDLE": str(bundle),
        }
        command = ["sh", str(runner), "package", "fixture-key"]
        first = 'touch "$SENTINEL_BUILD_WORK/object.o"\nexit 9\n'
        self.assertEqual(
            subprocess.run(
                command, input=first, text=True, env=env, capture_output=True
            ).returncode,
            9,
        )
        self.assertFalse((root / "package/fixture-key/complete").exists())
        retry = 'test -f "$SENTINEL_BUILD_WORK/object.o"\nprintf package > "$SENTINEL_GRAPHICS_PREFIX/fixture.apk"\n'
        subprocess.run(command, input=retry, text=True, env=env, capture_output=True, check=True)
        self.assertEqual((bundle / "fixture.apk").read_text(), "package")
        subprocess.run(
            command, input="exit 99\n", text=True, env=env, capture_output=True, check=True
        )

    def test_macos_ci_requires_supplied_artifacts(self):
        with self.assertRaisesRegex(RuntimeError, "requires SENTINEL_GUEST_GRAPHICS_DIR"):
            self.execute(system="darwin", runner=AssertionError("must not launch a VM"))

    def test_missing_supplied_artifact_fails(self):
        with self.assertRaises(FileNotFoundError):
            self.execute(prebuilt=self.desktop / "missing", system="darwin")

    def test_kernel_is_cached_independently_of_guest_userspace(self):
        self.execute("kernel")
        self.assertIn(b"echo kernel", self.calls[-1][1])
        (self.script.parent / "build-guest.sh").write_text("changed Mesa only")
        self.execute("kernel", runner=AssertionError("kernel must be cached"))
        output = self.execute("kernel", prebuilt=self.artifacts, system="darwin")
        self.assertTrue((output / "kernel-linux-arm64.tar.xz").is_file())
        (self.script.parent / "kernel.config").write_text("CONFIG_VIRTIO=n\n")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            self.execute("kernel", prebuilt=self.artifacts, system="darwin")

    def test_guest_patch_changes_invalidate_userspace_not_kernel(self):
        self.execute("musl")
        self.execute("kernel")
        (self.patches / "remote-proxy-fences.patch").write_text("changed patch\n")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            self.execute("musl", prebuilt=self.artifacts, system="darwin")
        self.execute("kernel", runner=AssertionError("kernel must remain cached"))

    def test_alpine_dependencies_invalidate_only_alpine_graphics(self):
        for file in (
            self.script.parent / "build-labwc.sh",
            self.patches / "labwc/0001-refresh-map-pointer-focus.patch",
        ):
            with self.subTest(input=file.name):
                for libc in ("musl", "glibc", "kernel"):
                    self.execute(libc)
                file.write_text(file.read_text() + "changed\n")
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    self.execute("musl", prebuilt=self.artifacts, system="darwin")
                for libc in ("glibc", "kernel"):
                    self.execute(
                        libc, runner=AssertionError("unaffected artifact must remain cached")
                    )

    def test_alpine_package_patch_reuses_compiled_graphics(self):
        self.execute("musl")
        self.assertIn(b"echo build", self.calls[-1][1])
        patch_file = self.patches / "labwc/0001-refresh-map-pointer-focus.patch"
        patch_file.write_text(patch_file.read_text() + "changed\n")
        self.execute("musl")
        self.assertIn(b"echo packages", self.calls[-1][1])
        self.assertNotIn(b"echo build", self.calls[-1][1])

    def test_missing_labwc_patch_fails_before_build(self):
        (self.patches / "labwc/0001-refresh-map-pointer-focus.patch").unlink()
        with self.assertRaisesRegex(RuntimeError, "Missing labwc build patches"):
            self.execute(runner=AssertionError("must not launch incomplete build"))

    def test_labwc_elf_validation_rejects_wrong_arch_libc_or_wlroots_abi(self):
        source = Path(__file__).resolve().parents[2] / "scripts/packaging/graphics/build-labwc.sh"
        script = source.read_text()
        self.assertIn("--wrap-mode=nofallback", script)
        self.assertIn("meson test -C build --print-errorlogs", script)
        check = re.search(r"<<'PY'\n(.*?)\nPY", script, re.S)[1]
        binary = self.desktop / "labwc"
        header = bytearray(20)
        header[:6] = b"\x7fELF\x02\x01"
        header[18:20] = (183).to_bytes(2, "little")
        binary.write_bytes(header)
        for interpreter, needed, error in (
            ("/lib/ld-musl-aarch64.so.1", "libwlroots-0.20.so", None),
            ("/lib/ld-linux-aarch64.so.1", "libwlroots-0.20.so", "musl ABI"),
            ("/lib/ld-musl-aarch64.so.1", "libwlroots-0.19.so", "wlroots ABI"),
            ("/lib/ld-musl-aarch64.so.1", "libc.musl-aarch64.so.1", "wlroots ABI"),
        ):
            with (
                self.subTest(interpreter=interpreter, needed=needed),
                patch("sys.argv", ["verify", str(binary), "0.20"]),
                patch(
                    "subprocess.check_output",
                    side_effect=[interpreter, f"(NEEDED) Shared library: [{needed}]"],
                ),
            ):
                if error:
                    with self.assertRaisesRegex(AssertionError, error):
                        exec(compile(check, str(source), "exec"), {})
                else:
                    exec(compile(check, str(source), "exec"), {})
        binary.write_bytes(b"not an ARM executable")
        with (
            patch("sys.argv", ["verify", str(binary), "0.20"]),
            self.assertRaisesRegex(AssertionError, "ELF64 AArch64"),
        ):
            exec(compile(check, str(source), "exec"), {})

    def test_kernel_patch_changes_invalidate_kernel_not_userspace(self):
        self.execute("musl")
        self.execute("kernel")
        (self.patches / "kernel-virtio-vblank.patch").write_text("changed kernel patch\n")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            self.execute("kernel", prebuilt=self.artifacts, system="darwin")
        self.execute("musl", runner=AssertionError("userspace must remain cached"))

    def test_display_contract_and_generator_invalidate_kernel_only(self):
        for file in (
            self.desktop / "native/graphics/display/modes.json",
            self.script.parent / "generate-display-modes.py",
        ):
            with self.subTest(input=file.name):
                self.execute("musl")
                self.execute("kernel")
                file.write_text(file.read_text() + "\n")
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    self.execute("kernel", prebuilt=self.artifacts, system="darwin")
                self.execute("musl", runner=AssertionError("mode changes must not rebuild Mesa"))

    def test_driver_source_changes_invalidate_userspace_not_kernel(self):
        self.execute("musl")
        original_builder = self.namespace["builder_id"]
        original_key = self.namespace["key"]
        self.execute("kernel")
        (self.driver / "virgl_mapped_buffer.c").write_text("changed storage implementation\n")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            self.execute("musl", prebuilt=self.artifacts, system="darwin")
        self.execute("kernel", runner=AssertionError("kernel must remain cached"))
        self.execute("musl")
        self.assertEqual(self.namespace["builder_id"], original_builder)
        self.assertNotEqual(self.namespace["key"], original_key)
        self.assertIn(
            f"{self.namespace['cache'] / 'linux-builders' / original_builder}:/var/cache/sentinel-build",
            self.calls[-1][0],
        )

    def test_driver_rename_invalidates_cached_artifact(self):
        self.execute("musl")
        (self.driver / "virgl_mapped_buffer.c").rename(self.driver / "renamed.c")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            self.execute("musl", prebuilt=self.artifacts, system="darwin")

    def test_bridge_and_transport_changes_invalidate_both_userspace_artifacts(self):
        for file in (
            self.bridge,
            self.transport / "gpu_transport.c",
            self.transport / "gpu_transport.h",
        ):
            with self.subTest(input=file.name):
                self.execute("musl")
                self.execute("glibc")
                self.execute("kernel")
                file.write_text(file.read_text() + "changed\n")
                for libc in ("musl", "glibc"):
                    with self.assertRaisesRegex(RuntimeError, "does not match"):
                        self.execute(libc, prebuilt=self.artifacts, system="darwin")
                self.execute("kernel", runner=AssertionError("kernel must remain cached"))

    def test_missing_transport_fails_before_builder(self):
        for file in self.transport.iterdir():
            file.unlink()
        with self.assertRaisesRegex(RuntimeError, "Missing guest graphics build inputs"):
            self.execute(runner=AssertionError("must not launch incomplete build"))

    def test_missing_bridge_fails_before_builder(self):
        self.bridge.unlink()
        with self.assertRaises(FileNotFoundError):
            self.execute(runner=AssertionError("must not launch incomplete build"))

    def test_missing_driver_fails_before_builder(self):
        (self.driver / "virgl_mapped_buffer.c").unlink()
        with self.assertRaisesRegex(RuntimeError, "Missing guest graphics build inputs"):
            self.execute(runner=AssertionError("must not launch incomplete build"))

    def test_macos_cache_miss_uses_explicit_helper_not_staging_parent(self):
        lock = self.desktop / "runtime.lock.json"
        config = json.loads(lock.read_text())
        kernel = b"verified build kernel"
        digest = hashlib.sha256(kernel).hexdigest()
        config["platforms"]["macos-arm64"]["workspaceRuntime"].update(
            kernelFileSha256=digest, initImage="example/init@sha256:abc"
        )
        lock.write_text(json.dumps(config))
        cache = self.desktop / "build/graphics-sources/guest"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / digest).write_bytes(kernel)
        output = self.desktop / ".staged-release/graphics"
        helper = self.desktop / "published/sentinel-workspace-runtime"
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("sys.argv", [str(self.script), str(output), "musl", str(helper)]),
            patch("sys.platform", "darwin"),
            patch("subprocess.Popen", side_effect=RuntimeError("stop before VM")) as launch,
            patch("subprocess.run", side_effect=AssertionError("no external commands")),
            self.assertRaisesRegex(RuntimeError, "stop before VM"),
        ):
            runpy.run_path(str(self.script), run_name="__main__")
        self.assertEqual(Path(launch.call_args.args[0][0]), helper.resolve())
        self.assertEqual(len(launch.call_args.args[0]), 4)
        self.assertNotEqual(helper.parent, output.parent)

    def test_macos_glibc_build_requests_pinned_compiler_not_workspace_image(self):
        lock = self.desktop / "runtime.lock.json"
        config = json.loads(lock.read_text())
        kernel = b"verified build kernel"
        digest = hashlib.sha256(kernel).hexdigest()
        config["platforms"]["macos-arm64"]["workspaceRuntime"].update(
            kernelFileSha256=digest, initImage="example/init@sha256:abc"
        )
        lock.write_text(json.dumps(config))
        cache = self.desktop / "build/graphics-sources/guest"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / digest).write_bytes(kernel)
        responses = queue.Queue()
        responses.put(json.dumps({"event": "ready"}) + "\n")
        requests = []

        def write(line):
            request = json.loads(line)
            requests.append(request)
            if request["action"] == "exec":
                output = Path(
                    next(item["project"] for item in requests if item["action"] == "start")
                )
                with tarfile.open(output / "graphics.tar.xz", "w:xz") as archive:
                    data = b"fixture-graphics"
                    info = tarfile.TarInfo("./version")
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
            responses.put(json.dumps({"id": request["id"], "exitCode": 0}) + "\n")

        helper = SimpleNamespace(
            stdin=SimpleNamespace(
                write=write, flush=lambda: None, close=lambda: responses.put(None)
            ),
            stdout=iter(responses.get, None),
            poll=lambda: None,
            wait=lambda timeout: 0,
        )
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("sys.argv", [str(self.script), str(self.artifacts), "glibc"]),
            patch("sys.platform", "darwin"),
            patch("subprocess.Popen", return_value=helper),
            patch("subprocess.check_output", return_value="19327352832"),
            patch("subprocess.run", side_effect=AssertionError("no external commands")),
        ):
            runpy.run_path(str(self.script), run_name="__main__")
        start = next(item for item in requests if item["action"] == "start")
        self.assertEqual(start["distribution"], "ubuntu")
        self.assertEqual(start["image_reference"], "example/ubuntu-compiler@sha256:def")
        self.assertEqual(requests[-1]["action"], "stop")

    def test_verified_published_artifact_is_not_recopied(self):
        self.execute()
        source = self.namespace["artifact"]
        target = self.artifacts / "mesa-linux-arm64.tar.xz"
        before = target.stat()
        with patch("shutil.copy2", side_effect=AssertionError("must keep verified artifact")):
            self.namespace["publish_file"](source, target)
        self.assertEqual(target.stat().st_ino, before.st_ino)
        self.assertEqual(target.stat().st_mtime_ns, before.st_mtime_ns)

    def test_failed_artifact_copy_preserves_published_bytes(self):
        self.execute()
        source = self.namespace["artifact"]
        target = self.artifacts / "published.tar.xz"
        target.write_bytes(b"previous complete artifact")

        def failed_copy(source, staged):
            Path(staged).write_bytes(b"incomplete")
            raise OSError("copy failed")

        with (
            patch("shutil.copy2", side_effect=failed_copy),
            self.assertRaisesRegex(OSError, "copy failed"),
        ):
            self.namespace["publish_file"](source, target)
        self.assertEqual(target.read_bytes(), b"previous complete artifact")
        self.assertEqual(list(target.parent.glob(".graphics-artifact-*")), [])
