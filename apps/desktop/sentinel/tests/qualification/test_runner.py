"""Selection and fail-closed reporting checks; never start a VM."""

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("qualification", Path(__file__).with_name("run.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class QualificationTests(unittest.TestCase):
    def setUp(self):
        # Runner lifecycle tests must not read/hash a developer's real runtime.
        self.enterContext(patch.object(runner, "source_identity", return_value="stable"))
        self.enterContext(
            patch.object(runner, "runtime_identity", return_value={"root": "fixture"})
        )

    def test_matrix_and_filters(self):
        self.assertEqual(len(runner.selections()), 32)
        self.assertEqual(
            runner.selections(["debian"], ["plasma"], ["chrome"]),
            [
                {
                    "distribution": "debian",
                    "desktop": "plasma",
                    "browser": "chrome",
                    "status": "unrun",
                }
            ],
        )
        self.assertEqual(len(runner.selections(["ubuntu", "alpine"], ["xfce", "gnome"])), 10)

    def test_failed_and_unrun_rows_cannot_pass(self):
        rows = runner.selections(["ubuntu"], ["xfce", "gnome"], ["firefox"])
        rows[0]["status"] = "pass"
        pending = runner.report(rows)
        self.assertEqual(pending["status"], "fail")
        self.assertFalse(pending["complete"])
        rows[1]["status"] = "fail"
        self.assertEqual(runner.report(rows)["status"], "fail")
        self.assertTrue(runner.report(rows)["complete"])
        rows[1]["status"] = "pass"
        passed = runner.report(rows)
        self.assertEqual(passed["status"], "pass")
        self.assertEqual(
            passed["browser_qualification"],
            "Selected automation lifecycle/GPU/pixels/input/confinement; Firefox selection also requires native Firefox",
        )
        self.assertEqual(passed["coverage"], "desktop-and-selected-browser-lifecycle-gpu")

    def test_report_preserves_failure_evidence(self):
        rows = [
            {
                "distribution": "ubuntu",
                "desktop": "xfce",
                "status": "fail",
                "exit_code": 7,
                "error": "sandbox startup failed",
                "evidence": "/example/run",
            }
        ]
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "report.json"
            runner.save_report(destination, rows)
            saved = json.loads(destination.read_text())
            self.assertEqual(saved["rows"], rows)
            self.assertEqual(saved["counts"], {"pass": 0, "fail": 1, "unrun": 0, "unsupported": 0})
            self.assertFalse(destination.with_suffix(".tmp").exists())

    def test_adapter_failure_is_recorded_without_launching_vm(self):
        process = MagicMock()
        process.poll.return_value = 17
        process.returncode = 17
        row = runner.selections(["alpine"], ["lxqt"])[0]
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(runner.subprocess, "Popen", return_value=process) as launch,
        ):
            result = runner.run_row(row, Path(temporary), threading.Event(), 10)
            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["exit_code"], 17)
            self.assertTrue((Path(result["evidence"]) / "console.log").exists())
            self.assertEqual(
                json.loads((Path(result["evidence"]) / "result.json").read_text()),
                result,
            )
            self.assertEqual(launch.call_args.kwargs["env"]["SENTINEL_TEST_DESKTOP"], "lxqt")

    def test_explicit_unsupported_selection_is_reported_without_launch(self):
        rows = runner.selections(["alpine"], ["xfce"], ["chrome"])
        self.assertEqual(rows[0]["status"], "unsupported")
        self.assertEqual(runner.report(rows)["counts"]["unsupported"], 1)
        self.assertEqual(runner.report(rows)["status"], "fail")
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(runner.subprocess, "Popen") as launch,
        ):
            result = runner.run_row(rows[0], Path(temporary), threading.Event(), 10)
            self.assertEqual(result, rows[0])
            launch.assert_not_called()
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_cli_browser_filter_and_unsupported_exit(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                runner.main(
                    [
                        "--list",
                        "--distribution",
                        "debian",
                        "--desktop",
                        "kde",
                        "--browser",
                        "chrome",
                    ]
                ),
                0,
            )
        rows = json.loads(output.getvalue())["rows"]
        self.assertEqual(rows, runner.selections(["debian"], ["plasma"], ["chrome"]))
        with (
            tempfile.TemporaryDirectory() as temporary,
            contextlib.redirect_stdout(io.StringIO()),
            patch.object(runner.subprocess, "Popen") as launch,
        ):
            self.assertEqual(
                runner.main(
                    [
                        "--distribution",
                        "alpine",
                        "--desktop",
                        "xfce",
                        "--browser",
                        "chrome",
                        "--output-root",
                        temporary,
                    ]
                ),
                1,
            )
            launch.assert_not_called()
            saved = json.loads(next(Path(temporary).glob("run-*/report.json")).read_text())
            self.assertEqual(saved["rows"][0]["status"], "unsupported")

    def test_browser_evidence_and_environment_are_separate(self):
        process = MagicMock()
        process.poll.return_value = 0
        process.returncode = 0
        rows = runner.selections(["debian"], ["xfce"])
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(runner.subprocess, "Popen", return_value=process) as launch,
            patch.dict(runner.os.environ, {"SENTINEL_DESKTOP_DIAGNOSE_CLIPBOARD": "1"}),
        ):
            for row in rows:
                result = runner.run_row(row, Path(temporary), threading.Event(), 10)
                self.assertEqual(result["status"], "pass")
                self.assertEqual(
                    Path(result["evidence"]),
                    Path(temporary) / "debian" / "xfce" / row["browser"],
                )
                environment = launch.call_args.kwargs["env"]
                self.assertEqual(environment["SENTINEL_TEST_BROWSER"], row["browser"])
                self.assertNotIn("SENTINEL_DESKTOP_DIAGNOSE_CLIPBOARD", environment)

    def test_changed_inputs_during_run_fail(self):
        process = MagicMock()
        process.poll.return_value = 0
        process.returncode = 0
        row = runner.selections(["debian"], ["xfce"], ["chrome"])[0]
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(runner.subprocess, "Popen", return_value=process),
            patch.object(runner, "source_identity", side_effect=["before", "after"]),
        ):
            result = runner.run_row(row, Path(temporary), threading.Event(), 10, "before")
            self.assertEqual(result["status"], "fail")
            self.assertIn("inputs changed", result["error"])

    def test_changed_inputs_refuse_launch(self):
        row = runner.selections(["alpine"], ["xfce"])[0]
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(runner.subprocess, "Popen") as launch,
        ):
            result = runner.run_row(row, Path(temporary), threading.Event(), 10, "stale")
            launch.assert_not_called()
            self.assertEqual(result["status"], "fail")
            self.assertIn("inputs changed", result["error"])

    def test_changed_runtime_refuses_launch(self):
        row = runner.selections(["debian"], ["xfce"], ["chrome"])[0]
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(runner.subprocess, "Popen") as launch,
        ):
            result = runner.run_row(
                row,
                Path(temporary),
                threading.Event(),
                10,
                expected_runtime={"root": "old"},
            )
            launch.assert_not_called()
            self.assertEqual(result["status"], "fail")
            self.assertIn("Runtime artifacts changed since", result["error"])

    def test_changed_runtime_after_success_fails(self):
        process = MagicMock(returncode=0)
        process.poll.return_value = 0
        row = runner.selections(["debian"], ["xfce"], ["chrome"])[0]
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(runner.subprocess, "Popen", return_value=process),
            patch.object(runner, "runtime_identity", side_effect=[{"bytes": "a"}, {"bytes": "b"}]),
        ):
            result = runner.run_row(row, Path(temporary), threading.Event(), 10)
            self.assertEqual(result["status"], "fail")
            self.assertIn("Runtime artifacts changed while", result["error"])


class RuntimeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        self.enterContext(patch.object(runner, "DESKTOP_ROOT", self.root))
        self.enterContext(patch.dict(os.environ, {"SENTINEL_TEST_RUNTIME": "package"}))
        self.package = self.root / "package"
        (self.package / "graphics").mkdir(parents=True)
        (self.package / "workspace-images/debian/blobs/sha256").mkdir(parents=True)
        self.entry = {
            "layout": "debian",
            "reference": "image@sha256:" + "a" * 64,
            "digest": "sha256:" + "a" * 64,
        }
        (self.package / "manifest.json").write_text(
            json.dumps({"initImage": "init@sha256:" + "b" * 64})
        )
        (self.package / "workspace-images/manifest.json").write_text(
            json.dumps({"images": {"debian": self.entry}})
        )
        for name in (
            "sentinel-workspace-runtime",
            "kernel",
            "graphics/renderer",
            "workspace-images/debian/blobs/sha256/layer",
        ):
            (self.package / name).write_bytes(b"original")

    def test_records_live_bytes_and_reuses_hashes(self):
        cache = {}
        with patch.object(runner.hashlib, "sha256", wraps=runner.hashlib.sha256) as hash_file:
            first = runner.runtime_identity("debian", cache)
            calls = hash_file.call_count
            self.assertEqual(calls, 6)
            self.assertEqual(first, runner.runtime_identity("debian", cache))
            self.assertEqual(hash_file.call_count, calls)
            self.assertEqual(first["workspace_image"], self.entry)

    def test_same_size_change_with_restored_mtime_is_detected(self):
        cache = {}
        before = runner.runtime_identity("debian", cache)
        blob = self.package / "workspace-images/debian/blobs/sha256/layer"
        original_stat = blob.stat()
        blob.write_bytes(b"modified")
        os.utime(blob, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        after = runner.runtime_identity("debian", cache)
        self.assertNotEqual(before, after)
        key = "workspace-images/debian/blobs/sha256/layer"
        self.assertNotEqual(before["artifacts"][key]["sha256"], after["artifacts"][key]["sha256"])

    def test_cache_is_shared_across_rows(self):
        cache = {}
        process = MagicMock(returncode=0)
        process.poll.return_value = 0
        with (
            patch.object(runner.subprocess, "Popen", return_value=process),
            patch.object(runner, "source_identity", return_value="stable"),
            patch.object(runner.hashlib, "sha256", wraps=runner.hashlib.sha256) as hash_file,
        ):
            expected = runner.runtime_identity("debian", cache)
            for row in runner.selections(["debian"], ["xfce"], ["chrome", "firefox"]):
                result = runner.run_row(
                    row,
                    self.root / "evidence",
                    threading.Event(),
                    10,
                    "stable",
                    expected,
                    cache,
                )
                self.assertEqual(result["status"], "pass")
            self.assertEqual(hash_file.call_count, 6)

    def test_symlink_retarget_is_detected(self):
        link = self.package / "graphics/library"
        (self.root / "first").write_bytes(b"same")
        (self.root / "second").write_bytes(b"same")
        link.symlink_to(self.root / "first")
        cache = {}
        before = runner.runtime_identity("debian", cache)
        link.unlink()
        link.symlink_to(self.root / "second")
        self.assertNotEqual(before, runner.runtime_identity("debian", cache))

    def test_missing_artifact_fails_closed(self):
        (self.package / "kernel").unlink()
        with self.assertRaises(FileNotFoundError):
            runner.runtime_identity("debian", {})

    def test_added_graphics_file_changes_identity(self):
        cache = {}
        before = runner.runtime_identity("debian", cache)
        (self.package / "graphics/new-library").write_bytes(b"new")
        self.assertNotEqual(before, runner.runtime_identity("debian", cache))


if __name__ == "__main__":
    unittest.main()
