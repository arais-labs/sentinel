import importlib.util
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

source = Path(__file__).resolve().parents[2] / "native/graphics/guest/desktop-session.py"
spec = importlib.util.spec_from_file_location("desktop_session", source)
session = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(source.parent), *sys.path]):
    spec.loader.exec_module(session)


class NativeContractTests(unittest.TestCase):
    def test_native_login_prepares_standard_x11_directory_before_dispatch(self):
        for protocol in ("x11", "wayland"):
            events = []
            config = {"protocol": protocol, "command": ["desktop"], "session_manager": "native"}
            keyboard = SimpleNamespace(
                resolve_keyboard=lambda *_: None,
                validate_keyboard=lambda *_: {},
                weston_keyboard=None,
                weston_effective_keyboard=None,
            )
            login = SimpleNamespace(serve=lambda *_: events.append("native-login"))
            with (
                patch.object(session, "configuration", return_value=config),
                patch.object(session, "own_descendants"),
                patch.object(
                    session,
                    "prepare_x11_directory",
                    side_effect=lambda: events.append("x11-directory"),
                ),
                patch.object(sys, "path", [str(source.parent), *sys.path]),
                patch.dict(
                    sys.modules,
                    {
                        session.__name__: session,
                        "desktop_keyboard": keyboard,
                        "desktop_login": login,
                    },
                ),
            ):
                session.serve({"geometry": "1280x800"}, 42)
            self.assertEqual(events, ["x11-directory", "native-login"])

    def test_old_normal_profiles_fail_before_runtime_writes_or_stop(self):
        for desktop in ("xfce", "lxqt", "gnome", "plasma"):
            for action in ("start", "status"):
                with (
                    self.subTest(desktop=desktop, action=action),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    config = root / "desktop.json"
                    config.write_text(
                        json.dumps({"protocol": "wayland", "command": ["legacy-session"]})
                    )
                    (root / "desktop-choice").write_text(desktop)
                    runtime = root / "not-created"
                    with (
                        patch.object(session, "CONFIG", config),
                        patch.object(session, "ROOT", runtime),
                        patch.object(session, "stop") as stop,
                        patch.object(session.subprocess, "Popen") as spawn,
                        self.assertRaisesRegex(RuntimeError, "Reinstall.*erases VM-only"),
                    ):
                        session.main({"action": action})
                    self.assertFalse(runtime.exists())
                    stop.assert_not_called()
                    spawn.assert_not_called()

    def test_only_explicit_internal_weston_can_use_manual_wayland(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "desktop.json"
            value = {"protocol": "wayland", "command": ["weston"]}
            config.write_text(json.dumps(value))
            with patch.object(session, "CONFIG", config):
                with self.assertRaisesRegex(RuntimeError, "Reinstall"):
                    session.configuration()
                (root / "desktop-choice").write_text("weston")
                self.assertEqual(session.configuration(), value)
                config.write_text(json.dumps({**value, "protocol": "x11"}))
                with self.assertRaisesRegex(ValueError, "requires Wayland"):
                    session.configuration()

    def test_native_custom_commands_pass_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "desktop.json"
            value = {
                "protocol": "wayland",
                "session_manager": "native",
                "clipboard_backend": "x11",
                "command": ["custom-session"],
            }
            config.write_text(json.dumps(value))
            (root / "desktop-choice").write_text("lxqt")
            with patch.object(session, "CONFIG", config):
                self.assertEqual(session.configuration(), value)


class DesktopTimingTests(unittest.TestCase):
    def test_internal_weston_timing_is_120_hz(self):
        for geometry in session.RESOLUTIONS:
            with self.subTest(geometry=geometry):
                timing = session.modeline(geometry).split()
                refresh = float(timing[0]) * 1_000_000 / (int(timing[4]) * int(timing[8]))
                self.assertAlmostEqual(refresh, 120, places=2)


class SessionBusTests(unittest.TestCase):
    def test_regular_user_bus_address_is_shared_after_readiness(self):
        environment = {}
        spawn = MagicMock()
        address = "unix:path=/run/user/1001/sentinel-bus,guid=0123"
        with (
            patch.object(session.os, "pipe", return_value=(10, 11)),
            patch.object(session.os, "close") as close,
            patch.object(session, "ready_line", return_value=address) as ready,
        ):
            result = session.start_session_bus(spawn, Path("/run/user/1001"), environment)
        self.assertIs(result, spawn.return_value)
        spawn.assert_called_once_with(
            [
                "dbus-daemon",
                "--session",
                "--nofork",
                "--nopidfile",
                "--address=unix:path=/run/user/1001/sentinel-bus",
                "--print-address=11",
            ],
            pass_fds=(11,),
        )
        ready.assert_called_once_with(10, spawn.return_value)
        self.assertEqual(environment["DBUS_SESSION_BUS_ADDRESS"], address)
        self.assertEqual([call.args[0] for call in close.call_args_list], [11, 10])

    def test_bus_start_failure_releases_readiness_descriptors(self):
        environment = {}
        with (
            patch.object(session.os, "pipe", return_value=(10, 11)),
            patch.object(session.os, "close") as close,
            patch.object(session, "ready_line", return_value="unix:path=/other/bus"),
            self.assertRaisesRegex(RuntimeError, "unexpected address"),
        ):
            session.start_session_bus(MagicMock(), Path("/run/user/1001"), environment)
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", environment)
        self.assertEqual([call.args[0] for call in close.call_args_list], [11, 10])

    def test_bus_exit_fails_session_without_polling(self):
        desktop, bus = MagicMock(pid=123), MagicMock(pid=456)
        with (
            patch.object(session.os, "pidfd_open", side_effect=[10, 11], create=True),
            patch.object(session.os, "close") as close,
            patch.object(session.select, "select", return_value=([11], [], [])) as wait,
            self.assertRaisesRegex(RuntimeError, "session bus exited"),
        ):
            session.wait_session(desktop, bus)
        wait.assert_called_once_with([10, 11], [], [])
        self.assertEqual([call.args[0] for call in close.call_args_list], [10, 11])

    def test_normal_session_exit_is_reaped(self):
        desktop, bus = MagicMock(pid=123), MagicMock(pid=456)
        with (
            patch.object(session.os, "pidfd_open", side_effect=[10, 11], create=True),
            patch.object(session.os, "close"),
            patch.object(session.select, "select", return_value=([10], [], [])),
        ):
            session.wait_session(desktop, bus)
        desktop.wait.assert_called_once_with()


class WaylandEnvironmentTests(unittest.TestCase):
    def connection(self, capabilities=7, delayed=False):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.getsockopt.return_value = struct.pack("3i", 42, 0, 0)
        messages = []

        def event(object_id, body):
            messages.extend([struct.pack("=II", object_id, (8 + len(body)) << 16), body])

        event(2, struct.pack("=II", 17, 8) + b"wl_seat\0" + struct.pack("=I", 7))
        event(3, b"\0" * 4)
        event(4, struct.pack("=I", capabilities))
        event(5, b"\0" * 4)
        if delayed:
            event(4, struct.pack("=I", 7))
        connection.recv.side_effect = messages
        return connection

    def test_x11_directory_is_sticky_and_reusable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".X11-unix"
            session.prepare_x11_directory(path)
            session.prepare_x11_directory(path)
            self.assertEqual(path.stat().st_mode & 0o7777, 0o1777)

    def test_x11_directory_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir(mode=0o700)
            path = root / ".X11-unix"
            path.symlink_to(target, target_is_directory=True)
            with self.assertRaises(OSError):
                session.prepare_x11_directory(path)
            self.assertEqual(target.stat().st_mode & 0o7777, 0o700)

    def test_only_peer_owned_listening_x11_socket_is_published(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / "42/fd").mkdir(parents=True)
            (proc / "net").mkdir()
            (proc / "42/fd/4").symlink_to("socket:[200]")
            (proc / "42/fd/5").symlink_to("socket:[201]")
            (proc / "net/unix").write_text(
                "Num RefCount Protocol Flags Type St Inode Path\n"
                "0: 2 0 00010000 0001 01 100 /tmp/.X11-unix/X0\n"
                "0: 2 0 00010000 0001 01 200 /tmp/.X11-unix/X6\n"
                "0: 2 0 00000000 0001 01 201 /tmp/.X11-unix/X9\n"
            )
            self.assertEqual(session.compositor_xdisplay(42, proc), ":6")
            (proc / "42/fd/4").unlink()
            self.assertIsNone(session.compositor_xdisplay(42, proc))

    def test_roundtrip_precedes_discovery_and_native_only_is_valid(self):
        connection = self.connection()
        with (
            patch.object(session.socket, "socket", return_value=connection),
            patch.object(session.socket, "SO_PEERCRED", 17, create=True),
            patch.object(session, "compositor_xdisplay", return_value=None) as discovery,
            patch.object(session, "initialize_pointer") as prime,
        ):
            self.assertIsNone(session.wayland_ready("/run/user/0/wayland-0"))
        self.assertEqual(connection.sendall.call_count, 4)
        prime.assert_not_called()
        discovery.assert_called_once_with(42)

    def test_roundtrip_timeout_does_not_publish_a_guess(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.getsockopt.return_value = struct.pack("3i", 42, 0, 0)
        connection.recv.side_effect = TimeoutError("bounded")
        with (
            patch.object(session.socket, "socket", return_value=connection),
            patch.object(session.socket, "SO_PEERCRED", 17, create=True),
            patch.object(session, "compositor_xdisplay") as discovery,
            self.assertRaises(TimeoutError),
        ):
            session.wayland_ready("/run/user/0/wayland-0")
        discovery.assert_not_called()

    def test_explicit_x11_environment_does_not_need_discovery(self):
        connection = self.connection()
        with (
            patch.object(session.socket, "socket", return_value=connection),
            patch.object(session.socket, "SO_PEERCRED", 17, create=True),
            patch.object(session, "compositor_xdisplay") as discovery,
        ):
            self.assertIsNone(session.wayland_ready("/run/user/0/wayland-0", False))
        discovery.assert_not_called()

    def test_lazy_pointer_is_primed_once_then_capability_event_acknowledged(self):
        connection = self.connection(capabilities=6, delayed=True)
        with (
            patch.object(session.socket, "socket", return_value=connection),
            patch.object(session.socket, "SO_PEERCRED", 17, create=True),
            patch.object(session, "initialize_pointer") as prime,
            patch.object(session, "compositor_xdisplay", return_value=":0") as discovery,
        ):
            self.assertEqual(session.wayland_ready("/run/user/0/wayland-0"), ":0")
        prime.assert_called_once()
        self.assertGreater(prime.call_args.args[0], 0)
        self.assertLessEqual(prime.call_args.args[0], 8)
        self.assertEqual(connection.recv.call_count, 10)
        discovery.assert_called_once_with(42)

    def test_priming_without_capability_does_not_publish_ready(self):
        connection = self.connection(capabilities=6)
        initial = list(connection.recv.side_effect)
        connection.recv.side_effect = [*initial, TimeoutError("pointer unavailable")]
        with (
            patch.object(session.socket, "socket", return_value=connection),
            patch.object(session.socket, "SO_PEERCRED", 17, create=True),
            patch.object(session, "initialize_pointer") as prime,
            patch.object(session, "compositor_xdisplay") as discovery,
            self.assertRaisesRegex(TimeoutError, "pointer unavailable"),
        ):
            session.wayland_ready("/run/user/0/wayland-0")
        prime.assert_called_once()
        discovery.assert_not_called()


class DesktopShutdownTests(unittest.TestCase):
    def test_native_owner_has_bounded_time_to_await_graphical_jobs_and_pam(self):
        with (
            patch.object(session, "alive", return_value=True),
            patch.object(session, "read_state", return_value={}),
            patch.object(session.os, "pidfd_open", return_value=55, create=True),
            patch.object(session.os, "close") as close,
            patch.object(session.signal, "pidfd_send_signal", create=True) as send,
            patch.object(session.select, "select", return_value=([55], [], [])) as wait,
            patch.object(session, "STATE"),
            patch.object(session, "ROOT"),
        ):
            session.stop({"owner": {"pid": 123}})
        wait.assert_called_once_with([55], [], [], 20)
        send.assert_called_once_with(55, session.signal.SIGTERM)
        close.assert_called_once_with(55)

    def test_missing_kernel_ownership_contract_fails_before_launch(self):
        path = MagicMock()
        path.is_file.return_value = False
        with (
            patch.object(session, "Path", return_value=path),
            patch.object(session.ctypes, "CDLL") as libc,
            self.assertRaisesRegex(RuntimeError, "lifecycle support is missing"),
        ):
            session.own_descendants()
        libc.assert_not_called()

    def test_surviving_descendant_is_failure_not_success(self):
        child_list = MagicMock()
        child_list.read_text.return_value = "123"
        with (
            patch.object(session, "Path", return_value=child_list),
            patch.object(session.os, "pidfd_open", return_value=55, create=True),
            patch.object(session.os, "close") as close,
            patch.object(session.select, "poll", create=True),
            patch.object(session.signal, "pidfd_send_signal", create=True) as send,
            self.assertRaisesRegex(RuntimeError, "descendants did not exit"),
        ):
            session.stop_children([], grace=0, kill_timeout=0)
        self.assertEqual(
            [call.args[1] for call in send.call_args_list],
            [session.signal.SIGTERM, session.signal.SIGKILL],
        )
        close.assert_called_once_with(55)

    def test_failed_owner_cleanup_does_not_return_stopped(self):
        with (
            patch.object(session, "alive", return_value=False),
            patch.object(session, "read_state", return_value={"cleanup_pending": True}),
            patch.object(session, "STATE") as state,
            self.assertRaisesRegex(RuntimeError, "cleanup did not finish"),
        ):
            session.stop({})
        state.unlink.assert_not_called()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux subreaper/pidfd contract")
    def test_adopted_session_escaping_children_release_lock_and_are_reaped(self):
        fixture = Path(__file__).resolve().parents[1] / "fixtures/desktop-lifecycle.py"
        result = subprocess.run(
            [sys.executable, str(fixture), str(source)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count('"lock_released": true'), 3)
