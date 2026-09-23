"""The browser worker uses the desktop identity without changing agent identity."""

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

SOURCE = Path(__file__).parents[1] / "app/services/runtime/guest_commands/linux/browser/start.py"
spec = importlib.util.spec_from_file_location("browser_guest_start", SOURCE)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
ACCOUNT = SimpleNamespace(pw_name="sentinel", pw_uid=12345, pw_gid=12345, pw_dir="/home/sentinel")


def test_snap_automation_profiles_are_confined_and_session_specific():
    first = worker.snap_profile("/var/lib/sentinel/control/one/browser", ACCOUNT.pw_dir)
    assert first.parent == Path("/home/sentinel/snap/chromium/common/sentinel-automation")
    assert first == worker.snap_profile("/var/lib/sentinel/control/one/browser", ACCOUNT.pw_dir)
    assert first != worker.snap_profile("/var/lib/sentinel/control/two/browser", ACCOUNT.pw_dir)


@pytest.mark.parametrize("protocol", ["x11", "wayland"])
def test_gui_uses_published_session_and_default_sandbox(tmp_path, monkeypatch, protocol):
    session = tmp_path / "session.json"
    environment = {
        "HOME": ACCOUNT.pw_dir,
        "USER": ACCOUNT.pw_name,
        "XDG_RUNTIME_DIR": "/run/user/12345",
        "DISPLAY": ":2",
        "GALLIUM_DRIVER": "virgl",
        "LD_LIBRARY_PATH": "/opt/sentinel/graphics/lib",
    }
    if protocol == "wayland":
        environment["WAYLAND_DISPLAY"] = "wayland-0"
    session.write_text(
        json.dumps(
            {"protocol": protocol, "user": {"uid": ACCOUNT.pw_uid}, "environment": environment}
        )
    )
    monkeypatch.setenv("VTEST_SOCKET_NAME", "/obsolete/renderer.sock")
    monkeypatch.setenv("LIBGL_ALWAYS_SOFTWARE", "1")
    env, actual = worker.browser_environment(ACCOUNT, ":2", session)
    assert all(env[key] == value for key, value in environment.items())
    assert "VTEST_SOCKET_NAME" not in env
    assert "LIBGL_ALWAYS_SOFTWARE" not in env
    command = worker.browser_command("chromium", 9300, "/profile", "1280x800", actual)
    assert f"--ozone-platform={protocol}" in command
    assert not any(
        flag in command for flag in ("--no-sandbox", "--disable-vulkan", "--ignore-gpu-blocklist")
    )
    assert not any(flag.startswith("--use-angle") for flag in command)


def test_headless_regular_user_does_not_require_desktop_session(tmp_path):
    env, protocol = worker.browser_environment(ACCOUNT, "", tmp_path / "missing")
    assert env["HOME"] == ACCOUNT.pw_dir
    assert "--headless=new" in worker.browser_command(
        "chromium", 9300, "/profile", "1280x800", protocol
    )


def test_headless_runtime_is_private_and_rejects_symlinks(tmp_path):
    with patch.object(worker.os, "fchown") as chown:
        runtime = Path(worker.prepare_headless_runtime(tmp_path, ACCOUNT))
    assert runtime.stat().st_mode & 0o777 == 0o700
    chown.assert_called_once()
    runtime.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    runtime.symlink_to(outside, target_is_directory=True)
    with patch.object(worker.os, "fchown") as chown, pytest.raises(OSError):
        worker.prepare_headless_runtime(tmp_path, ACCOUNT)
    chown.assert_not_called()


def test_browser_child_privileges_do_not_change_supervisor():
    with (
        patch.object(worker.os, "geteuid", return_value=0),
        patch.object(worker.os, "getgrouplist", return_value=[12345, 44]),
    ):
        assert worker.browser_credentials(ACCOUNT) == {
            "user": 12345,
            "group": 12345,
            "extra_groups": [12345, 44],
        }
    with patch.object(worker.os, "geteuid", return_value=12345):
        assert worker.browser_credentials(ACCOUNT) == {}
    with patch.object(worker.os, "geteuid", return_value=555):
        with pytest.raises(ValueError):
            worker.browser_credentials(ACCOUNT)


@pytest.mark.parametrize("headless", [False, True])
def test_snap_scope_preserves_command_and_native_environment(monkeypatch, headless):
    monkeypatch.setattr(Path, "is_dir", lambda _: True)
    monkeypatch.setattr(Path, "exists", lambda _: True)
    monkeypatch.setattr(worker.os, "geteuid", lambda: ACCOUNT.pw_uid)
    monkeypatch.setattr(worker, "native_user_runtime", lambda _: Path("/run/user/12345"))
    environment = {"DISPLAY": ":2", "GALLIUM_DRIVER": "virgl"}
    command = ["chromium", "--user-data-dir=/profile", "about:blank"]
    with patch.object(worker.subprocess, "run") as run:
        actual = worker.snap_launch_command(command, ACCOUNT, environment, headless=headless)
    assert actual == ["systemd-run", "--user", "--scope", "--quiet", "--", *command]
    assert environment == {
        "DISPLAY": ":2",
        "GALLIUM_DRIVER": "virgl",
        "XDG_RUNTIME_DIR": "/run/user/12345",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/12345/bus",
    }
    run.assert_not_called()


def test_headless_snap_starts_native_manager_as_root(monkeypatch):
    monkeypatch.setattr(Path, "is_dir", lambda _: True)
    monkeypatch.setattr(Path, "exists", lambda _: False)
    monkeypatch.setattr(worker.os, "geteuid", lambda: 0)
    monkeypatch.setattr(worker.os, "getgrouplist", lambda *_: [ACCOUNT.pw_gid])
    with (
        patch.object(
            worker,
            "native_user_runtime",
            side_effect=[FileNotFoundError(), Path("/run/user/12345")],
        ),
        patch.object(worker.subprocess, "run") as run,
    ):
        worker.snap_launch_command(["chromium"], ACCOUNT, {}, headless=True)
    assert [call.args[0] for call in run.call_args_list] == [
        ["loginctl", "enable-linger", "sentinel"],
        ["systemctl", "start", "user@12345.service"],
    ]
    assert all(call.kwargs["check"] and call.kwargs["timeout"] == 10 for call in run.call_args_list)


def test_missing_graphical_manager_does_not_create_replacement(monkeypatch):
    monkeypatch.setattr(Path, "is_dir", lambda _: True)
    monkeypatch.setattr(worker.os, "geteuid", lambda: ACCOUNT.pw_uid)
    with (
        patch.object(worker, "native_user_runtime", side_effect=FileNotFoundError()),
        patch.object(worker.subprocess, "run") as run,
        pytest.raises(ValueError, match="manager is unavailable"),
    ):
        worker.snap_launch_command(["chromium"], ACCOUNT, {}, headless=False)
    run.assert_not_called()


@pytest.mark.parametrize(
    "bad_path,mode,uid",
    [
        ("/run/user/12345", stat.S_IFDIR | 0o755, ACCOUNT.pw_uid),
        ("/run/user/12345/systemd", stat.S_IFLNK | 0o700, ACCOUNT.pw_uid),
        ("/run/user/12345/systemd/private", stat.S_IFSOCK | 0o600, 0),
        ("/run/user/12345/bus", stat.S_IFREG | 0o600, ACCOUNT.pw_uid),
    ],
)
def test_native_runtime_rejects_wrong_owner_permissions_and_types(monkeypatch, bad_path, mode, uid):
    def info(path):
        if str(path) == bad_path:
            return SimpleNamespace(st_mode=mode, st_uid=uid)
        kind = stat.S_IFSOCK if path.name in {"private", "bus"} else stat.S_IFDIR
        return SimpleNamespace(st_mode=kind | 0o700, st_uid=ACCOUNT.pw_uid)

    monkeypatch.setattr(Path, "lstat", info)
    with pytest.raises(ValueError, match="invalid owner or permissions"):
        worker.native_user_runtime(ACCOUNT)


@pytest.fixture
def private_profile(tmp_path):
    control = tmp_path.resolve() / "private" / "control"
    browser = control / "session" / "browser"
    profile = browser / "chromium"
    profile.mkdir(parents=True)
    (profile / "Preferences").write_text('{"preserved":true}')
    return control, browser, profile


def test_profile_migration_preserves_content_and_does_not_follow_links(private_profile, tmp_path):
    control, browser, profile = private_profile
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    (profile / "external-link").symlink_to(outside)
    with patch.object(worker.os, "fchown") as chown:
        worker.prepare_profile(
            browser, ACCOUNT, control_root=control, root_device=browser.stat().st_dev
        )
    assert (profile / "Preferences").read_text() == '{"preserved":true}'
    assert outside.read_text() == "untouched"
    assert chown.call_count == 6  # profile file, two dirs, three ancestors; never symlink target.
    assert control.stat().st_mode & 0o010


def test_profile_rejects_symlink_root(private_profile):
    control, browser, profile = private_profile
    profile.rename(browser / "original")
    profile.symlink_to(browser / "original", target_is_directory=True)
    with patch.object(worker.os, "fchown") as chown, pytest.raises(ValueError, match="symlink"):
        worker.prepare_profile(
            browser, ACCOUNT, control_root=control, root_device=browser.stat().st_dev
        )
    chown.assert_not_called()


def test_profile_rejects_shared_filesystem(private_profile):
    control, browser, _ = private_profile
    with (
        patch.object(worker.os, "fchown") as chown,
        pytest.raises(ValueError, match="mounted/shared"),
    ):
        worker.prepare_profile(browser, ACCOUNT, control_root=control, root_device=-1)
    chown.assert_not_called()


def test_profile_rejects_hard_links(private_profile, tmp_path):
    control, browser, profile = private_profile
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    os.link(outside, profile / "hard-link")
    with patch.object(worker.os, "fchown"), pytest.raises(ValueError, match="hard-linked"):
        worker.prepare_profile(
            browser, ACCOUNT, control_root=control, root_device=browser.stat().st_dev
        )


def test_profile_rejects_non_private_target(private_profile):
    _, browser, _ = private_profile
    with pytest.raises(ValueError, match="private session state"):
        worker.prepare_profile(browser, ACCOUNT)


@pytest.mark.parametrize(
    "desktop_installed,display,account_exists",
    [
        (True, ":2", True),
        (True, "", True),
        (False, "", True),
        (False, "", False),
    ],
)
def test_shipped_worker_launches_child_with_expected_identity(
    tmp_path, monkeypatch, desktop_installed, display, account_exists
):
    request = {
        "browser": str(tmp_path / "browser"),
        "runtime": str(tmp_path / "runtime"),
        "logs": str(tmp_path / "logs"),
        "session_root": str(tmp_path),
        "home": str(tmp_path / "root-home"),
        "display": display,
    }
    monkeypatch.setattr(sys, "argv", [str(SOURCE), json.dumps(request)])
    monkeypatch.setitem(sys.modules, "desktop_user", SimpleNamespace(identity=lambda: ACCOUNT))

    def account_by_name(_):
        if not account_exists:
            raise KeyError("sentinel")
        return ACCOUNT

    monkeypatch.setattr(worker.pwd, "getpwnam", account_by_name)
    monkeypatch.setattr(worker.os, "geteuid", lambda: 0)
    monkeypatch.setattr(worker.os, "getgrouplist", lambda *_: [ACCOUNT.pw_gid, 44])
    real_is_file = Path.is_file
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda path: (
            desktop_installed
            if str(path) == "/opt/sentinel/desktop/desktop_user.py"
            else real_is_file(path)
        ),
    )
    monkeypatch.setattr(worker.shutil, "which", lambda _: "/usr/bin/chromium")
    monkeypatch.setattr(worker, "prepare_profile", lambda *_: None)
    monkeypatch.setattr(
        worker, "prepare_headless_runtime", lambda *_: str(tmp_path / "browser/runtime")
    )
    monkeypatch.setattr(
        worker,
        "browser_environment",
        lambda *_: (
            {"HOME": ACCOUNT.pw_dir, "XDG_RUNTIME_DIR": "/run/user/12345"},
            "x11" if display else "headless",
        ),
    )
    child = MagicMock(pid=4321)
    monkeypatch.setattr(
        worker,
        "process_identity",
        lambda _: {
            "boot_id": "test-boot",
            "start_ticks": "42",
            "uid": ACCOUNT.pw_uid,
        },
    )
    child.poll.return_value = None
    response = MagicMock()
    response.__enter__.return_value.status = 200
    with (
        patch.object(worker.socket, "socket", MagicMock()),
        patch.object(worker.urllib.request, "urlopen", return_value=response),
        patch.object(worker.subprocess, "Popen", return_value=child) as spawn,
    ):
        with pytest.raises(SystemExit) as exit_result:
            worker.main()
    assert exit_result.value.code == 0
    if account_exists:
        command = spawn.call_args.args[0]
        assert spawn.call_args.kwargs["user"] == ACCOUNT.pw_uid
        assert spawn.call_args.kwargs["extra_groups"] == [ACCOUNT.pw_gid, 44]
        assert "--no-sandbox" not in command
    else:
        spawn.assert_not_called()
