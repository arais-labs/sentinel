"""Exercise the fixture's two independent clipboard owners without host GTK."""

import ast
import io
import json
import os
from pathlib import Path
import secrets
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def fixture_nodes(*names):
    path = Path(__file__).resolve().parents[1] / "fixtures/desktop-contract.py"
    tree = ast.parse(path.read_text())
    return ast.Module(
        body=[node for node in tree.body if getattr(node, "name", None) in names], type_ignores=[]
    )


class ClipboardOracleTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "fixtures/desktop-contract.py"
        self.state = {"focused": True}
        self.events = []

        def application_copy(text, _length):
            self.events.append("application-copy")
            self.state.update(owner="application", text=text)

        def sentinel_read():
            self.assertEqual(self.state["owner"], "application")
            self.events.append("sentinel-read")
            return self.state["text"]

        def sentinel_write(text):
            self.events.append("sentinel-write")
            self.state.update(owner="sentinel", text=text)
            self.owner_callback(None, None)

        def connect(signal, callback):
            self.assertEqual(signal, "owner-change")
            self.owner_callback = callback
            return 1

        def disconnect(handler):
            self.assertEqual(handler, 1)
            self.owner_callback = None

        def application_paste(callback, data):
            self.assertEqual(self.state["owner"], "sentinel")
            self.events.append("application-paste")
            callback(None, self.state["text"], data)

        self.application = SimpleNamespace(
            set_text=application_copy,
            request_text=application_paste,
            connect=connect,
            disconnect=disconnect,
        )
        self.clipboard = SimpleNamespace(read=sentinel_read, write=sentinel_write)

        def connect_copy(signal, callback):
            self.assertEqual(signal, "key-press-event")
            self.copy_callback = callback
            return 2

        def disconnect_copy(handler):
            self.assertEqual(handler, 2)
            self.copy_callback = None

        def execute(action):
            self.assertEqual(action, {"type": "keypress", "keys": ["Control_L", "c"]})
            self.events.append("copy-gesture")
            self.copy_callback(None, SimpleNamespace(keyval=99, state=4))

        self.desktop = SimpleNamespace(execute=execute)
        namespace = {
            "threading": threading,
            "secrets": secrets,
            "on_gui": lambda callback: callback(),
            "window": SimpleNamespace(is_active=lambda: self.state["focused"]),
            "area": SimpleNamespace(
                has_focus=lambda: self.state["focused"],
                connect=connect_copy,
                disconnect=disconnect_copy,
            ),
            "Gtk": SimpleNamespace(
                Clipboard=SimpleNamespace(get=lambda _selection: self.application)
            ),
            "Gdk": SimpleNamespace(
                SELECTION_CLIPBOARD=object(),
                KEY_c=99,
                keyval_to_lower=lambda value: value,
                ModifierType=SimpleNamespace(CONTROL_MASK=4),
                selection_owner_get=lambda _selection: (
                    "local-window" if self.state.get("owner") == "application" else None
                ),
                Display=SimpleNamespace(get_default=lambda: SimpleNamespace(sync=lambda: None)),
            ),
            "clipboard": self.clipboard,
            "desktop": self.desktop,
            "session": {"clipboard_backend": "wayland"},
        }
        exec(compile(fixture_nodes("clipboard_contract_transfer"), str(path), "exec"), namespace)
        self.exercise = namespace["clipboard_contract_transfer"]

    def test_destination_barrier_precedes_single_production_read(self):
        def observed(text, *, phase):
            self.assertEqual(phase, "copy")
            self.assertEqual(text, self.state["text"])
            self.events.append("native-selection")

        self.exercise(observer=SimpleNamespace(wait=observed))
        self.assertEqual(
            self.events,
            [
                "copy-gesture",
                "application-copy",
                "native-selection",
                "sentinel-read",
                "sentinel-write",
                "application-paste",
            ],
        )

    def test_two_independent_owners_in_both_directions(self):
        self.assertEqual(
            self.exercise(),
            {
                "application_to_sentinel": True,
                "sentinel_to_application": True,
                "backend": "wayland",
            },
        )
        self.assertEqual(
            self.events,
            [
                "copy-gesture",
                "application-copy",
                "sentinel-read",
                "sentinel-write",
                "application-paste",
            ],
        )
        self.assertIsNone(self.copy_callback)

    def test_wrong_application_selection_is_failure(self):
        self.clipboard.read = lambda: "stale CLI selection"
        with self.assertRaisesRegex(AssertionError, "focused application's"):
            self.exercise()

    def test_diagnostic_mode_records_only_copy_mismatch_not_a_passing_transfer(self):
        self.clipboard.read = lambda: "persisted clipboard"
        result = self.exercise(diagnose_copy_mismatch=True)
        self.assertIs(result["application_to_sentinel"], False)
        self.assertIsNone(result["sentinel_to_application"])
        self.assertEqual(result["failure"]["kind"], "application-selection-mismatch")
        self.assertEqual(result["failure"]["received"], "persisted clipboard")
        self.assertNotIn("sentinel-write", self.events)

    def test_diagnostic_mode_does_not_swallow_clipboard_transport_errors(self):
        def fail():
            raise TimeoutError("transport failed")

        self.clipboard.read = fail
        with self.assertRaisesRegex(TimeoutError, "transport failed"):
            self.exercise(diagnose_copy_mismatch=True)

    def test_focus_stealing_is_not_accepted(self):
        write = self.clipboard.write

        def lose_focus(text):
            write(text)
            self.state["focused"] = False

        self.clipboard.write = lose_focus
        with self.assertRaisesRegex(AssertionError, "keyboard focus"):
            self.exercise()

    def test_asynchronous_owner_notification_precedes_application_paste(self):
        notified = threading.Event()

        def asynchronous_write(text):
            self.events.append("sentinel-write")
            self.owner_callback(None, None)  # Delayed original ownership event.

            def publish_owner():
                self.state.update(owner="sentinel", text=text)
                self.owner_callback(None, None)
                notified.set()

            threading.Thread(target=publish_owner).start()

        self.clipboard.write = asynchronous_write
        self.exercise()
        self.assertTrue(notified.wait(1))
        self.assertIsNone(self.owner_callback)


class SelectionObserverTests(unittest.TestCase):
    def setUp(self):
        namespace = dict(
            globals(), signal=signal, subprocess=subprocess, tempfile=tempfile, time=time
        )
        exec(compile(fixture_nodes("WaylandSelectionObserver"), "<fixture>", "exec"), namespace)
        self.observer = namespace["WaylandSelectionObserver"]()
        self.observer.marker = "baseline"
        self.addCleanup(self.observer.errors.close)

    @staticmethod
    def record(text, state="data", oversize=False):
        return (json.dumps(dict(text=text, state=state, oversize=oversize)) + "\n").encode()

    def test_baseline_and_nil_transitions_are_recorded_not_accepted_as_copy(self):
        self.observer.buffer = (
            self.record("baseline") + self.record("", "nil") + self.record("copied")
        )
        self.observer.wait("copied", phase="copy")
        self.assertEqual([e["text"] for e in self.observer.events], ["baseline", "", "copied"])

    def test_competing_selection_fails_even_if_expected_selection_is_queued(self):
        self.observer.buffer = self.record("other owner") + self.record("copied")
        with self.assertRaisesRegex(AssertionError, "competing"):
            self.observer.wait("copied", phase="copy")

    def test_escaped_maximum_payload_survives_fragmented_records(self):
        payload = "\x00" * 262144
        record = self.record(payload)
        chunks = [record[i : i + 4096] for i in range(0, len(record), 4096)]
        self.observer.process = SimpleNamespace(stdout=SimpleNamespace(fileno=lambda: 42))
        with (
            patch.object(select, "select", return_value=([42], [], [])),
            patch.object(os, "read", side_effect=chunks),
        ):
            self.observer.wait(payload, phase="copy")
        self.assertEqual(len(self.observer.events), 1)

    def test_oversized_payload_is_rejected(self):
        self.observer.buffer = self.record("copied", oversize=True)
        with self.assertRaisesRegex(AssertionError, "256 KiB"):
            self.observer.wait("copied", phase="copy")

    def test_timeout_does_not_retry_copy(self):
        self.observer.process = SimpleNamespace(stdout=object())
        with patch.object(select, "select", return_value=([], [], [])):
            with self.assertRaisesRegex(TimeoutError, "selection did not reach copy"):
                self.observer.wait("copied", phase="copy")

    def test_cleanup_failure_preserves_original_failure_and_closes_streams(self):
        self.observer.process = SimpleNamespace(pid=123, stdout=io.BytesIO())
        original = AssertionError("real copy failed")
        with (
            patch.object(os, "killpg", side_effect=PermissionError("cleanup")),
            patch.object(sys, "stderr", io.StringIO()),
        ):
            self.observer.__exit__(AssertionError, original, None)
        self.assertTrue(self.observer.process.stdout.closed)
        self.assertTrue(self.observer.errors.closed)
        self.assertIn("cleanup", original.__notes__[0])

    def test_cleanup_failure_without_original_is_reported(self):
        self.observer.process = SimpleNamespace(pid=123, stdout=io.BytesIO())
        with (
            patch.object(os, "killpg", side_effect=PermissionError("cleanup")),
            patch.object(sys, "stderr", io.StringIO()),
        ):
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                self.observer.__exit__(None, None, None)
