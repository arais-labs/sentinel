"""Regular desktop account setup; the VM supervisor and agent remain root."""

from contextlib import contextmanager
import grp
import os
from pathlib import Path
import pwd
import stat
import subprocess
import tempfile

NAME = "sentinel"
HOME = "/home/sentinel"


def identity():
    try:
        account = pwd.getpwnam(NAME)
    except KeyError as error:
        raise RuntimeError("Desktop account is missing; run workspace setup") from error
    if account.pw_uid == 0 or account.pw_gid == 0 or account.pw_dir != HOME:
        raise RuntimeError("The sentinel account must be non-root with home /home/sentinel")
    return account


def write_root_file(path, contents, mode=0o644, validate=None):
    """Validate a root-owned temporary file before atomically publishing it."""
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".sentinel-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            os.fchown(stream.fileno(), 0, 0)
            os.fchmod(stream.fileno(), mode)
            stream.write(contents)
        if validate:
            subprocess.run([*validate, temporary], check=True)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def ensure_account():
    if os.geteuid() != 0:
        raise RuntimeError("Desktop account setup requires root")
    try:
        pwd.getpwnam(NAME)
    except KeyError:
        subprocess.run(
            [
                "useradd",
                "--create-home",
                "--user-group",
                "--home-dir",
                HOME,
                "--shell",
                "/bin/sh",
                NAME,
            ],
            check=True,
        )
    account = identity()
    home = Path(account.pw_dir).lstat()
    if not stat.S_ISDIR(home.st_mode) or home.st_uid != account.pw_uid:
        raise RuntimeError("Desktop home must be a directory owned by the sentinel account")
    groups = []
    for name in ("video", "render"):
        try:
            grp.getgrnam(name)
        except KeyError:
            continue
        groups.append(name)
    if groups:
        subprocess.run(["usermod", "--append", "--groups", ",".join(groups), NAME], check=True)
    write_root_file(
        Path("/etc/sudoers.d/sentinel"),
        "# Workspace desktop account; the agent retains unrestricted VM administration.\n"
        "sentinel ALL=(ALL:ALL) NOPASSWD: ALL\n",
        mode=0o440,
        validate=["visudo", "--check", "--file"],
    )
    return account


@contextmanager
def as_user(account):
    """Run preference setup as its owner in this single-threaded provisioner."""
    uid, gid, groups = os.geteuid(), os.getegid(), os.getgroups()
    try:
        os.setgroups(os.getgrouplist(account.pw_name, account.pw_gid))
        os.setegid(account.pw_gid)
        os.seteuid(account.pw_uid)
        yield
    finally:
        os.seteuid(uid)
        os.setegid(gid)
        os.setgroups(groups)
