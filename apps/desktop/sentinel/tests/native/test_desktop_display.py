"""Real private sockets + native capture; synthetic frames require no running VM.

Run: python3 -m unittest discover -s tests/native -p 'test_desktop_display.py'
"""

import base64
import importlib.util
import json
from pathlib import Path
import select
import socket
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

DESKTOP = Path(__file__).resolve().parents[2]
GRAPHICS = DESKTOP / "native/graphics"
spec = importlib.util.spec_from_file_location(
    "sentinel_display", GRAPHICS / "guest/sentinel_display.py"
)
device = importlib.util.module_from_spec(spec)
spec.loader.exec_module(device)


class DisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="sentinel-display-build-", dir="/tmp")
        cls.binary = str(Path(cls.build.name) / "display-test")
        subprocess.run(
            [
                "clang",
                "-fobjc-arc",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I" + str(GRAPHICS / "video"),
                str(DESKTOP / "tests/native/desktop-display.m"),
                str(GRAPHICS / "video/DesktopDisplayServer.m"),
                "-framework",
                "Foundation",
                "-framework",
                "CoreVideo",
                "-framework",
                "ImageIO",
                "-framework",
                "CoreGraphics",
                "-o",
                cls.binary,
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.root = tempfile.TemporaryDirectory(prefix="sentinel-display-", dir="/tmp")
        self.process = subprocess.Popen(
            [self.binary, self.root.name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.assertEqual(self.event(), {"ready": True})
        self.controls = []

    def tearDown(self):
        for control in self.controls:
            control.close()
        if self.process.poll() is None:
            self.process.stdin.write(b'{"action":"close"}\n')
        try:
            _, errors = self.process.communicate(timeout=5)
            self.assertEqual(self.process.returncode, 0, errors.decode())
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.communicate()
            self.root.cleanup()

    def event(self):
        # Deadline guards a broken protocol, not a delay to let it "settle".
        self.assertTrue(select.select([self.process.stdout], [], [], 5)[0], "Native event missing")
        return json.loads(self.process.stdout.readline())

    def connect(self, name):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(5)
        connection.connect(str(Path(self.root.name) / name))
        self.addCleanup(connection.close)
        return connection

    def desktop(self):
        result = device.Desktop(str(Path(self.root.name) / "control.sock"), clipboard=Mock())
        result.clipboard.session.return_value = ("wayland", {})
        result.clipboard.credentials.return_value = {}
        result.clipboard.session_path = Path(self.root.name) / "session.json"
        self.controls.append(result)
        return result

    def test_stale_socket_is_reclaimed_after_a_crash(self):
        with tempfile.TemporaryDirectory(prefix="sentinel-stale-", dir="/tmp") as root:
            for name in ("video.sock", "control.sock"):
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale:
                    stale.bind(str(Path(root) / name))
            result = subprocess.run(
                [self.binary, root],
                input=b'{"action":"close"}\n',
                capture_output=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertIn(b'"ready":true', result.stdout)
            self.assertFalse((Path(root) / "video.sock").exists())

    def test_existing_endpoints_are_not_deleted_when_startup_fails(self):
        for name in ("video.sock", "control.sock"):
            for kind in ("active", "file", "symlink"):
                with (
                    self.subTest(name=name, kind=kind),
                    tempfile.TemporaryDirectory(prefix="sentinel-owned-", dir="/tmp") as root,
                ):
                    endpoint = Path(root) / name
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                        if kind == "active":
                            listener.bind(str(endpoint))
                            listener.listen()
                        elif kind == "file":
                            endpoint.write_text("keep")
                        else:
                            endpoint.symlink_to("missing-target")
                        inode = endpoint.lstat().st_ino
                        result = subprocess.run(
                            [self.binary, root],
                            input=b"",
                            capture_output=True,
                            timeout=5,
                        )
                        self.assertEqual(result.returncode, 1, result.stderr.decode())
                        self.assertIn(b"Address already in use", result.stderr)
                        self.assertEqual(endpoint.lstat().st_ino, inode)
                        if name == "control.sock":
                            self.assertFalse(
                                (Path(root) / "video.sock").exists(),
                                "Roll back only our own listener",
                            )

    def test_capture_and_actions_share_the_real_display(self):
        desktop = self.desktop()
        self.assertEqual(desktop.geometry(), (800, 600))
        desktop.execute({"type": "move", "x": 799, "y": 599})
        self.assertEqual(
            self.event(),
            {"device": 1, "events": [[3, 0, 65535], [3, 1, 65535], [0, 0, 0]]},
        )
        capture = desktop.screenshot()
        self.assertEqual(capture["viewport"], {"width": 800, "height": 600})
        self.assertEqual(capture["cursor"], {"x": 799, "y": 599})
        self.assertTrue(
            base64.b64decode(capture["screenshot"].split(",")[1]).startswith(b"\x89PNG")
        )
        desktop.execute({"type": "keypress", "keys": ["Control_L", "a"]})
        self.assertEqual(self.event()["events"], [[1, 29, 1], [1, 30, 1], [0, 0, 0]])
        released = [self.event(), self.event()]
        self.assertEqual({event["events"][0][1] for event in released}, {29, 30})
        self.assertTrue(all(event["events"][0][2] == 0 for event in released))

    def test_close_preserves_replaced_endpoints(self):
        video = Path(self.root.name) / "video.sock"
        control = Path(self.root.name) / "control.sock"
        video.unlink()
        control.unlink()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as replacement:
            replacement.bind(str(video))
            replacement.listen()
            control.symlink_to("missing-target")
            identities = [path.lstat().st_ino for path in (video, control)]
            self.process.stdin.write(b'{"action":"close"}\n')
            self.assertEqual(self.event(), {"closed": True})
            self.process.wait(timeout=5)
            self.assertEqual([path.lstat().st_ino for path in (video, control)], identities)

    def test_partial_startup_reports_only_the_socket_it_bound(self):
        with tempfile.TemporaryDirectory(prefix="sentinel-partial-", dir="/tmp") as root:
            control = Path(root) / "control.sock"
            control.write_text("preserve")
            result = subprocess.run(
                [self.binary, root, "--ownership"], input=b"", capture_output=True, timeout=5
            )
            self.assertEqual(result.returncode, 1)
            events = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event"], "endpoint")
            self.assertEqual(events[0]["path"], str(Path(root) / "video.sock"))
            self.assertGreater(int(events[0]["inode"]), 0)
            self.assertEqual(control.read_text(), "preserve")
            self.assertFalse((Path(root) / "video.sock").exists())

    def test_disconnect_releases_held_input_and_exclusive_control(self):
        desktop = self.desktop()
        with self.assertRaisesRegex(RuntimeError, "busy"):
            self.desktop()
        desktop.input(1, [(1, 272, 1)])
        self.event()
        desktop.close()
        self.assertEqual(self.event(), {"device": 1, "events": [[1, 272, 0], [0, 0, 0]]})
        # Synchronize via the release event; no sleeps or polling.
        other = self.desktop()
        self.assertEqual(other.geometry(), (800, 600))

    def test_logical_shortcuts_follow_session_keymap_without_duplicate_modifiers(self):
        desktop = self.desktop()
        desktop.clipboard.session_path.write_text(
            json.dumps(
                {
                    "keyboard_keys": {"a": [45], "1": [42, 3], "v": [47]},
                }
            )
        )
        self.assertEqual(desktop.keycodes(["Control_L", "a"]), [29, 45])
        self.assertEqual(desktop.keycodes(["Shift_L", "1"]), [42, 3])
        with self.assertRaises(ValueError):
            desktop.keycodes(["z"])

    def test_invalid_events_rejected_without_touching_input(self):
        desktop = self.desktop()
        with self.assertRaises(RuntimeError):
            desktop.input(0, [(2, 8, -2147483648)])
        with self.assertRaises(RuntimeError):
            desktop.input(2, [(1, 1000, 1)])
        with self.assertRaises(ValueError):
            desktop.keycodes(["not_a_key"])
        self.assertEqual(desktop.geometry(), (800, 600))

    def test_pointer_initialization_synchronizes_absolute_position_without_buttons(self):
        desktop = self.desktop()
        desktop.initialize_pointer()
        self.assertEqual(
            self.event(),
            {
                "device": 1,
                "events": [
                    [3, 0, 32768],
                    [3, 1, 32767],
                    [0, 0, 0],
                    [3, 0, 32767],
                    [3, 1, 32767],
                    [0, 0, 0],
                ],
            },
        )
        with self.assertRaises(RuntimeError):
            desktop.input(0, [(2, 0, 121)])
        with self.assertRaises(RuntimeError):
            desktop.input(0, [(2, 2, 1)])
        desktop.close()

    def test_unicode_text_is_not_interpreted_by_a_shell(self):
        desktop = self.desktop()
        text = "Bonjour 世界 👋 $(touch /never)"
        desktop.execute({"type": "type", "text": text})
        desktop.clipboard.write.assert_called_once_with(text)
        self.assertEqual(self.event()["events"], [[1, 29, 1], [1, 42, 1], [1, 47, 1], [0, 0, 0]])
        self.event()
        self.event()
        self.event()

    def test_interrupted_drag_releases_button(self):
        desktop = self.desktop()
        move = desktop.move

        def interrupted(point):
            if point["x"] == 2:
                raise TimeoutError("cancelled")
            move(point)

        with patch.object(desktop, "move", side_effect=interrupted):
            with self.assertRaises(TimeoutError):
                desktop.execute({"type": "drag", "path": [{"x": 1, "y": 1}, {"x": 2, "y": 2}]})
        self.event()
        self.assertEqual(self.event()["events"][0], [1, 272, 1])
        self.assertEqual(self.event()["events"][0], [1, 272, 0])

    def test_viewer_detach_does_not_close_desktop(self):
        viewer = self.connect("video.sock")
        self.assertEqual(self.event(), {"keyframe": True})
        for kind, flags, value in [(2, 0, 1), (1, 0, 2), (2, 1, 3)]:
            self.process.stdin.write(
                json.dumps(
                    {"action": "publish", "type": kind, "flags": flags, "value": value}
                ).encode()
                + b"\n"
            )
            self.assertEqual(self.event(), {"published": True})
        packets = b""
        while len(packets) < 34:
            packets += viewer.recv(34 - len(packets))
        self.assertEqual((packets[0], packets[16], packets[17], packets[33]), (1, 2, 2, 3))
        viewer.close()
        self.assertEqual(self.desktop().geometry(), (800, 600))
        reconnected = self.connect("video.sock")
        self.assertEqual(self.event(), {"keyframe": True})
        reconnected.close()

    def test_audio_shares_viewer_transport_without_owning_computer_input(self):
        viewer = self.connect("video.sock")
        self.assertEqual(self.event(), {"keyframe": True})
        publisher = self.connect("control.sock")
        publisher.sendall(b'{"action":"audio"}\n')
        reader = publisher.makefile("rb")
        self.addCleanup(reader.close)
        self.assertEqual(json.loads(reader.readline()), {"ok": True})
        other = self.connect("control.sock")
        other.sendall(b'{"action":"audio"}\n')
        other_reader = other.makefile("rb")
        self.addCleanup(other_reader.close)
        self.assertEqual(json.loads(other_reader.readline()), {"ok": False})
        # An audio publisher never blocks the computer-use input lease.
        self.assertEqual(self.desktop().geometry(), (800, 600))
        opus = b"\xf4\xff\xfe"  # 10 ms CELT stereo TOC; payload is opaque to the worker.
        publisher.sendall(struct.pack("!H", len(opus)) + opus)
        packet = b""
        while len(packet) < 19:
            packet += viewer.recv(19 - len(packet))
        kind, flags, reserved, length, timestamp = struct.unpack("!BBHIQ", packet[:16])
        self.assertEqual((kind, flags, reserved, length), (5, 0, 0, 3))
        self.assertGreater(timestamp, 0)
        self.assertEqual(packet[16:], opus)
        # Reject a 20 ms packet on this fixed-format stream, without killing video.
        publisher.sendall(b"\x00\x03\xf8\xff\xfe")
        self.assertEqual(reader.read(1), b"")
        viewer.sendall(struct.pack("!BBHIQ", 4, 0, 0, 0, 0))
        self.assertEqual(self.event(), {"keyframe": True})

    def test_audio_rejects_oversized_packet_before_reading_payload(self):
        publisher = self.connect("control.sock")
        publisher.sendall(b'{"action":"audio"}\n')
        reader = publisher.makefile("rb")
        self.addCleanup(reader.close)
        self.assertEqual(json.loads(reader.readline()), {"ok": True})
        publisher.sendall(struct.pack("!H", 1276))
        self.assertEqual(reader.read(1), b"")
        self.assertEqual(self.desktop().geometry(), (800, 600))


if __name__ == "__main__":
    unittest.main()
