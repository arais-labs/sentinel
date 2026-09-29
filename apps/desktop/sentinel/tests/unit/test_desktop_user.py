import importlib.util
from pathlib import Path
import pwd
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

source = Path(__file__).resolve().parents[2] / "native/graphics/guest/desktop_user.py"
spec = importlib.util.spec_from_file_location("desktop_user", source)
user = importlib.util.module_from_spec(spec)
spec.loader.exec_module(user)


def account(home=user.HOME, uid=1234, gid=2345):
    return pwd.struct_passwd(("sentinel", "x", uid, gid, "", str(home), "/bin/sh"))


class DesktopUserTests(unittest.TestCase):
    def test_identity_uses_allocated_ids_and_rejects_root_or_wrong_home(self):
        expected = account()
        with patch.object(user.pwd, "getpwnam", return_value=expected):
            self.assertEqual(user.identity(), expected)
        for invalid in (account(uid=0), account(gid=0), account(home="/root")):
            with patch.object(user.pwd, "getpwnam", return_value=invalid):
                with self.assertRaises(RuntimeError):
                    user.identity()
        with patch.object(user.pwd, "getpwnam", side_effect=KeyError):
            with self.assertRaisesRegex(RuntimeError, "run workspace setup"):
                user.identity()

    def test_setup_preserves_account_and_uses_only_standard_graphics_groups(self):
        expected = account()
        with (
            patch.object(user.os, "geteuid", return_value=0),
            patch.object(user.pwd, "getpwnam", return_value=expected),
            patch.object(
                user.Path,
                "lstat",
                return_value=SimpleNamespace(st_mode=0o40700, st_uid=expected.pw_uid),
            ),
            patch.object(user.grp, "getgrnam"),
            patch.object(user.subprocess, "run") as run,
            patch.object(user, "write_root_file") as write,
        ):
            self.assertEqual(user.ensure_account(), expected)
        run.assert_called_once_with(
            ["usermod", "--append", "--groups", "video,render", "sentinel"], check=True
        )
        # The driver no longer requires privileged userfaultfd access. Setup
        # must not install a device-permission rule alongside the sudo policy.
        write.assert_called_once()
        sudo = write.call_args
        self.assertEqual(sudo.args[0], Path("/etc/sudoers.d/sentinel"))
        self.assertIn("sentinel ALL=(ALL:ALL) NOPASSWD: ALL", sudo.args[1])
        self.assertEqual(sudo.kwargs["mode"], 0o440)
        self.assertEqual(sudo.kwargs["validate"], ["visudo", "--check", "--file"])

    def test_new_account_creation_does_not_force_uid(self):
        expected = account()
        with (
            patch.object(user.os, "geteuid", return_value=0),
            patch.object(user.pwd, "getpwnam", side_effect=[KeyError, expected]),
            patch.object(
                user.Path,
                "lstat",
                return_value=SimpleNamespace(st_mode=0o40700, st_uid=expected.pw_uid),
            ),
            patch.object(user.grp, "getgrnam", side_effect=KeyError),
            patch.object(user.subprocess, "run") as run,
            patch.object(user, "write_root_file"),
        ):
            user.ensure_account()
        command = run.call_args.args[0]
        self.assertEqual(command[0], "useradd")
        self.assertIn("--create-home", command)
        self.assertNotIn("--uid", command)

    def test_preference_credentials_restored_on_failure(self):
        expected = account()
        with (
            patch.object(user.os, "geteuid", return_value=0),
            patch.object(user.os, "getegid", return_value=0),
            patch.object(user.os, "getgroups", return_value=[0]),
            patch.object(user.os, "getgrouplist", return_value=[2345, 77]),
            patch.object(user.os, "seteuid") as uid,
            patch.object(user.os, "setegid") as gid,
            patch.object(user.os, "setgroups") as groups,
        ):
            with self.assertRaises(ValueError):
                with user.as_user(expected):
                    raise ValueError("preference write failed")
        self.assertEqual(uid.call_args_list, [call(1234), call(0)])
        self.assertEqual(gid.call_args_list, [call(2345), call(0)])
        self.assertEqual(groups.call_args_list, [call([2345, 77]), call([0])])

    def test_root_policy_is_validated_before_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sudoers.d/sentinel"
            with patch.object(user.os, "fchown"), patch.object(user.subprocess, "run") as run:
                user.write_root_file(path, "valid", mode=0o440, validate=["visudo", "-cf"])
                self.assertEqual(path.read_text(), "valid")
                self.assertEqual(path.stat().st_mode & 0o777, 0o440)
                run.assert_called_once()
                run.side_effect = RuntimeError("invalid policy")
                with self.assertRaises(RuntimeError):
                    user.write_root_file(path, "invalid", mode=0o440, validate=["visudo", "-cf"])
            self.assertEqual(path.read_text(), "valid")
            self.assertEqual(list(path.parent.iterdir()), [path])
