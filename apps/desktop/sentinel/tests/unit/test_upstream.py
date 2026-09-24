"""Patch preparation is repeatable, removes retired edits, and fails atomically."""

import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from upstream import prepare_source, preserve_source_times


class UpstreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.patch = self.work / "change.patch"
        self.patch.write_text("--- a/value.txt\n+++ b/value.txt\n@@ -1 +1 @@\n-before\n+after\n")
        with tarfile.open(self.work / "fixture-revision.tar.gz", "w:gz") as archive:
            data = b"before\n"
            info = tarfile.TarInfo("upstream/value.txt")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))

    def prepare(self, patches=None, revision="revision"):
        return prepare_source(
            self.work,
            "example/source",
            revision,
            "fixture",
            [self.patch] if patches is None else patches,
        )

    def test_repeat_build_does_not_reapply_or_download(self):
        result = self.prepare()
        self.assertEqual((result / "value.txt").read_text(), "after\n")
        with patch("subprocess.run", side_effect=AssertionError("unexpected rebuild")):
            self.assertEqual(self.prepare(), result)

    def test_changed_and_removed_patches_start_from_pristine_source(self):
        result = self.prepare()
        self.patch.write_text(self.patch.read_text().replace("+after", "+updated"))
        self.prepare()
        self.assertEqual((result / "value.txt").read_text(), "updated\n")
        self.prepare([])
        self.assertEqual((result / "value.txt").read_text(), "before\n")

    def test_source_times_preserve_unchanged_and_dirty_reverted_files(self):
        result = self.prepare()
        value = result / "value.txt"
        os.utime(value, ns=(1_000_000_000, 1_000_000_000))
        self.prepare([])
        self.assertGreater(value.stat().st_mtime_ns, 1_000_000_000)
        # Rebuild the staged tree with identical contents and old archive dates.
        staged = self.work / "staged"
        staged.mkdir()
        copy = staged / "value.txt"
        copy.write_bytes(value.read_bytes())
        os.utime(copy, ns=(0, 0))
        preserve_source_times(staged, result)
        self.assertEqual(copy.stat().st_mtime_ns, value.stat().st_mtime_ns)
        fresh = staged / "new.txt"
        fresh.write_text("new")
        os.utime(fresh, ns=(0, 0))
        # Never follow a symlink into another tree when preserving timestamps.
        external = self.work / "external"
        external.write_text("external")
        before = external.stat().st_mtime_ns
        (staged / "link").symlink_to(external)
        preserve_source_times(staged, result)
        self.assertGreater(fresh.stat().st_mtime_ns, 0)
        self.assertEqual(external.stat().st_mtime_ns, before)

    def test_failure_preserves_previous_tree_and_stamp(self):
        result = self.prepare()
        stamp = (result / ".sentinel-source").read_text()
        self.patch.write_text(self.patch.read_text().replace("-before", "-missing"))
        with self.assertRaisesRegex(RuntimeError, r"fixture@revision: change.patch failed"):
            self.prepare()
        self.assertEqual((result / "value.txt").read_text(), "after\n")
        self.assertEqual((result / ".sentinel-source").read_text(), stamp)
        self.assertEqual(list(result.glob("*.rej")), [])

    def test_partial_patch_set_is_never_published(self):
        second = self.work / "second.patch"
        second.write_text("--- a/value.txt\n+++ b/value.txt\n@@ -1 +1 @@\n-missing\n+bad\n")
        with self.assertRaisesRegex(RuntimeError, "second.patch failed"):
            self.prepare([self.patch, second])
        self.assertFalse((self.work / "fixture").exists())

    def test_legacy_generated_tree_is_replaced(self):
        old = self.work / "fixture"
        old.mkdir()
        (old / "stale.h").write_text("retired patch")
        self.prepare()
        self.assertFalse((old / "stale.h").exists())
        self.assertEqual((old / "value.txt").read_text(), "after\n")

    def test_revision_change_does_not_reuse_source(self):
        self.prepare()
        with patch("subprocess.run", side_effect=RuntimeError("download requested")):
            with self.assertRaisesRegex(RuntimeError, "download requested"):
                self.prepare(revision="next-revision")

    def test_real_ninja_rebuilds_changed_and_reverted_sources_only(self):
        tools = Path(__file__).resolve().parents[2] / "build/graphics-sources/venv/bin"
        meson = str(tools / "meson") if (tools / "meson").exists() else shutil.which("meson")
        ninja = str(tools / "ninja") if (tools / "ninja").exists() else shutil.which("ninja")
        if not meson or not ninja or not shutil.which("cc"):
            self.skipTest("Meson, Ninja and a C compiler are required")
        files = {
            "meson.build": "project('incremental', 'c')\nexecutable('probe', 'main.c', 'value.c')\n",
            "main.c": '#include <stdio.h>\nint value(void); int main(void) { printf("%d\\n", value()); }\n',
            "value.c": "int value(void) { return 1; }\n",
        }
        with tarfile.open(self.work / "fixture-revision.tar.gz", "w:gz") as archive:
            for name, content in files.items():
                data = content.encode()
                info = tarfile.TarInfo("upstream/" + name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        self.patch.write_text(
            "--- a/value.c\n+++ b/value.c\n@@ -1 +1 @@\n"
            "-int value(void) { return 1; }\n+int value(void) { return 2; }\n"
        )
        tree = self.prepare([])
        build = self.work / "objects"
        env = {**os.environ, "PATH": str(Path(ninja).parent) + os.pathsep + os.environ["PATH"]}

        def compile(reconfigure=False):
            subprocess.run(
                [
                    meson,
                    "setup",
                    *(["--reconfigure"] if reconfigure else []),
                    str(build),
                    str(tree),
                ],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run([ninja, "-C", str(build)], env=env, check=True, capture_output=True)
            return {file.name: file.stat().st_mtime_ns for file in build.rglob("*.o")}

        first = compile()
        self.assertEqual(len(first), 2)
        self.assertEqual(compile(True), first)
        self.prepare()
        patched = compile(True)
        self.assertEqual(patched["main.c.o"], first["main.c.o"])
        self.assertGreater(patched["value.c.o"], first["value.c.o"])
        self.assertEqual(subprocess.check_output([build / "probe"], text=True), "2\n")
        self.prepare([])
        reverted = compile(True)
        self.assertEqual(reverted["main.c.o"], first["main.c.o"])
        self.assertGreater(reverted["value.c.o"], patched["value.c.o"])
        self.assertEqual(subprocess.check_output([build / "probe"], text=True), "1\n")
