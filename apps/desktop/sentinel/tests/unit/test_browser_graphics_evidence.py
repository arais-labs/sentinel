"""Keep GLX qualification strict about implementation, not GLVND dispatch."""

import hashlib
import importlib.util
import io
import json
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import tarfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "app_parity", Path(__file__).parents[1] / "fixtures" / "app-parity.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class BrowserGraphicsEvidenceTests(unittest.TestCase):
    def native_packages(self, graphics):
        directory = graphics.parent / "packages/mesa"
        directory.mkdir(parents=True)
        entries = []
        for name, library in (
            ("mesa", "libgallium-test.so"),
            ("mesa-egl", "libEGL.so.1.0.0"),
            ("mesa-gl", "libGL.so.1.2.0"),
        ):
            archive = directory / f"{name}-26.1.6-r1.apk"
            with tarfile.open(archive, "w:gz") as package:
                for path, data in (
                    (".PKGINFO", f"pkgname = {name}\npkgver = 26.1.6-r1\n".encode()),
                    (f"usr/lib/{library}", library.encode()),
                ):
                    member = tarfile.TarInfo(path)
                    member.size = len(data)
                    package.addfile(member, io.BytesIO(data))
            entries.append(
                dict(
                    name=name,
                    apk=archive.name,
                    sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                )
            )
        (directory / "manifest.json").write_text(
            json.dumps(
                dict(
                    schema=1,
                    name="mesa",
                    distribution="alpine",
                    architecture="aarch64",
                    version="26.1.6-r1",
                    packages=entries,
                )
            )
        )
        return directory

    def test_native_package_hashes_verify_actual_loaded_bytes(self):
        with self.process_family([(100, [], 0), (110, ["--type=gpu-process"], 2)], "chromium") as (
            proc,
            graphics,
        ):
            self.native_packages(graphics)
            native = graphics.parent / "usr/lib"
            native.mkdir(exist_ok=True)
            rows = []
            for name in ("libgallium-test.so", "libEGL.so.1.0.0", "libGL.so.1.2.0"):
                library = native / name
                library.write_bytes(name.encode())
                info = library.stat()
                rows.append(
                    f"1000-2000 r-xp 00000000 {os.major(info.st_dev):x}:{os.minor(info.st_dev):x} "
                    f"{info.st_ino} /usr/lib/{name}\n"
                )
            for pid in (100, 110):
                (proc / str(pid) / "maps").write_text("".join(rows))
            result = fixture.browser_graphics_evidence(100, "chromium", proc, graphics)
            self.assertEqual([item["pid"] for item in result["processes"]], [110])
            # Matching name/location alone must never qualify a distro fallback.
            (native / "libgallium-test.so").write_bytes(b"unqualified distro library")
            with self.assertRaisesRegex(AssertionError, "unqualified graphics library"):
                fixture.browser_graphics_evidence(100, "chromium", proc, graphics)

    def test_native_package_corruption_or_missing_frontend_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            graphics = Path(temporary) / "graphics"
            graphics.mkdir()
            directory = self.native_packages(graphics)
            path = directory / "mesa-egl-26.1.6-r1.apk"
            original = path.read_bytes()
            path.write_bytes(b"corrupt")
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                fixture.packaged_graphics_hashes(graphics)
            path.write_bytes(original)
            manifest_path = directory / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["packages"] = [
                entry for entry in manifest["packages"] if entry["name"] != "mesa-gl"
            ]
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(RuntimeError, "Incomplete packaged"):
                fixture.packaged_graphics_hashes(graphics)
        fixture.packaged_graphics_hashes.cache_clear()

    @contextmanager
    def process_family(self, processes, app="chrome"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graphics = root / "graphics"
            graphics.mkdir()
            for name in ("libgallium-test.so", "libEGL_mesa.so.0"):
                (graphics / name).write_bytes(name.encode())
            executable = root / (
                "opt/google/chrome/chrome" if app == "chrome" else f"usr/lib/{app}/{app}"
            )
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"browser")
            proc = root / "proc"
            proc.mkdir()
            for pid, arguments, seccomp in processes:
                base = proc / str(pid)
                (base / "attr").mkdir(parents=True)
                (base / "fd").mkdir()
                (base / "fd/5").symlink_to("/dev/dri/renderD128")
                (base / "exe").symlink_to(executable)
                (base / "root").symlink_to(root, target_is_directory=True)
                (base / "stat").write_text(f"{pid} (browser) S {1 if pid == 100 else 100} 0\n")
                (base / "cmdline").write_bytes(
                    b"\0".join(
                        [str(executable).encode(), *[arg.encode() for arg in arguments], b""]
                    )
                )
                status = f"Uid:\t1000 1000 1000 1000\nSeccomp:\t{seccomp}\n"
                (base / "status").write_text(status)
                (base / "attr/current").write_text("chrome (unconfined)\n")
                for tid in (pid, pid + 1):
                    task = base / "task" / str(tid)
                    task.mkdir(parents=True)
                    (task / "status").write_text(status)
                rows = []
                for library in graphics.iterdir():
                    info = library.stat()
                    rows.append(
                        f"1000-2000 r-xp 00000000 {os.major(info.st_dev):x}:{os.minor(info.st_dev):x} "
                        f"{info.st_ino} /graphics/{library.name}\n"
                    )
                (base / "maps").write_text("".join(rows))
            try:
                with patch.object(fixture, "drm_driver_name", return_value="virtio_gpu"):
                    yield proc, graphics
            finally:
                fixture.packaged_graphics_hashes.cache_clear()

    def test_unsandboxed_main_with_graphics_does_not_replace_gpu_role(self):
        for app in ("chrome", "chromium"):
            with (
                self.subTest(app=app),
                self.process_family(
                    [(100, [], 0), (110, ["--type=gpu-process"], 2), (120, ["--type=broker"], 2)],
                    app,
                ) as (proc, graphics),
            ):
                result = fixture.browser_graphics_evidence(100, app, proc, graphics)
                self.assertEqual([item["pid"] for item in result["processes"]], [110])
                self.assertEqual(len(result["processes"][0]["confinement"]["threads"]), 2)

    def test_graphics_broker_without_gpu_process_cannot_qualify(self):
        with self.process_family([(100, [], 0), (120, ["--type=broker"], 2)]) as (proc, graphics):
            with self.assertRaisesRegex(RuntimeError, "No live browser process"):
                fixture.browser_graphics_evidence(100, "chrome", proc, graphics)

    def test_rewritten_chromium_titles_require_one_exact_gpu_role(self):
        for role, qualifies in (
            ("--type=gpu-process", True),
            ("--type=broker", False),
            ("--type=gpu-process-extra", False),
            ("--label=--type=gpu-process", False),
            ("--type=gpu-process --type=broker", False),
            ("--type=gpu-process --type=gpu-process", False),
        ):
            with (
                self.subTest(role=role),
                self.process_family([(100, [], 0), (110, [], 2)], "chromium") as (proc, graphics),
            ):
                (proc / "110/cmdline").write_bytes(
                    f"/usr/lib/chromium/chromium {role} --ozone-platform=wayland".encode()
                    + b"\0" * 16
                )
                if qualifies:
                    result = fixture.browser_graphics_evidence(100, "chromium", proc, graphics)
                    self.assertEqual([item["pid"] for item in result["processes"]], [110])
                else:
                    with self.assertRaisesRegex(RuntimeError, "No live browser process"):
                        fixture.browser_graphics_evidence(100, "chromium", proc, graphics)

    def test_gpu_process_must_have_seccomp_on_every_thread(self):
        with self.process_family([(100, [], 0), (110, ["--type=gpu-process"], 0)]) as (
            proc,
            graphics,
        ):
            with self.assertRaises(AssertionError) as failure:
                fixture.browser_graphics_evidence(100, "chrome", proc, graphics)
            self.assertEqual(failure.exception.args[0]["pid"], 110)
        with self.process_family([(100, [], 0), (110, ["--type=gpu-process"], 2)]) as (
            proc,
            graphics,
        ):
            (proc / "110/task/111/status").write_text("Uid:\t1000 1000 1000 1000\nSeccomp:\t0\n")
            with self.assertRaises(AssertionError) as failure:
                fixture.browser_graphics_evidence(100, "chrome", proc, graphics)
            self.assertEqual(failure.exception.args[0]["tid"], 111)

    def test_bypass_flag_on_main_or_broker_still_fails(self):
        for pid in (100, 120):
            with (
                self.subTest(pid=pid),
                self.process_family(
                    [
                        (100, ["--no-sandbox"] if pid == 100 else [], 0),
                        (110, ["--type=gpu-process"], 2),
                        (
                            120,
                            (
                                ["--type=broker", "--disable-gpu-sandbox"]
                                if pid == 120
                                else ["--type=broker"]
                            ),
                            2,
                        ),
                    ]
                ) as (proc, graphics),
            ):
                with self.assertRaisesRegex(AssertionError, "Browser sandbox bypass"):
                    fixture.browser_graphics_evidence(100, "chrome", proc, graphics)

    def test_firefox_parent_graphics_and_content_sandbox_are_unchanged(self):
        with self.process_family([(100, [], 0), (110, ["-contentproc", "tab"], 2)], "firefox") as (
            proc,
            graphics,
        ):
            result = fixture.browser_graphics_evidence(100, "firefox", proc, graphics)
            self.assertIn(100, [item["pid"] for item in result["processes"]])
            self.assertEqual([item["pid"] for item in result["sandboxed_content"]], [110])

    def test_all_observed_threads_must_be_filtered_nonroot(self):
        with tempfile.TemporaryDirectory() as directory:
            process = Path(directory) / "100"
            for tid in (100, 101):
                task = process / "task" / str(tid)
                task.mkdir(parents=True)
                (task / "status").write_text("Uid:\t1000 1000 1000 1000\nSeccomp:\t2\n")
            self.assertEqual(len(fixture.sandbox_thread_evidence(process)), 2)
            worker = process / "task/101/status"
            for status in (
                "Uid:\t1000 1000 1000 1000\nSeccomp:\t0\n",
                "Uid:\t0 0 0 0\nSeccomp:\t2\n",
            ):
                with self.subTest(status=status):
                    worker.write_text(status)
                    with self.assertRaises(AssertionError):
                        fixture.sandbox_thread_evidence(process)

    def test_disappeared_leader_cannot_qualify(self):
        with tempfile.TemporaryDirectory() as directory:
            process = Path(directory) / "100"
            (process / "task").mkdir(parents=True)
            with self.assertRaisesRegex(AssertionError, "leader disappeared"):
                fixture.sandbox_thread_evidence(process)

    def check_frontends(self, names, wanted, glvnd):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ["libgallium-test.so", *names]:
                (root / name).write_bytes(name.encode())
            gallium, frontends, hashes, dispatch = fixture.packaged_graphics_hashes(root)
            self.assertEqual(gallium.name, "libgallium-test.so")
            self.assertEqual({path.name for path in frontends}, set(wanted))
            self.assertEqual(dispatch, glvnd)
            self.assertEqual(set(hashes), {gallium, *frontends})
            for path, digest in hashes.items():
                self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        fixture.packaged_graphics_hashes.cache_clear()

    def test_glvnd_implementations_not_public_dispatch(self):
        self.check_frontends(
            ["libEGL.so.1", "libGL.so.1", "libEGL_mesa.so.0", "libGLX_mesa.so.0"],
            ["libEGL_mesa.so.0", "libGLX_mesa.so.0"],
            True,
        )

    def test_monolithic_mesa_egl_and_glx(self):
        self.check_frontends(["libEGL.so.1", "libGL.so.1"], ["libEGL.so.1", "libGL.so.1"], False)

    def test_missing_implementation_is_not_qualified(self):
        with self.assertRaisesRegex(RuntimeError, "No packaged Mesa"):
            self.check_frontends([], [], False)


if __name__ == "__main__":
    unittest.main()
