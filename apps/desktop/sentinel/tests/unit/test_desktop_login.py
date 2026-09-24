import importlib.util
import io
import json
import os
import runpy
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

source = Path(__file__).resolve().parents[2] / "native/graphics/guest/desktop_login.py"
sys.path.insert(0, str(source.parent))
try:
    spec = importlib.util.spec_from_file_location("desktop_login_test", source)
    login = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(login)
finally:
    sys.path.pop(0)


class DesktopLoginTests(unittest.TestCase):
    def test_audio_uses_existing_native_server_without_starting_daemon(self):
        responses = iter(
            ["Server Name: PulseAudio (on PipeWire)", '[{"name":"existing"}]', "existing\n"]
        )
        credentials = {"user": 1007, "group": 1007, "extra_groups": [1007, 44]}
        with patch.object(
            login.subprocess,
            "run",
            side_effect=lambda *args, **kwargs: SimpleNamespace(stdout=next(responses)),
        ) as run:
            login.prepare_audio(
                {
                    "XDG_RUNTIME_DIR": "/run/user/1007",
                    "PULSE_SERVER": "unix:/run/user/1007/pulse/native",
                },
                credentials,
            )
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["pactl", "info"],
                ["pactl", "--format=json", "list", "sinks"],
                ["pactl", "get-default-sink"],
            ],
        )
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["user"], 1007)
            self.assertEqual(call.kwargs["extra_groups"], [1007, 44])

    def test_audio_creates_only_missing_playback_sink_in_same_server(self):
        responses = iter(["Server Name: native", "[]", "17", '[{"name":"sentinel"}]', "sentinel\n"])
        with patch.object(
            login.subprocess,
            "run",
            side_effect=lambda *args, **kwargs: SimpleNamespace(stdout=next(responses)),
        ) as run:
            login.prepare_audio({"PULSE_SERVER": "unix:/run/user/1007/pulse/native"}, {})
        self.assertEqual(
            run.call_args_list[2].args[0],
            [
                "pactl",
                "load-module",
                "module-null-sink",
                "sink_name=sentinel",
                "rate=48000",
                "channels=2",
            ],
        )
        self.assertTrue(all(call.args[0][0] == "pactl" for call in run.call_args_list))

    def test_audio_waits_for_native_default_after_one_sink_creation(self):
        responses = iter(
            [
                "Server Name: native",
                "[]",
                "17",
                "[]",
                "sentinel\n",
                '[{"name":"sentinel"}]',
                "sentinel\n",
            ]
        )
        with (
            patch.object(
                login.subprocess,
                "run",
                side_effect=lambda *args, **kwargs: SimpleNamespace(stdout=next(responses)),
            ) as run,
            patch.object(login.time, "sleep") as sleep,
        ):
            login.prepare_audio({"PULSE_SERVER": "unix:/run/user/1007/pulse/native"}, {})
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(sum("load-module" in command for command in commands), 1)
        self.assertFalse(any("set-default-sink" in command for command in commands))
        sleep.assert_called_once()

    def test_audio_missing_default_has_bounded_observation_without_reload(self):
        responses = iter(
            ["Server Name: native", '[{"name":"existing"}]', "\n", '[{"name":"existing"}]', "\n"]
        )
        with (
            patch.object(
                login.subprocess,
                "run",
                side_effect=lambda *args, **kwargs: SimpleNamespace(stdout=next(responses)),
            ) as run,
            patch.object(login.time, "monotonic", side_effect=[0, 1, 6]),
            patch.object(login.time, "sleep"),
            self.assertRaisesRegex(RuntimeError, "no ready default playback output"),
        ):
            login.prepare_audio({"PULSE_SERVER": "unix:/run/user/1007/pulse/native"}, {})
        self.assertFalse(any("load-module" in call.args[0] for call in run.call_args_list))

    def test_audio_child_receives_only_authorized_fd_and_normal_user_credentials(self):
        account = SimpleNamespace(
            pw_uid=1007, pw_gid=1007, pw_name="sentinel", pw_dir="/home/sentinel"
        )
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.fileno.return_value = 71
        child = Mock()
        child.stdout.fileno.return_value = 72
        lifecycle = SimpleNamespace(
            ROOT=Path("/run/sentinel-desktop"), ready_line=Mock(return_value='{"event":"ready"}')
        )
        children, environment = [], {
            "HOME": account.pw_dir,
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=native",
        }
        with (
            patch.object(login, "prepare_audio", return_value=environment),
            patch.object(
                login.runpy, "run_path", return_value={"connect": Mock(return_value=connection)}
            ),
            patch.object(login.subprocess, "Popen", return_value=child) as spawn,
        ):
            self.assertIs(
                login.start_audio(
                    environment, account, [1007, 44], children, io.BytesIO(), lifecycle
                ),
                child,
            )
        self.assertEqual(children, [child])
        self.assertEqual(spawn.call_args.kwargs["user"], 1007)
        self.assertEqual(spawn.call_args.kwargs["extra_groups"], [1007, 44])
        self.assertEqual(spawn.call_args.kwargs["pass_fds"], (71,))
        self.assertEqual(spawn.call_args.kwargs["env"], environment)
        self.assertEqual(
            spawn.call_args.args[0][-4:], ["--socket-fd", "71", "--source", "@DEFAULT_MONITOR@"]
        )
        child.stdout.close.assert_called_once()

    def test_native_audio_waits_for_owned_socket_and_disables_libpulse_autospawn(self):
        environment = {"XDG_RUNTIME_DIR": "/run/user/1007"}
        credentials = {"user": 1007, "group": 1007, "extra_groups": [1007]}
        responses = iter(["native server", '[{"name":"existing"}]', "existing\n"])
        with (
            patch.object(
                login.Path, "lstat", return_value=SimpleNamespace(st_mode=0o140600, st_uid=1007)
            ),
            patch.object(
                login.subprocess,
                "run",
                side_effect=lambda *args, **kwargs: SimpleNamespace(stdout=next(responses)),
            ) as run,
        ):
            result = login.prepare_audio(environment, credentials)
        self.assertNotIn("PULSE_SERVER", environment)
        self.assertEqual(result["PULSE_SERVER"], "unix:/run/user/1007/pulse/native")
        self.assertTrue(
            all(
                call.kwargs["env"]["PULSE_SERVER"] == result["PULSE_SERVER"]
                for call in run.call_args_list
            )
        )
        with (
            patch.object(
                login.Path, "lstat", return_value=SimpleNamespace(st_mode=0o140600, st_uid=0)
            ),
            patch.object(login.subprocess, "run") as run,
            self.assertRaisesRegex(RuntimeError, "owned"),
        ):
            login.prepare_audio(environment, credentials)
        run.assert_not_called()

    def test_audio_failure_is_nonfatal_without_retry_or_duplicate_server(self):
        account = SimpleNamespace(pw_uid=1007, pw_gid=1007)
        log = io.BytesIO()
        with (
            patch.object(
                login, "prepare_audio", side_effect=RuntimeError("native server unavailable")
            ),
            patch.object(login.subprocess, "Popen") as spawn,
        ):
            self.assertIsNone(login.start_audio({}, account, [], [], log, None))
        spawn.assert_not_called()
        self.assertIn(b"Desktop sound unavailable", log.getvalue())
        with (
            patch.object(login, "prepare_audio", side_effect=InterruptedError),
            self.assertRaises(InterruptedError),
        ):
            login.start_audio({}, account, [], [], log, None)

    def test_audio_exit_does_not_stop_healthy_login(self):
        manager, audio, log = SimpleNamespace(pid=41), Mock(pid=42), io.BytesIO()
        audio.wait.return_value = 1
        with (
            patch.object(login.os, "pidfd_open", side_effect=[100, 101, 102], create=True),
            patch.object(login.os, "close") as close,
            patch.object(
                login.select, "select", side_effect=[([102], [], []), ([100], [], [])]
            ) as wait,
        ):
            login.wait_login(40, manager, audio, log)
        self.assertEqual(wait.call_count, 2)
        self.assertEqual(sorted(call.args[0] for call in close.call_args_list), [100, 101, 102])
        self.assertIn(b"desktop remains available", log.getvalue())

    def ready(self):
        return {
            "uid": 1007,
            "token": "owned",
            "owner": {"pid": 41, "identity": "123"},
            "session_id": "c7",
            "protocol": "wayland",
            "environment": {
                "XDG_SESSION_ID": "c7",
                "XDG_RUNTIME_DIR": "/run/user/1007",
                "WAYLAND_DISPLAY": "wayland-2",
            },
        }

    def test_endpoint_requires_kernel_process_uid_not_user_claim(self):
        value = self.ready()
        with patch.object(
            login.Path, "read_text", return_value="Name:\tapp\nUid:\t1007\t0\t1007\t1007\n"
        ):
            with self.assertRaisesRegex(RuntimeError, "process is not owned"):
                login.validate_ready(value, 1007, "owned", lambda _: True)
        with patch.object(login.Path, "read_text", return_value="Uid:\t1007\t1007\t1007\t1007\n"):
            self.assertEqual(login.validate_ready(value, 1007, "owned", lambda _: True), "c7")

    def test_endpoint_allows_compositor_to_omit_environment_login_id(self):
        value = self.ready()
        value["environment"].pop("XDG_SESSION_ID")
        with patch.object(login, "process_uid") as check_uid:
            self.assertEqual(login.validate_ready(value, 1007, "owned", lambda _: True), "c7")
        check_uid.assert_called_once_with(41, 1007)
        self.assertNotIn("XDG_SESSION_ID", value["environment"])
        with (
            patch.object(login, "process_uid"),
            self.assertRaisesRegex(RuntimeError, "does not belong"),
        ):
            login.validate_ready(value, 1007, "owned", lambda _: False)

    def test_endpoint_rejects_invalid_pid_token_runtime_and_environment(self):
        for changes in (
            {"owner": {"pid": "../../file"}},
            {"owner": {"pid": True}},
            {"uid": 0},
            {"token": "different"},
            {"session_id": "../other"},
            {"environment": {"XDG_RUNTIME_DIR": "/root"}},
            {"environment": {"XDG_RUNTIME_DIR": "/run/user/1007", "XDG_SESSION_ID": "c8"}},
            {"environment": {"BAD=KEY": "value"}},
        ):
            with (
                self.subTest(changes=changes),
                patch.object(login, "process_uid"),
                self.assertRaises(RuntimeError),
            ):
                login.validate_ready({**self.ready(), **changes}, 1007, "owned", lambda _: True)

    def test_login_requires_registered_active_owned_graphical_seat(self):
        valid = {"User": "1007", "Active": "yes", "Seat": "seat0", "Type": "wayland", "VTNr": "7"}
        login.require_graphical_login(valid, 1007, "wayland")
        for field, value in (
            ("User", "0"),
            ("Active", "no"),
            ("Seat", ""),
            ("Type", "tty"),
            ("VTNr", "2"),
        ):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                login.require_graphical_login({**valid, field: value}, 1007, "wayland")

    def test_owner_must_descend_from_pam_leader_and_this_greetd(self):
        with patch.object(login, "is_descendant", side_effect=[True, True]) as descendant:
            login.require_login_owner({"Leader": "40"}, {"pid": 41}, 39)
        self.assertEqual([call.args for call in descendant.call_args_list], [(41, 40), (40, 39)])
        for results in ([False], [True, False]):
            with (
                patch.object(login, "is_descendant", side_effect=results),
                self.assertRaisesRegex(RuntimeError, "this greetd"),
            ):
                login.require_login_owner({"Leader": "40"}, {"pid": 41}, 39)

    def test_ancestry_handles_process_names_with_parentheses(self):
        def read(path):
            pid = int(path.parts[-2])
            parent = {41: 40, 40: 39, 39: 1}[pid]
            return f"{pid} (name with ) parens) S {parent} 0 0"

        with patch.object(login.Path, "read_text", read):
            self.assertTrue(login.is_descendant(41, 39))
            self.assertFalse(login.is_descendant(41, 100))

    def test_configuration_uses_real_pam_service_and_quotes_command_arguments(self):
        command = ["/usr/bin/python3", "/a path/runner.py", "a'quoted;argument"]
        log_path = Path("/run/a path/'quoted.log")
        config = tomllib.loads(
            login.greetd_config(command, "sentinel", Path("/run/a.run"), "wayland", log_path)
        )
        self.assertEqual(config["terminal"], {"vt": 7, "switch": True})
        self.assertEqual(config["general"]["service"], "sentinel-wayland")
        self.assertFalse(config["general"]["source_profile"])
        wrapped = shlex.split(config["initial_session"]["command"])
        self.assertEqual(
            wrapped,
            [
                "/usr/bin/python3",
                str(source.with_name("desktop-session-log.py")),
                str(log_path),
                *command,
            ],
        )
        self.assertEqual(config["initial_session"]["user"], "sentinel")
        with self.assertRaises(ValueError):
            login.greetd_config(command, "sentinel", Path("run"), "other", log_path)

    def test_private_session_log_is_bounded_and_rejects_symlink_and_wrong_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.log"
            path.write_bytes(b"old" * 40000 + b"latest error")
            path.chmod(0o600)
            log = io.BytesIO()
            login.append_session_log(path, os.getuid(), log)
            self.assertTrue(log.getvalue().endswith(b"latest error\n"))
            self.assertLess(len(log.getvalue()), 65650)
            log = io.BytesIO()
            login.append_session_log(path, os.getuid() + 1, log)
            self.assertNotIn(b"latest error", log.getvalue())
            self.assertIn(b"not a private regular user file", log.getvalue())
            link = Path(directory) / "link.log"
            link.symlink_to(path)
            log = io.BytesIO()
            login.append_session_log(link, os.getuid(), log)
            self.assertNotIn(b"latest error", log.getvalue())
            self.assertIn(b"log unavailable", log.getvalue())

    def test_session_command_log_redirection_quotes_paths_and_has_private_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a 'quoted; logfile"
            command = [
                sys.executable,
                "-c",
                "import sys; print(sys.argv[1]); print('error',file=sys.stderr)",
                "a'quoted;argument",
            ]
            config = tomllib.loads(
                login.greetd_config(command, "sentinel", Path("run"), "x11", path)
            )
            # Match greetd's actual source_profile=false execution contract.
            subprocess.run(
                ["/bin/sh", "-c", "exec " + config["initial_session"]["command"]], check=True
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(set(path.read_text().splitlines()), {"a'quoted;argument", "error"})

    def test_startup_journal_is_uid_filtered_bounded_and_optional(self):
        log = io.BytesIO()
        with (
            patch.object(login.Path, "is_dir", return_value=True),
            patch.object(login.shutil, "which", return_value="/usr/bin/journalctl"),
            patch.object(
                login.subprocess,
                "run",
                return_value=SimpleNamespace(stdout=b"a" * 100000 + b"last"),
            ) as run,
        ):
            login.append_user_journal(1007, log)
        self.assertIn("_UID=1007", run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["timeout"], 3)
        self.assertTrue(log.getvalue().endswith(b"last\n"))
        self.assertLess(len(log.getvalue()), 65700)
        with (
            patch.object(login.Path, "is_dir", return_value=False),
            patch.object(login.subprocess, "run") as run,
        ):
            login.append_user_journal(1007, io.BytesIO())
        run.assert_not_called()

    def test_session_log_flood_is_bounded_and_preserves_latest_output(self):
        logger = runpy.run_path(str(source.with_name("desktop-session-log.py")))
        output = io.BytesIO()
        for _ in range(40):
            logger["append"](output, b"x" * 65536)
            self.assertLessEqual(len(output.getvalue()), logger["LIMIT"])
        logger["append"](output, b"last error\n")
        self.assertTrue(output.getvalue().endswith(b"last error\n"))

    def test_session_log_write_failure_does_not_block_child_or_lose_exit_status(self):
        logger = runpy.run_path(str(source.with_name("desktop-session-log.py")))
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(logger["run"].__globals__, append=Mock(side_effect=OSError("disk full"))),
        ):
            result = logger["run"](
                str(Path(directory) / "session.log"),
                [
                    sys.executable,
                    "-c",
                    "import os; os.write(1, b'x' * 2000000); raise SystemExit(7)",
                ],
            )
        self.assertEqual(result, 7)

    def test_display_configuration_returns_actual_mode_and_runs_as_session_user(self):
        account = SimpleNamespace(pw_uid=1007, pw_gid=1007, pw_name="sentinel")
        actual = {"width": 1280, "height": 800, "refresh_hz": 60, "geometry_met": False}
        environment = {"WAYLAND_DISPLAY": "wayland-2"}
        with (
            patch.object(login.Path, "read_text", return_value="gnome\n"),
            patch.object(login.os, "getgrouplist", return_value=[1007, 44]),
            patch.object(login, "configure_output", return_value=actual) as configure,
        ):
            result, groups = login.configure_display("1920x1200", environment, account)
        self.assertEqual(result, actual)
        self.assertEqual(groups, [1007, 44])
        configure.assert_called_once_with(
            "gnome",
            "1920x1200",
            environment,
            {"user": 1007, "group": 1007, "extra_groups": [1007, 44]},
        )

    def test_failed_logind_termination_still_reaps_greetd(self):
        children, stop = [object()], Mock()
        with (
            patch.object(login, "stop_graphical_session"),
            patch.object(
                login.subprocess, "run", side_effect=subprocess.TimeoutExpired("loginctl", 3)
            ),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            login.stop_login("c7", children, stop, io.BytesIO(), object())
        stop.assert_called_once_with(children)

    def test_unvalidated_session_is_never_terminated(self):
        stop = Mock()
        with (
            patch.object(login.subprocess, "run") as run,
            patch.object(login, "stop_graphical_session") as graphical,
        ):
            login.stop_login(None, [], stop, io.BytesIO(), object())
        run.assert_not_called()
        graphical.assert_not_called()
        stop.assert_called_once_with([])

    def test_native_shutdown_waits_for_declared_graphical_units_only(self):
        account = SimpleNamespace(
            pw_uid=1007, pw_gid=1007, pw_name="sentinel", pw_dir="/home/sentinel"
        )
        units = {
            r"app-org.example\x2dtool@autostart.service",
            "graphical-session.target",
            "plasma-kwin_wayland.service",
            "plasma-workspace-wayland.target",
            "plasma-workspace.target",
        }

        def snapshot(active):
            return "\n\n".join(
                f"Id={unit}\nActiveState={'deactivating' if unit == active else 'inactive'}\nJob={'19' if unit == active else '0'}"
                for unit in sorted(units)
            )

        responses = iter(
            [
                'Id=graphical-session.target\nConsistsOf=plasma-kwin_wayland.service "app-org.example\\\\x2dtool@autostart.service"\nBoundBy=plasma-workspace.target\n',
                "Id=plasma-kwin_wayland.service\nBoundBy=plasma-workspace-wayland.target\n\nId=plasma-workspace.target\n",
                "Id=plasma-workspace-wayland.target\nBoundBy=\nConsistsOf=\n",
                "",
                snapshot("plasma-kwin_wayland.service"),
                snapshot(None),
            ]
        )

        def information(path):
            return SimpleNamespace(
                st_uid=1007, st_mode=0o140600 if path.name == "private" else 0o40700
            )

        with (
            patch.object(login.Path, "is_dir", return_value=True),
            patch.object(login.Path, "lstat", information),
            patch.object(login.os, "getgrouplist", return_value=[1007, 44]),
            patch.object(login.time, "sleep") as sleep,
            patch.object(
                login.subprocess,
                "run",
                side_effect=lambda *args, **kwargs: SimpleNamespace(stdout=next(responses)),
            ) as run,
        ):
            login.stop_graphical_session(account, io.BytesIO())
        stops = [call for call in run.call_args_list if "stop" in call.args[0]]
        self.assertEqual(len(stops), 1)
        self.assertEqual(
            stops[0].args[0],
            ["systemctl", "--user", "--no-block", "stop", "graphical-session.target"],
        )
        self.assertEqual(
            run.call_args.args[0],
            [
                "systemctl",
                "--user",
                "show",
                "--all",
                "--property=Id",
                "--property=ActiveState",
                "--property=Job",
                "--",
                *sorted(units),
            ],
        )
        sleep.assert_called_once()
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["user"], 1007)
            self.assertEqual(call.kwargs["extra_groups"], [1007, 44])
            self.assertEqual(call.kwargs["env"]["XDG_RUNTIME_DIR"], "/run/user/1007")
            self.assertNotIn("LD_PRELOAD", call.kwargs["env"])
            self.assertTrue(call.kwargs["check"])

    def test_native_shutdown_observes_dependency_only_units_and_pending_jobs(self):
        units = {"graphical-session.target", "org.gnome.Shell@user.service"}
        self.assertEqual(
            login.graphical_shutdown_pending(
                "Id=graphical-session.target\nActiveState=inactive\nJob=0\n\n"
                "Id=org.gnome.Shell@user.service\nActiveState=deactivating\nJob=7\nRefuseManualStop=yes\n",
                units,
            ),
            {"org.gnome.Shell@user.service": {"state": "deactivating", "job": "7"}},
        )
        # A queued job is not completed cleanup, even after the process exited.
        self.assertEqual(
            login.graphical_shutdown_pending(
                "Id=graphical-session.target\nActiveState=inactive\nJob=0\n\n"
                "Id=org.gnome.Shell@user.service\nActiveState=failed\nJob=7\n",
                units,
            ),
            {"org.gnome.Shell@user.service": {"state": "failed", "job": "7"}},
        )
        self.assertEqual(
            login.graphical_shutdown_pending(
                "Id=graphical-session.target\nActiveState=inactive\nJob=0\n\n"
                "Id=org.gnome.Shell@user.service\nActiveState=failed\nJob=\n",
                units,
            ),
            {},
        )

    def test_native_shutdown_drains_reactivated_dbus_services_after_clients_exit(self):
        account = SimpleNamespace(
            pw_uid=1007, pw_gid=1007, pw_name="sentinel", pw_dir="/home/sentinel"
        )
        client, portal = "org.example.Desktop.service", "xdg-desktop-portal.service"
        snapshots = [
            f"Id=graphical-session.target\nActiveState=inactive\nJob=0\n\n"
            f"Id={client}\nActiveState={client_state}\nJob={client_job}\n\n"
            f"Id={portal}\nActiveState={portal_state}\nJob={portal_job}\n"
            for client_state, client_job, portal_state, portal_job in (
                ("deactivating", "12", "activating", "13"),
                ("inactive", "0", "activating", "13"),
                ("inactive", "0", "deactivating", "14"),
                ("inactive", "0", "inactive", "0"),
            )
        ]
        responses = iter(
            [
                f"Id=graphical-session.target\nConsistsOf={client} {portal}\n",
                f"Id={client}\nType=dbus\nRefuseManualStop=yes\n\n"
                f"Id={portal}\nType=dbus\nRefuseManualStop=no\n",
                "",
                snapshots[0],
                snapshots[1],
                "",
                snapshots[2],
                snapshots[3],
            ]
        )

        def information(path):
            return SimpleNamespace(
                st_uid=1007, st_mode=0o140600 if path.name == "private" else 0o40700
            )

        with (
            patch.object(login.Path, "is_dir", return_value=True),
            patch.object(login.Path, "lstat", information),
            patch.object(login.os, "getgrouplist", return_value=[1007]),
            patch.object(login.time, "sleep"),
            patch.object(
                login.subprocess,
                "run",
                side_effect=lambda *args, **kwargs: SimpleNamespace(stdout=next(responses)),
            ) as run,
        ):
            login.stop_graphical_session(account, io.BytesIO())
        calls = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            calls[2], ["systemctl", "--user", "--no-block", "stop", "graphical-session.target"]
        )
        self.assertTrue(all("--all" in calls[index] for index in (3, 4, 6, 7)))
        self.assertEqual(calls[5], ["systemctl", "--user", "--no-block", "stop", "--", portal])
        self.assertEqual(len(calls), 8)

    def test_native_shutdown_rejects_incomplete_or_unrelated_state_snapshots(self):
        for output in (
            "",
            "Id=owned.service\nActiveState=inactive\n",
            "Id=owned.service\nJob=0\n",
            "Id=other.service\nActiveState=inactive\nJob=0\n",
            "Id=owned.service\nActiveState=inactive\nJob=0\n\nId=owned.service\nActiveState=inactive\nJob=0\n",
        ):
            with self.subTest(output=output), self.assertRaises(RuntimeError):
                login.graphical_shutdown_pending(output, {"owned.service"})

    def test_native_shutdown_bounds_dependency_cycles_and_observed_state_wait(self):
        account = SimpleNamespace(
            pw_uid=1007, pw_gid=1007, pw_name="sentinel", pw_dir="/home/sentinel"
        )
        clock = [0]
        calls = []

        def execute(arguments, **kwargs):
            calls.append(arguments)
            self.assertLessEqual(kwargs["timeout"], 10)
            if "--all" in arguments:
                clock[0] = 11
                return SimpleNamespace(
                    stdout="Id=graphical-session.target\nActiveState=inactive\nJob=0\n\n"
                    "Id=org.gnome.Shell@user.service\nActiveState=deactivating\nJob=7\n"
                )
            if "stop" in arguments:
                return SimpleNamespace(stdout="")
            if arguments[-1] == "graphical-session.target":
                return SimpleNamespace(
                    stdout="Id=graphical-session.target\nConsistsOf=org.gnome.Shell@user.service\n"
                )
            return SimpleNamespace(
                stdout="Id=org.gnome.Shell@user.service\nBoundBy=graphical-session.target\n"
            )

        def information(path):
            return SimpleNamespace(
                st_uid=1007, st_mode=0o140600 if path.name == "private" else 0o40700
            )

        with (
            patch.object(login.Path, "is_dir", return_value=True),
            patch.object(login.Path, "lstat", information),
            patch.object(login.os, "getgrouplist", return_value=[1007]),
            patch.object(login.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(login.time, "sleep"),
            patch.object(login.subprocess, "run", side_effect=execute),
            self.assertRaisesRegex(RuntimeError, "org.gnome.Shell@user.service.*deactivating"),
        ):
            login.stop_graphical_session(account, io.BytesIO())
        self.assertEqual(len(calls), 4)
        self.assertEqual(
            [call for call in calls if "stop" in call],
            [["systemctl", "--user", "--no-block", "stop", "graphical-session.target"]],
        )

    def test_native_shutdown_rejects_symlink_or_wrong_owner_before_connecting(self):
        account = SimpleNamespace(pw_uid=1007)
        for mode, uid in ((0o120700, 1007), (0o40700, 0), (0o40777, 1007)):
            with (
                self.subTest(mode=mode, uid=uid),
                patch.object(login.Path, "is_dir", return_value=True),
                patch.object(
                    login.Path, "lstat", return_value=SimpleNamespace(st_uid=uid, st_mode=mode)
                ),
                patch.object(login.subprocess, "run") as run,
                self.assertRaisesRegex(RuntimeError, "private user"),
            ):
                login.stop_graphical_session(account, io.BytesIO())
            run.assert_not_called()

    def test_native_shutdown_without_systemd_never_starts_private_manager(self):
        with (
            patch.object(login.Path, "is_dir", return_value=False),
            patch.object(login.subprocess, "run") as run,
        ):
            login.stop_graphical_session(object(), io.BytesIO())
        run.assert_not_called()

    def test_native_shutdown_precedes_pam_and_failure_still_reaps_owned_children(self):
        events, account, children = [], object(), [object()]

        def graphical(*args):
            events.append("graphical")
            raise subprocess.TimeoutExpired("systemctl", 6)

        with (
            patch.object(login, "stop_graphical_session", side_effect=graphical),
            patch.object(
                login.subprocess, "run", side_effect=lambda *args, **kwargs: events.append("pam")
            ) as run,
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            login.stop_login(
                "c7", children, lambda value: events.append("children"), io.BytesIO(), account
            )
        self.assertEqual(events, ["graphical", "pam", "children"])
        self.assertEqual(run.call_args.args[0], ["loginctl", "terminate-session", "c7"])

    def test_private_endpoint_rejects_symlink_permissions_and_oversized_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ready.json"
            path.write_text(json.dumps({"test": True}))
            path.chmod(0o600)
            self.assertEqual(login.private_json(path, os.getuid()), {"test": True})
            symlink = Path(temporary) / "link"
            symlink.symlink_to(path)
            with self.assertRaises(OSError):
                login.private_json(symlink, os.getuid())
            path.chmod(0o644)
            with self.assertRaisesRegex(RuntimeError, "private regular"):
                login.private_json(path, os.getuid())
            path.chmod(0o600)
            path.write_text(" " * (1024 * 1024 + 1))
            with self.assertRaisesRegex(RuntimeError, "size limit"):
                login.private_json(path, os.getuid())


if __name__ == "__main__":
    unittest.main()
