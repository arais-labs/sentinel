"""Apply host matching through stock per-user desktop keyboard settings.

Only explicit desktop startup calls this. An explicit keyboard:null leaves all
native settings untouched; there is no input injector or background enforcer.
"""

import os
import subprocess

from desktop_keyboard import resolve_keyboard


def run(arguments, environment, credentials=None):
    subprocess.run(
        arguments,
        env=environment,
        check=True,
        timeout=8,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        **(credentials or {}),
    )


def before_start(desktop, keyboard, environment):
    if keyboard is None:
        return
    if os.getuid() == 0 or os.geteuid() != os.getuid():
        raise RuntimeError("Native keyboard settings must run as the desktop user")
    keyboard = resolve_keyboard({"keyboard": keyboard}, {})
    desktop = desktop.lower()
    if desktop == "gnome":
        # Mutter's native input-source settings support layouts/models/options,
        # not a custom XKB rules file. Do not silently claim a different ruleset.
        if keyboard["rules"] != "evdev":
            raise ValueError("GNOME keyboard matching requires the evdev XKB rules")
        layouts = keyboard["layout"].split(",")
        variants = keyboard["variant"].split(",")
        if len(variants) > len(layouts):
            raise ValueError("Keyboard variants exceed the configured layouts")
        sources = [
            (
                "xkb",
                layout
                + ("+" + variants[index] if index < len(variants) and variants[index] else ""),
            )
            for index, layout in enumerate(layouts)
        ]
        settings = {
            "sources": sources,
            "mru-sources": sources,
            "xkb-options": [value for value in keyboard["options"].split(",") if value],
            "xkb-model": keyboard["model"],
        }
        for key, value in settings.items():
            run(
                [
                    "gsettings",
                    "set",
                    "org.gnome.desktop.input-sources",
                    key,
                    repr(value),
                ],
                environment,
            )
    elif desktop in {"plasma", "kde"}:
        settings = {
            "Model": keyboard["model"],
            "LayoutList": keyboard["layout"],
            "VariantList": keyboard["variant"],
            "Options": keyboard["options"],
            "ResetOldOptions": "true",
            "Use": "true",
        }
        for key, value in settings.items():
            run(
                [
                    "kwriteconfig6",
                    "--file",
                    "kxkbrc",
                    "--group",
                    "Layout",
                    "--key",
                    key,
                    value,
                ],
                environment,
            )
    elif desktop == "xfce":
        settings = {
            "XkbModel": keyboard["model"],
            "XkbLayout": keyboard["layout"],
            "XkbVariant": keyboard["variant"],
        }
        for option, key in (
            ("grp:", "XkbOptions/Group"),
            ("compose:", "XkbOptions/Compose"),
        ):
            settings[key] = next(
                (
                    value
                    for value in reversed(keyboard["options"].split(","))
                    if value.startswith(option)
                ),
                "",
            )
        for key, value in settings.items():
            run(
                [
                    "xfconf-query",
                    "--channel",
                    "keyboard-layout",
                    "--property",
                    "/Default/" + key,
                    "--create",
                    "--type",
                    "string",
                    "--set",
                    value,
                ],
                environment,
            )
        run(
            [
                "xfconf-query",
                "--channel",
                "keyboard-layout",
                "--property",
                "/Default/XkbDisable",
                "--create",
                "--type",
                "bool",
                "--set",
                "false",
            ],
            environment,
        )
    elif desktop != "lxqt":
        raise ValueError("Unknown desktop keyboard settings adapter")
    # labwc consumes XKB_DEFAULT_* directly, already supplied in launch env.


def after_start(desktop, keyboard, environment, credentials):
    if keyboard is None or desktop.lower() != "xfce":
        return
    if credentials.get("user", 0) == 0:
        raise RuntimeError("X11 keyboard settings require the desktop user's credentials")
    keyboard = resolve_keyboard({"keyboard": keyboard}, {})
    # XFCE stores layout/model/variant itself, but exposes only group/compose
    # options. Set the complete XKB map on the authenticated live X server too.
    arguments = ["setxkbmap"]
    for field in ("rules", "model", "layout", "variant"):
        arguments.extend(["-" + field, keyboard[field]])
    arguments.extend(["-option", "", "-option", keyboard["options"]])
    run(arguments, environment, credentials)
