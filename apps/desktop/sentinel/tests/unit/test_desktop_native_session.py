import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

source = Path(__file__).resolve().parents[2] / "native/graphics/guest/desktop-native-session.py"
spec = importlib.util.spec_from_file_location("desktop_native_session", source)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeDesktopSessionTests(unittest.TestCase):
    def test_gnome_ready_requires_this_login_and_live_shell_identity(self):
        owner = {"uid": 1007, "token": "t" * 32, "session_id": "c7"}
        marker = {**owner, "schema": 1, "shell": {"pid": 42, "identity": "123"}}
        directory = Path("/run/user/1007/sentinel-desktop")
        with (
            patch.object(native, "read_json", return_value=marker),
            patch.object(native, "process_identity", return_value="123"),
            patch.object(native.Path, "stat", return_value=SimpleNamespace(st_uid=1007)),
        ):
            self.assertTrue(native.gnome_ready(directory, owner, 1007))
        invalid = [None, [], {**marker, "schema": 2}]
        invalid += [
            {**marker, key: value}
            for key, value in (("uid", 1008), ("token", "old-login"), ("session_id", "c6"))
        ]
        invalid += [
            {**marker, "shell": value}
            for value in (
                None,
                [],
                {},
                {"pid": True, "identity": "123"},
                {"pid": 0, "identity": "123"},
                {"pid": 42, "identity": ""},
            )
        ]
        for value in invalid:
            with self.subTest(marker=value), patch.object(native, "read_json", return_value=value):
                self.assertFalse(native.gnome_ready(directory, owner, 1007))
        for identity, uid in ((None, 1007), ("reused-pid", 1007), ("123", 1008)):
            with (
                self.subTest(identity=identity, uid=uid),
                patch.object(native, "read_json", return_value=marker),
                patch.object(native, "process_identity", return_value=identity),
                patch.object(native.Path, "stat", return_value=SimpleNamespace(st_uid=uid)),
            ):
                self.assertFalse(native.gnome_ready(directory, owner, 1007))
        with patch.object(native, "read_json", side_effect=FileNotFoundError):
            self.assertFalse(native.gnome_ready(directory, owner, 1007))
        with (
            patch.object(native, "read_json", side_effect=RuntimeError("private regular")),
            self.assertRaisesRegex(RuntimeError, "private regular"),
        ):
            native.gnome_ready(directory, owner, 1007)

    def test_private_state_rejects_symlink_and_nonprivate_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native.private_directory(root, os.getuid())
            path = root / "state.json"
            native.write_json(path, {"token": "a" * 32})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(native.read_json(path, os.getuid()), {"token": "a" * 32})
            link = root / "link.json"
            link.symlink_to(path)
            with self.assertRaises(OSError):
                native.read_json(link, os.getuid())
            path.chmod(0o644)
            with self.assertRaisesRegex(RuntimeError, "private regular"):
                native.read_json(path, os.getuid())
            root.chmod(0o755)
            with self.assertRaisesRegex(RuntimeError, "0700"):
                native.private_directory(root, os.getuid())

    def test_environment_preserves_pam_and_leaves_display_to_compositor(self):
        inherited = {
            "XDG_SESSION_ID": "c7",
            "XDG_RUNTIME_DIR": "/run/user/1007",
            "HOME": "/home/sentinel",
            "DISPLAY": ":99",
            "WAYLAND_DISPLAY": "stale",
        }
        request = {
            "command": ["/usr/bin/session"],
            "token": "t" * 32,
            "environment": {
                "XDG_SESSION_ID": "wrong",
                "HOME": "/root",
                "DISPLAY": ":3",
                "XDG_CURRENT_DESKTOP": "Example",
                "GPU_DRIVER": "native",
            },
        }
        environment = native.session_environment(request, inherited)
        self.assertEqual(environment["XDG_SESSION_ID"], "c7")
        self.assertEqual(environment["HOME"], "/home/sentinel")
        self.assertEqual(environment["XDG_CURRENT_DESKTOP"], "Example")
        self.assertEqual(environment["GPU_DRIVER"], "native")
        self.assertEqual(environment["SENTINEL_SESSION_TOKEN"], "t" * 32)
        self.assertNotIn("DISPLAY", environment)
        self.assertNotIn("WAYLAND_DISPLAY", environment)

    def test_login_id_and_valid_argv_are_required(self):
        request = {"command": ["session"], "token": "t" * 32}
        with self.assertRaisesRegex(RuntimeError, "register"):
            native.session_environment(request, {})
        for command in ("session --flag", [], ["session", 1], ["bad\0"]):
            with (
                self.subTest(command=command),
                self.assertRaisesRegex(RuntimeError, "argument list"),
            ):
                native.session_environment(
                    {**request, "command": command}, {"XDG_SESSION_ID": "c1"}
                )

    def test_display_socket_cannot_escape_runtime(self):
        runtime = Path("/run/user/1007")
        for name in ("../outside", "/tmp/socket", ""):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                native.wayland_socket({"WAYLAND_DISPLAY": name}, runtime)
        self.assertEqual(
            native.wayland_socket({"WAYLAND_DISPLAY": "wayland-4"}, runtime), runtime / "wayland-4"
        )

    def test_socket_readiness_requires_real_owned_listener(self):
        with tempfile.TemporaryDirectory(prefix="native-", dir="/tmp") as temporary:
            path = Path(temporary) / "s"
            path.write_text("not a socket")
            self.assertFalse(native.socket_ready(path, os.getuid()))
            path.unlink()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(path))
                server.listen()
                self.assertTrue(native.socket_ready(path, os.getuid()))
                self.assertFalse(native.socket_ready(path, os.getuid() + 1))

    def test_native_bus_uses_existing_canonical_socket(self):
        environment = {"XDG_RUNTIME_DIR": "/run/user/1007"}
        with (
            patch.object(native, "socket_ready", return_value=True),
            patch.object(native.os, "execvpe") as execute,
        ):
            native.ensure_bus(environment, Path("request"))
        self.assertEqual(environment["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1007/bus")
        execute.assert_not_called()

    def test_systemd_missing_user_bus_is_not_hidden_by_private_bus(self):
        with (
            patch.object(native, "socket_ready", return_value=False),
            patch.object(native.platform, "freedesktop_os_release", return_value={"ID": "debian"}),
            patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(RuntimeError, "systemd user session"),
        ):
            native.ensure_bus({"XDG_RUNTIME_DIR": "/run/user/1007"}, Path("request"))

    def test_alpine_reexecs_once_in_stock_session_bus(self):
        environment = {"XDG_RUNTIME_DIR": "/run/user/1007"}
        request = Path("/run/sentinel-login/token/launch.json")
        with (
            patch.object(native, "socket_ready", return_value=False),
            patch.object(native.platform, "freedesktop_os_release", return_value={"ID": "alpine"}),
            patch.dict(os.environ, {}, clear=True),
            patch.object(native.os, "execvpe") as execute,
        ):
            native.ensure_bus(environment, request)
        self.assertEqual(execute.call_args.args[0], "dbus-run-session")
        self.assertEqual(execute.call_args.args[1][-2:], ["run", str(request)])
        self.assertEqual(environment["SENTINEL_NATIVE_BUS_STARTED"], "1")
        environment["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/tmp/native-bus"
        with (
            patch.object(native, "socket_ready", return_value=False),
            patch.dict(os.environ, {"SENTINEL_NATIVE_BUS_STARTED": "1"}),
            patch.object(native.os, "execvpe") as execute,
        ):
            native.ensure_bus(environment, request)
        execute.assert_not_called()

    def test_autostart_publishes_actual_environment_without_custom_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            uid = os.getuid() or 1007
            owner = {
                "uid": uid,
                "token": "t" * 32,
                "session_id": "c7",
                "desktop": "gnome",
                "owner": {"pid": 123, "identity": "456", "pgrp": 123},
            }
            native.write_json(directory / "owner.json", owner)
            environment = {
                "XDG_SESSION_ID": "c7",
                "XDG_RUNTIME_DIR": str(directory.parent),
                "WAYLAND_DISPLAY": "compositor-selected",
                "DISPLAY": ":4",
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=real-bus",
            }
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(native.os, "getuid", return_value=uid),
                patch.object(native.os, "geteuid", return_value=uid),
                patch.object(native, "session_directory", return_value=directory),
                patch.object(native, "read_json", return_value=owner),
                patch.object(native, "process_identity", return_value="456"),
                patch.object(native, "socket_ready", return_value=True),
                patch.object(native, "gnome_ready", side_effect=[False, True]) as readiness,
                patch.object(native.time, "sleep"),
            ):
                self.assertEqual(native.publish(), 0)
            self.assertEqual(readiness.call_count, 2)
            ready = json.loads((directory / "ready.json").read_text())
            self.assertEqual(ready["environment"], environment)
            self.assertEqual(ready["owner"], owner["owner"])
            self.assertEqual(ready["token"], owner["token"])

    def test_x11_publication_checks_cookie_authentication_not_wayland(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            uid = os.getuid() or 1007
            owner = {
                "uid": uid,
                "token": "t" * 32,
                "session_id": "c7",
                "protocol": "x11",
                "owner": {"pid": 123, "identity": "456", "pgrp": 123},
            }
            environment = {
                "XDG_SESSION_ID": "c7",
                "DISPLAY": ":4",
                "XAUTHORITY": str(directory / "authority"),
            }
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(native.os, "getuid", return_value=uid),
                patch.object(native.os, "geteuid", return_value=uid),
                patch.object(native, "session_directory", return_value=directory),
                patch.object(native, "read_json", return_value=owner),
                patch.object(
                    native.Path, "lstat", return_value=SimpleNamespace(st_mode=0o100600, st_uid=uid)
                ),
                patch.object(native, "process_identity", return_value="456"),
                patch.object(
                    native.subprocess, "run", return_value=SimpleNamespace(returncode=0)
                ) as authenticate,
            ):
                self.assertEqual(native.publish(), 0)
            self.assertEqual(authenticate.call_args.args[0], ["xdpyinfo"])
            ready = json.loads((directory / "ready.json").read_text())
            self.assertEqual(ready["protocol"], "x11")
            self.assertNotIn("wayland_socket", ready)

    def test_systemd_autostart_without_environment_login_id_uses_live_private_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            uid = os.getuid() or 1007
            owner = {
                "uid": uid,
                "token": "t" * 32,
                "session_id": "c7",
                "owner": {"pid": 123, "identity": "456", "pgrp": 123},
            }
            environment = {
                "XDG_RUNTIME_DIR": str(directory.parent),
                "WAYLAND_DISPLAY": "actual-display",
            }
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(native.os, "getuid", return_value=uid),
                patch.object(native.os, "geteuid", return_value=uid),
                patch.object(native, "session_directory", return_value=directory),
                patch.object(native, "read_json", return_value=owner),
                patch.object(native, "process_identity", return_value="456"),
                patch.object(native, "socket_ready", return_value=True),
            ):
                self.assertEqual(native.publish(), 0)
            ready = json.loads((directory / "ready.json").read_text())
            self.assertEqual(ready["session_id"], "c7")
            self.assertEqual(ready["environment"], environment)
            self.assertNotIn("XDG_SESSION_ID", ready["environment"])

    def test_missing_environment_id_cannot_publish_dead_or_unregistered_owner(self):
        uid = os.getuid() or 1007
        valid = {
            "uid": uid,
            "token": "t" * 32,
            "session_id": "c7",
            "owner": {"pid": 123, "identity": "456"},
        }
        for owner, identity, reason in (
            (valid, None, "owner has exited"),
            (valid, "different-start-time", "owner has exited"),
            ({**valid, "session_id": None}, "456", "registered login"),
            ({**valid, "session_id": "../other"}, "456", "registered login"),
        ):
            with (
                self.subTest(owner=owner, identity=identity),
                patch.dict(os.environ, {}, clear=True),
                patch.object(native.os, "getuid", return_value=uid),
                patch.object(native.os, "geteuid", return_value=uid),
                patch.object(
                    native,
                    "session_directory",
                    return_value=Path("/run/user/1007/sentinel-desktop"),
                ),
                patch.object(native, "read_json", return_value=owner),
                patch.object(native, "process_identity", return_value=identity),
                patch.object(native, "write_json") as write,
                self.assertRaisesRegex(RuntimeError, reason),
            ):
                native.publish()
            write.assert_not_called()

    def test_autostart_entry_runs_only_publisher_not_a_second_desktop(self):
        self.assertIn("desktop-native-session.py publish", native.AUTOSTART)
        self.assertIn("NoDisplay=true", native.AUTOSTART)
        self.assertNotIn("X-GNOME-Autostart-Phase", native.AUTOSTART)
        self.assertNotIn("gnome-session", native.AUTOSTART)
        self.assertNotIn("startplasma", native.AUTOSTART)

    def test_autostart_rejects_other_login_and_dead_owner(self):
        uid = os.getuid() or 1007
        owner = {
            "uid": uid,
            "token": "t" * 32,
            "session_id": "c7",
            "owner": {"pid": 123, "identity": "456"},
        }
        with (
            patch.dict(os.environ, {"XDG_SESSION_ID": "other"}, clear=True),
            patch.object(native.os, "getuid", return_value=uid),
            patch.object(native.os, "geteuid", return_value=uid),
            patch.object(
                native, "session_directory", return_value=Path("/run/user/1007/sentinel-desktop")
            ),
            patch.object(native, "read_json", return_value=owner),
            patch.object(native, "process_identity", return_value="456"),
            self.assertRaisesRegex(RuntimeError, "another login"),
        ):
            native.publish()


if __name__ == "__main__":
    unittest.main()
