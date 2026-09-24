"""Prevent idle password prompts in the managed, password-locked VM account."""

import os
import subprocess


def configure(desktop, environment):
    if os.getuid() == 0:
        raise RuntimeError("Desktop lock preferences must run as the desktop user")
    commands = []
    if desktop.lower() in {"plasma", "kde"}:
        commands = [
            [
                "kwriteconfig6",
                "--file",
                "kscreenlockerrc",
                "--group",
                "Daemon",
                "--key",
                key,
                "--type",
                "bool",
                "false",
            ]
            for key in ("Autolock", "LockOnResume")
        ]
    elif desktop.lower() == "gnome":
        commands = [
            ["gsettings", "set", "org.gnome.desktop.screensaver", "lock-enabled", "false"],
            ["gsettings", "set", "org.gnome.desktop.session", "idle-delay", "0"],
        ]
    elif desktop.lower() == "xfce":
        commands = [
            [
                "xfconf-query",
                "--channel",
                channel,
                "--property",
                key,
                "--create",
                "--type",
                "bool",
                "--set",
                "false",
            ]
            for channel, key in (
                ("xfce4-screensaver", "/lock/enabled"),
                ("xfce4-screensaver", "/saver/idle-activation/enabled"),
                ("xfce4-power-manager", "/xfce4-power-manager/lock-screen-suspend-hibernate"),
            )
        ]
    # The minimal LXQt/labwc selection does not install an idle locker.
    for command in commands:
        subprocess.run(
            command,
            env=environment,
            check=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
