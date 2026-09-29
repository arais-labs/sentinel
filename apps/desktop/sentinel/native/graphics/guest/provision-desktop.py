"""Install the selected desktop into the workspace's existing Linux filesystem.

Only explicit workspace setup calls this. Reconnecting never installs packages
or replaces an agent's session configuration.
"""

import argparse
import json
import os
import platform
from pathlib import Path
import re
import shlex
import shutil
import subprocess

from desktop_contract import NATIVE_DESKTOPS, require_session_contract
from desktop_gnome import configure_gnome
from desktop_packages import install_bundled_apk, install_bundled_deb

COMMON_APK = "dbus python3 sudo shadow xauth socat kmod libbsd eudev libinput-libs libinput-udev libxkbcommon mesa-egl mesa-dri-gallium libxshmfence libdrm zstd-libs expat libx11 libxext libxcb wayland-libs-client wayland-libs-server font-dejavu font-noto font-noto-emoji pulseaudio pulseaudio-utils opus".split()
# The OS owns GLVND's public dispatch and Mesa's standard vendor registration.
# Sentinel supplies vendor libraries through its global libc loader directory.
COMMON_APT = "dbus python3 sudo passwd xauth socat kmod libbsd0 udev libinput10 libxkbcommon0 libegl1 libgl1 libgles2 libopengl0 libegl-mesa0 libgl1-mesa-dri libxshmfence1 libdrm2 libzstd1 libexpat1 libx11-6 libx11-xcb1 libxext6 libxcb1 libxcb-dri3-0 libxcb-present0 libxcb-sync1 libxcb-xfixes0 libxfixes3 libwayland-client0 libwayland-server0 fonts-dejavu fonts-noto-core fonts-noto-color-emoji pulseaudio pulseaudio-utils libopus0".split()
NATIVE_AUDIO_APK = ["pipewire", "pipewire-pulse", "wireplumber"]
NATIVE_AUDIO_APT = ["pipewire", "pipewire-pulse", "wireplumber"]

DESKTOP_APK = {
    "xfce": "xorg-server xinit setxkbmap xf86-input-libinput xfce4 xfce4-terminal xfce4-whiskermenu-plugin greybird-themes greybird-themes-gtk3 numix-themes-xfwm4 papirus-icon-theme dbus-x11 xdpyinfo xrandr xclip xdotool".split(),
    "weston": "weston weston-backend-drm weston-shell-desktop weston-terminal weston-xwayland seatd xwayland wl-clipboard xfce4-appfinder thunar".split(),
    "lxqt": "labwc lxqt-session lxqt-panel lxqt-runner lxqt-config lxqt-qtplugin lxqt-notificationd lxqt-policykit lxqt-themes pcmanfm-qt qterminal breeze-icons qt6-qtwayland qt6-qtsvg xwayland wl-clipboard wlr-randr".split(),
    # Alpine keeps GNOME Shell's mandatory Gdm-1.0 typelib/libgdm in gdm,
    # without a separate client-library package. Only install it; greetd owns
    # the graphical login and no GDM service is enabled or started here.
    # Shell imports Polkit/PolkitAgent, whose typelibs are not in the linked
    # libraries subpackage. Use Alpine's native elogind-aware policy service.
    "gnome": "gnome-shell gnome-session gnome-shell-session gdm polkit-elogind gnome-settings-daemon gnome-control-center gnome-terminal nautilus localsearch gnome-backgrounds adwaita-icon-theme xdg-desktop-portal-gnome xwayland xclip py3-gobject3 glib".split(),
    "plasma": "plasma-desktop plasma-workspace kwin kconfig libkscreen dolphin konsole systemsettings breeze breeze-icons qt6-qtwayland qt6-qtsvg xdg-desktop-portal-kde xwayland wl-clipboard".split(),
}
DESKTOP_APT = {
    "xfce": "xserver-xorg-core xinit xserver-xorg-input-libinput xfce4 xfce4-terminal xfce4-whiskermenu-plugin greybird-gtk-theme numix-gtk-theme papirus-icon-theme dbus-x11 x11-utils x11-xserver-utils xclip xdotool".split(),
    "weston": "weston seatd xwayland wl-clipboard xfce4-appfinder thunar".split(),
    "lxqt": "labwc lxqt-session lxqt-panel lxqt-runner lxqt-config lxqt-qtplugin lxqt-notificationd lxqt-policykit lxqt-themes pcmanfm-qt qterminal breeze-icon-theme qt6-wayland qt6-svg-plugins xwayland wl-clipboard wlr-randr".split(),
    "gnome": "gnome-shell gnome-session gnome-settings-daemon gnome-control-center gnome-terminal nautilus gnome-backgrounds adwaita-icon-theme xdg-desktop-portal-gnome xwayland xclip python3-gi libglib2.0-bin gir1.2-glib-2.0".split(),
    "plasma": "plasma-desktop plasma-workspace kwin-wayland libkf6config-bin libkscreen-bin dolphin konsole systemsettings breeze breeze-icon-theme qt6-wayland qt6-svg-plugins xdg-desktop-portal-kde xwayland wl-clipboard".split(),
}

DESKTOP_COMMANDS = {
    # startx owns the X server, authorization cookie and XFCE client lifetime.
    # Keep greetd's controlling VT so Xorg can acquire devices through logind.
    "xfce": ["/usr/bin/startx", "/usr/bin/startxfce4", "--", "-keeptty", "-nolisten", "tcp"],
    "weston": [
        "weston",
        "--backend=drm-backend.so",
        "--drm-device=card0",
        "--socket=wayland-0",
    ],
    "lxqt": ["labwc", "-S", "lxqt-session"],
    "gnome": ["/usr/bin/gnome-session"],
    "plasma": ["/usr/bin/startplasma-wayland"],
}

# These are full, distribution-maintained graphical login sessions. Their
# compositors acquire devices through logind/elogind, and their session managers
# own the application/service lifetime. Installing packages is not a substitute
# for the native-init/PAM lifecycle selected by this explicit configuration.
NATIVE_DESKTOP_ENVIRONMENTS = {
    "xfce": {
        "XDG_CURRENT_DESKTOP": "XFCE",
        "XDG_SESSION_DESKTOP": "xfce",
        "XDG_SESSION_TYPE": "x11",
    },
    "lxqt": {
        "XDG_CURRENT_DESKTOP": "LXQt:labwc",
        "XDG_SESSION_DESKTOP": "lxqt",
        "XDG_SESSION_TYPE": "wayland",
        "XDG_MENU_PREFIX": "lxqt-",
        # Match the distro's LXQt data/config search path without overwriting
        # the user's panel, theme, compositor or appearance configuration.
        "XDG_CONFIG_DIRS": "/etc:/etc/xdg:/usr/share",
        "XDG_DATA_DIRS": "/usr/local/share:/usr/share",
        "QT_QPA_PLATFORM": "wayland",
        "QT_QPA_PLATFORMTHEME": "lxqt",
        "LIBSEAT_BACKEND": "logind",
    },
    "gnome": {
        "XDG_CURRENT_DESKTOP": "GNOME",
        "XDG_SESSION_DESKTOP": "gnome",
        "XDG_SESSION_TYPE": "wayland",
    },
    "plasma": {
        "XDG_CURRENT_DESKTOP": "KDE",
        "XDG_SESSION_DESKTOP": "KDE",
        "XDG_SESSION_TYPE": "wayland",
        # Composite the cursor on the GPU; avoid KWin's GLES cursor readback.
        "KWIN_FORCE_SW_CURSOR": "1",
    },
}


def require_lxqt_apt():
    """Reject old distribution packages before installing or changing a session."""
    for name, minimum in (("lxqt-session", "2.1"), ("lxqt-panel", "2.1"), ("labwc", None)):
        result = subprocess.run(
            ["apt-cache", "policy", name],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "LC_ALL": "C"},
        )
        match = re.search(r"^\s*Candidate:\s*(\S+)", result.stdout, re.MULTILINE)
        version = match[1] if match else "(none)"
        if version == "(none)" or (
            minimum
            and subprocess.run(["dpkg", "--compare-versions", version, "ge", minimum]).returncode
            != 0
        ):
            raise RuntimeError(
                "LXQt Wayland requires LXQt session/panel 2.1 or newer and labwc; "
                f"this distribution provides {name} {version}. Reinstall this "
                "workspace to use the current OS image (VM-only data is erased), "
                "or keep the existing desktop. "
                "Desktop setup does not upgrade the operating system."
            )


def configure_weston(path):
    """Add discoverable apps/files launchers during explicit desktop setup only.

    Preserve the existing INI verbatim, including repeated launcher/output
    sections, custom commands and shell bindings. Weston only accepts PNG icons;
    Appfinder and Thunar ship these icons with their distribution packages.
    """
    saved = (
        path.read_text()
        if path.exists()
        else (
            "[core]\nshell=desktop-shell.so\nxwayland=true\nidle-time=0\nrepaint-window=4\n"
            "[shell]\nlocking=false\npanel-position=top\n"
        )
    )
    # Weston draws launcher PNGs at their intrinsic size. Keep its 32px panel
    # usable with packaged small icons, including previously generated defaults.
    # Thunar ships a 16px app icon but no 24px app icon; do not substitute one of
    # its unrelated 24px copy/move action icons or rewrite custom icon choices.
    original = saved
    for name, size in (("appfinder", 24), ("thunar", 16)):
        saved = re.sub(
            rf"(?m)^icon=/usr/share/icons/hicolor/48x48/apps/org\.xfce\.{name}\.png$",
            f"icon=/usr/share/icons/hicolor/{size}x{size}/apps/org.xfce.{name}.png",
            saved,
        )
    programs = set()
    section = None
    for line in saved.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
        elif section == "launcher" and line.partition("=")[0].strip() == "path":
            # Recognize custom arguments or environment prefixes without
            # rewriting the user's launcher or executing its command.
            try:
                programs.update(Path(token).name for token in shlex.split(line.partition("=")[2]))
            except ValueError:
                continue
    additions = []
    for program, title, icon in (
        (
            "xfce4-appfinder",
            "Applications",
            "/usr/share/icons/hicolor/24x24/apps/org.xfce.appfinder.png",
        ),
        ("thunar", "Files", "/usr/share/icons/hicolor/16x16/apps/org.xfce.thunar.png"),
        ("weston-terminal", "Terminal", "/usr/share/weston/terminal.png"),
    ):
        if program not in programs:
            additions.append(
                f"\n[launcher]\ndisplayname={title}\nicon={icon}\npath=/usr/bin/{program}\n"
            )
    if not additions and saved == original and path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".next")
    temporary.write_text(
        saved + ("\n" if saved and not saved.endswith("\n") else "") + "".join(additions)
    )
    temporary.replace(path)


def package_names(selection, common, profiles, native_audio):
    names = common + profiles[selection]
    if selection in NATIVE_DESKTOP_ENVIRONMENTS:
        # Native sessions own one PipeWire/Pulse-compatible service. Keep Pulse
        # client tools/libraries, not the competing pulseaudio daemon package.
        names = [name for name in names if name != "pulseaudio"] + native_audio
    return sorted(set(names))


def packages(selection):
    if shutil.which("apk"):
        names = package_names(selection, COMMON_APK, DESKTOP_APK, NATIVE_AUDIO_APK)
        if selection == "lxqt":
            # apk resolves the complete transaction before installing anything.
            # Constraints also prevent accepting an already-installed old panel.
            names = [
                name + ">=2.1" if name in {"lxqt-session", "lxqt-panel"} else name for name in names
            ]
            subprocess.run(["apk", "add", "--no-cache", *names], check=True)
            return
        if subprocess.run(
            ["apk", "info", "-e", *names], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode:
            subprocess.run(["apk", "add", "--no-cache", *names], check=True)
        return
    if not shutil.which("apt-get"):
        raise RuntimeError("Desktop setup supports Alpine, Ubuntu and Debian")
    environment = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    if selection == "lxqt":
        subprocess.run(
            ["apt-get", "-o", "Acquire::Retries=3", "update"], env=environment, check=True
        )
        require_lxqt_apt()
    names = package_names(selection, COMMON_APT, DESKTOP_APT, NATIVE_AUDIO_APT)
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Package} ${Status}\n", *names], capture_output=True, text=True
    )
    installed = {
        line.split()[0]
        for line in result.stdout.splitlines()
        if line.endswith(" install ok installed")
    }
    if selection != "lxqt" and set(names).issubset(installed):
        return
    # This is a running native Linux system, not an offline image build.
    # Maintainer scripts must reload services after installing their policy and
    # accounts (notably D-Bus/polkit). Never override an administrator's policy.
    if selection != "lxqt":
        subprocess.run(
            ["apt-get", "-o", "Acquire::Retries=3", "update"], env=environment, check=True
        )
    subprocess.run(
        [
            "apt-get",
            "-o",
            "DPkg::Lock::Timeout=120",
            "install",
            "-y",
            "--no-install-recommends",
            *names,
        ],
        env=environment,
        check=True,
    )


def existing_configuration(selection, root=Path("/etc/sentinel")):
    choice, config = root / "desktop-choice", root / "desktop.json"
    previous = choice.read_text().strip() if choice.exists() else None
    if config.exists():
        saved = json.loads(config.read_text())
        # Selecting another desktop is not an implicit OS/session migration.
        # Refuse before package installation or any persistent config changes.
        if selection in NATIVE_DESKTOPS:
            require_session_contract(saved, selection)
        return previous, saved
    return previous, None


def configure(selection, root=Path("/etc/sentinel"), *, distribution=None):
    previous, saved = existing_configuration(selection, root)
    root.mkdir(parents=True, exist_ok=True)
    choice, config = root / "desktop-choice", root / "desktop.json"
    # Editing desktop.json is supported. Only selecting a different desktop in
    # workspace settings replaces it, preserving the previous configuration.
    if saved is not None and previous in (None, selection):
        if selection == "gnome":
            saved = configure_gnome(saved)
            temporary = config.with_suffix(".next")
            temporary.write_text(json.dumps(saved, indent=2) + "\n")
            temporary.replace(config)
        choice.write_text(selection + "\n")
        return
    if config.exists():
        shutil.copy2(config, root / "desktop.previous.json")
    defaults = {
        "protocol": "x11" if selection == "xfce" else "wayland",
        "clipboard_backend": "x11" if selection in {"xfce", "gnome"} else "wayland",
        "command": DESKTOP_COMMANDS[selection].copy(),
        "environment": {},
        "audio": True,
    }
    if selection == "lxqt" and distribution == "alpine":
        # The bundled labwc 0.20 carries the stationary-pointer map fix. Its
        # wlroots, runtime dependencies and defaults remain distro-owned.
        # An absent bundle is an installation error, never a stock fallback.
        defaults["command"][0] = "/opt/sentinel/graphics/bin/labwc"
    if selection in NATIVE_DESKTOP_ENVIRONMENTS:
        defaults["session_manager"] = "native"
        defaults["environment"] = NATIVE_DESKTOP_ENVIRONMENTS[selection].copy()
        # Native desktop sessions own display servers and socket names. Output
        # configuration uses the actual published session, never a fake mode.
    if selection == "gnome":
        defaults = configure_gnome(defaults)
    temporary = config.with_suffix(".next")
    temporary.write_text(json.dumps(defaults, indent=2) + "\n")
    temporary.replace(config)
    choice.write_text(selection + "\n")


def install_native_desktop_packages(selection):
    if selection != "plasma":
        return
    distribution = platform.freedesktop_os_release().get("ID")
    if distribution not in {"alpine", "debian", "ubuntu"}:
        raise RuntimeError("Unsupported native Plasma package distribution")
    root = Path(__file__).parent / "native-packages" / distribution / "packages"
    if distribution == "alpine":
        install_bundled_apk("plasma-workspace-libs", root)
    else:
        install_bundled_deb("libklipper6", root)


def configure_native_autostart(root=Path("/etc/xdg/autostart")):
    # This entry only publishes a live Sentinel-owned session. It is inert in
    # unrelated graphical logins, and leaves desktop/user autostart untouched.
    import runpy

    source = Path(__file__).with_name("desktop-native-session.py")
    contents = runpy.run_path(str(source))["AUTOSTART"]
    root.mkdir(parents=True, exist_ok=True)
    path = root / "sentinel-session.desktop"
    temporary = path.with_suffix(".next")
    temporary.write_text(contents)
    temporary.chmod(0o644)
    temporary.replace(path)


def configure_xorg_wrapper(selection, distribution, root=Path("/etc/X11")):
    if selection != "xfce" or distribution != "alpine":
        return
    # Alpine builds stock Xorg without logind support. Its distribution-owned
    # setuid wrapper must retain privileges for the server's VT/device access;
    # greetd, startx, XFCE and every client still use the regular desktop login.
    # Other distributions use their normal rootless logind-enabled server.
    root.mkdir(parents=True, exist_ok=True)
    try:
        with (root / "Xwrapper.config").open("x") as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(
                "# Stock Alpine Xorg has no logind device broker.\n"
                "allowed_users=console\nneeds_root_rights=yes\n"
            )
    except FileExistsError:
        pass  # An explicit administrator override remains authoritative.


if __name__ == "__main__":
    from desktop_user import as_user, ensure_account

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("desktop", choices=DESKTOP_APK)
    args = parser.parse_args()
    existing_configuration(args.desktop)
    packages(args.desktop)
    install_native_desktop_packages(args.desktop)
    account = ensure_account()
    configure_xorg_wrapper(args.desktop, platform.freedesktop_os_release().get("ID"))
    if args.desktop in NATIVE_DESKTOP_ENVIRONMENTS:
        configure_native_autostart()
    if args.desktop == "weston":
        with as_user(account):
            configure_weston(Path(account.pw_dir) / ".config/weston.ini")
    configure(args.desktop, distribution=platform.freedesktop_os_release().get("ID"))
    print("Workspace desktop configured")
