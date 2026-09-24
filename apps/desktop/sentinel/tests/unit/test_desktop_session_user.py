"""Exercise the shipped desktop supervisor without starting a VM or display."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

SOURCE = Path(__file__).resolve().parents[2] / "native/graphics/guest/desktop-session.py"
spec = importlib.util.spec_from_file_location("desktop_session_user_test", SOURCE)
session = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(SOURCE.parent), *sys.path]):
    spec.loader.exec_module(session)


class DesktopUserSessionTests(unittest.TestCase):
    def exercise(self, protocol, runtime_case=None):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            home = root / "home"
            home.mkdir()
            # The tests own all real files; no attempt is made to change UID.
            account = SimpleNamespace(
                pw_uid=os.geteuid(),
                pw_gid=os.getegid(),
                pw_name="sentinel",
                pw_dir=str(home),
            )
            run_user = root / "run-user"
            run_user.mkdir()
            runtime = run_user / str(account.pw_uid)
            if runtime_case == "symlink":
                runtime.symlink_to(home, target_is_directory=True)
            config = root / "desktop.json"
            (root / "desktop-choice").write_text("weston")
            config.write_text(
                json.dumps(
                    {
                        "protocol": protocol,
                        "command": ["test-desktop"],
                        "environment": {
                            "HOME": "/root",
                            "USER": "root",
                            "XDG_RUNTIME_DIR": "/run/user/0",
                        },
                    }
                )
            )
            keyboard = SimpleNamespace(
                resolve_keyboard=lambda *_: None,
                validate_keyboard=lambda *_: {},
                weston_keyboard=lambda contents, *_: contents,
                weston_effective_keyboard=lambda *_: None,
            )
            user = SimpleNamespace(identity=lambda: account, as_user=lambda _: nullcontext())
            stack.enter_context(
                patch.dict("sys.modules", {"desktop_keyboard": keyboard, "desktop_user": user})
            )
            stack.enter_context(patch.object(session, "ROOT", root))
            stack.enter_context(patch.object(session, "STATE", root / "state.json"))
            stack.enter_context(patch.object(session, "CONFIG", config))
            stack.enter_context(patch.object(session, "LOG", root / "desktop.log"))
            stack.enter_context(
                patch.object(
                    session,
                    "Path",
                    side_effect=lambda value: (
                        run_user if str(value) == "/run/user" else Path(value)
                    ),
                )
            )
            chown = stack.enter_context(patch.object(session.os, "fchown"))
            stack.enter_context(
                patch.object(session.os, "getgrouplist", return_value=[account.pw_gid, 44])
            )
            stack.enter_context(
                patch.object(
                    session.grp,
                    "getgrgid",
                    return_value=SimpleNamespace(gr_name="sentinel"),
                )
            )
            stack.enter_context(patch.object(session.signal, "signal"))
            for name in (
                "own_descendants",
                "prepare_x11_directory",
                "seed_preferences",
                "stop_children",
                "wait_socket",
                "initialize_pointer",
            ):
                stack.enter_context(patch.object(session, name))
            bus_address = "unix:path=" + str(runtime / "sentinel-bus")
            stack.enter_context(
                patch.object(session, "ready_line", side_effect=["2", bus_address + ",guid=test"])
            )
            lifetime = stack.enter_context(patch.object(session, "wait_session"))
            stack.enter_context(patch.object(session, "wayland_ready", return_value=":2"))
            stack.enter_context(patch.object(session, "healthy", return_value=True))
            stack.enter_context(
                patch.object(
                    session,
                    "record",
                    side_effect=lambda pid: {"pid": pid, "identity": "test"},
                )
            )
            published = []
            stack.enter_context(
                patch.object(
                    session,
                    "atomic_json",
                    side_effect=lambda path, value: published.append(
                        (path, json.loads(json.dumps(value)))
                    ),
                )
            )
            spawn = stack.enter_context(
                patch.object(
                    session.subprocess,
                    "Popen",
                    side_effect=lambda *_args, **_kwargs: MagicMock(pid=4242),
                )
            )
            run = stack.enter_context(patch.object(session.subprocess, "run"))
            if runtime_case == "owner":
                stack.enter_context(
                    patch.object(
                        session.os,
                        "fstat",
                        return_value=SimpleNamespace(st_uid=account.pw_uid + 10000),
                    )
                )
            read_fd, write_fd = os.pipe()
            try:
                if runtime_case:
                    with self.assertRaises((OSError, RuntimeError)):
                        session.serve({"geometry": "1280x800"}, write_fd)
                    chown.assert_not_called()
                    spawn.assert_not_called()
                    os.close(write_fd)
                    return
                session.serve({"geometry": "1280x800"}, write_fd)
                self.assertEqual(os.read(read_fd, 20), b"ready\n")
            finally:
                os.close(read_fd)
            self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
            chown.assert_called_once_with(unittest.mock.ANY, account.pw_uid, account.pw_gid)
            desktop = next(value for path, value in published if path.name == "session.json")
            self.assertEqual(desktop["user"]["uid"], account.pw_uid)
            self.assertEqual(desktop["environment"]["HOME"], str(home))
            self.assertEqual(desktop["environment"]["USER"], "sentinel")
            self.assertEqual(desktop["environment"]["XDG_RUNTIME_DIR"], str(runtime))
            self.assertEqual(
                desktop["environment"]["DBUS_SESSION_BUS_ADDRESS"],
                bus_address + ",guid=test",
            )
            commands = {}
            for call in spawn.call_args_list:
                binary = call.args[0][0]
                commands[binary] = call
                if binary in {"Xorg", "seatd", "python3"}:
                    self.assertNotIn("user", call.kwargs)
                else:
                    self.assertEqual(call.kwargs["user"], account.pw_uid)
                    self.assertEqual(call.kwargs["group"], account.pw_gid)
                    self.assertEqual(call.kwargs["extra_groups"], [account.pw_gid, 44])
                self.assertEqual(call.kwargs["cwd"], str(home))
            self.assertIn("test-desktop", commands)
            self.assertIn("dbus-daemon", commands)
            self.assertIn("--nofork", commands["dbus-daemon"].args[0])
            self.assertIn("--address=" + bus_address, commands["dbus-daemon"].args[0])
            self.assertEqual(
                commands["test-desktop"].kwargs["env"]["DBUS_SESSION_BUS_ADDRESS"],
                desktop["environment"]["DBUS_SESSION_BUS_ADDRESS"],
            )
            lifetime.assert_called_once()
            self.assertIn("pulseaudio", commands)
            self.assertNotIn("--system", commands["pulseaudio"].args[0])
            if protocol == "x11":
                self.assertIn("-auth", commands["Xorg"].args[0])
                self.assertNotIn("-ac", commands["Xorg"].args[0])
                for call in run.call_args_list:
                    self.assertEqual(call.args[0], ["xauth", "-q"])
                    self.assertEqual(call.kwargs["user"], account.pw_uid)
            else:
                self.assertEqual(commands["seatd"].args[0][1:3], ["-g", "sentinel"])

    def test_wayland_session_and_audio_regular_seat_broker_privileged(self):
        self.exercise("wayland")

    def test_runtime_symlink_rejected(self):
        self.exercise("wayland", "symlink")

    def test_runtime_other_owner_rejected(self):
        self.exercise("wayland", "owner")
