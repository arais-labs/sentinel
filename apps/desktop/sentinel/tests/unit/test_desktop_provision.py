import importlib.util
import json
import subprocess
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

source = Path(__file__).resolve().parents[2] / "native/graphics/guest/provision-desktop.py"
spec = importlib.util.spec_from_file_location("desktop_provision", source)
provision = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(source.parent), *sys.path]):
    spec.loader.exec_module(provision)


class DesktopProvisionTests(unittest.TestCase):
    def test_native_plasma_package_is_distro_owned_and_failure_is_not_hidden(self):
        for distribution in ("alpine", "debian", "ubuntu"):
            with (
                self.subTest(distribution=distribution),
                patch.object(
                    provision.platform, "freedesktop_os_release", return_value={"ID": distribution}
                ),
                patch.object(provision, "install_bundled_apk") as apk,
                patch.object(provision, "install_bundled_deb") as deb,
            ):
                for desktop in ("xfce", "lxqt", "gnome", "weston"):
                    provision.install_native_desktop_packages(desktop)
                apk.assert_not_called()
                deb.assert_not_called()
                provision.install_native_desktop_packages("plasma")
                selected = apk if distribution == "alpine" else deb
                selected.assert_called_once_with(
                    "plasma-workspace-libs" if distribution == "alpine" else "libklipper6",
                    source.parent / "native-packages" / distribution / "packages",
                )
                selected.side_effect = ValueError("invalid package")
                with self.assertRaisesRegex(ValueError, "invalid package"):
                    provision.install_native_desktop_packages("plasma")

    def setUp(self):
        # OS capability probing/asset installation has its own temporary-root
        # tests. These tests exercise profile and package selection on any host.
        installer = patch.object(provision, "configure_gnome", side_effect=lambda config: config)
        installer.start()
        self.addCleanup(installer.stop)

    def test_alpine_gnome_uses_distro_indexer(self):
        events = []

        def run(command, **options):
            events.append(command)
            return SimpleNamespace(returncode=1 if command[:3] == ["apk", "info", "-e"] else 0)

        with (
            patch.object(provision.shutil, "which", return_value="apk"),
            patch.object(provision.subprocess, "run", side_effect=run),
            patch.object(
                provision,
                "install_bundled_apk",
                side_effect=lambda name: events.append(["bundled", name]),
            ),
        ):
            provision.packages("gnome")
        self.assertEqual(events[1][:3], ["apk", "add", "--no-cache"])
        self.assertIn("localsearch", events[1])
        self.assertEqual(len(events), 2)

    def test_alpine_plasma_uses_distro_kwin(self):
        with (
            patch.object(provision.shutil, "which", return_value="apk"),
            patch.object(provision.subprocess, "run", return_value=SimpleNamespace(returncode=0)),
            patch.object(provision, "install_bundled_apk") as install,
        ):
            for selection in ("xfce", "lxqt", "plasma", "weston"):
                provision.packages(selection)
        install.assert_not_called()
        self.assertEqual(
            provision.NATIVE_DESKTOP_ENVIRONMENTS["plasma"]["KWIN_FORCE_SW_CURSOR"], "1"
        )

    def test_live_apt_provision_never_suppresses_native_service_actions(self):
        for failing in (False, True):
            with self.subTest(install_failure=failing), tempfile.TemporaryDirectory() as directory:
                # A user policy is outside Sentinel's live package transaction:
                # it must remain byte-for-byte and mode-for-mode untouched even
                # when apt fails. With no policy, no temporary one is created.
                policy = Path(directory) / "policy-rc.d"
                policy.write_text("#!/bin/sh\n# administrator policy\nexit 0\n")
                policy.chmod(0o750)
                before = policy.stat()

                def run(command, **kwargs):
                    if command[0] == "apt-get" and "install" in command and failing:
                        raise subprocess.CalledProcessError(1, command)
                    return SimpleNamespace(stdout="", returncode=0)

                with (
                    patch.object(
                        provision.shutil,
                        "which",
                        side_effect=lambda name: name if name == "apt-get" else None,
                    ),
                    patch.object(provision, "Path", return_value=policy) as paths,
                    patch.object(provision.subprocess, "run", side_effect=run) as commands,
                ):
                    if failing:
                        with self.assertRaises(subprocess.CalledProcessError):
                            provision.packages("xfce")
                    else:
                        provision.packages("xfce")
                paths.assert_not_called()
                self.assertEqual(policy.read_text(), "#!/bin/sh\n# administrator policy\nexit 0\n")
                self.assertEqual(policy.stat().st_mode, before.st_mode)
                self.assertEqual(policy.stat().st_mtime_ns, before.st_mtime_ns)
                invocations = [call.args[0] for call in commands.call_args_list]
                self.assertEqual(invocations[0][0], "dpkg-query")
                self.assertEqual(invocations[1], ["apt-get", "-o", "Acquire::Retries=3", "update"])
                self.assertEqual(
                    invocations[2][:6],
                    [
                        "apt-get",
                        "-o",
                        "DPkg::Lock::Timeout=120",
                        "install",
                        "-y",
                        "--no-install-recommends",
                    ],
                )
                self.assertTrue(all(call.kwargs["check"] for call in commands.call_args_list[1:]))

    def test_only_alpine_xfce_seeds_stock_privileged_xorg_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "X11"
            for selection, distribution in (
                ("lxqt", "alpine"),
                ("xfce", "ubuntu"),
                ("xfce", "debian"),
            ):
                provision.configure_xorg_wrapper(selection, distribution, root)
                self.assertFalse(root.exists())
            provision.configure_xorg_wrapper("xfce", "alpine", root)
            path = root / "Xwrapper.config"
            self.assertIn("allowed_users=console\nneeds_root_rights=yes\n", path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            path.write_text("# explicit administrator policy\nneeds_root_rights=no\n")
            provision.configure_xorg_wrapper("xfce", "alpine", root)
            self.assertEqual(
                path.read_text(), "# explicit administrator policy\nneeds_root_rights=no\n"
            )

    def test_native_profiles_choose_one_audio_server_and_keep_pulse_clients(self):
        for common, profiles, audio in (
            (provision.COMMON_APK, provision.DESKTOP_APK, provision.NATIVE_AUDIO_APK),
            (provision.COMMON_APT, provision.DESKTOP_APT, provision.NATIVE_AUDIO_APT),
        ):
            for selection in provision.NATIVE_DESKTOP_ENVIRONMENTS:
                names = provision.package_names(selection, common, profiles, audio)
                self.assertTrue(
                    {"pipewire", "pipewire-pulse", "wireplumber", "pulseaudio-utils"}.issubset(
                        names
                    )
                )
                self.assertNotIn("pulseaudio", names)
            # The standalone compositor fixture still owns its isolated server.
            self.assertIn("pulseaudio", provision.package_names("weston", common, profiles, audio))

    def test_native_autostart_is_owned_idempotent_and_preserves_other_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom = root / "custom.desktop"
            custom.write_text("[Desktop Entry]\nExec=my-app\n")
            provision.configure_native_autostart(root)
            path = root / "sentinel-session.desktop"
            saved = path.read_text()
            self.assertIn("desktop-native-session.py publish", saved)
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            provision.configure_native_autostart(root)
            self.assertEqual(path.read_text(), saved)
            self.assertEqual(custom.read_text(), "[Desktop Entry]\nExec=my-app\n")

    def test_full_desktops_select_native_login_and_stock_session_commands(self):
        for selection, command, desktop in (
            ("gnome", "/usr/bin/gnome-session", "GNOME"),
            ("plasma", "/usr/bin/startplasma-wayland", "KDE"),
        ):
            with self.subTest(selection=selection), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                provision.configure(selection, root)
                path = root / "desktop.json"
                config = json.loads(path.read_text())
                self.assertEqual(config["command"], [command])
                self.assertEqual(config["session_manager"], "native")
                self.assertEqual(config["protocol"], "wayland")
                self.assertEqual(config["environment"]["XDG_CURRENT_DESKTOP"], desktop)
                self.assertEqual(config["environment"]["XDG_SESSION_TYPE"], "wayland")
                self.assertNotIn("output_command", config)
                for key in (
                    "WAYLAND_DISPLAY",
                    "DISPLAY",
                    "DBUS_SESSION_BUS_ADDRESS",
                    "XDG_RUNTIME_DIR",
                    "LIBSEAT_BACKEND",
                    "WLR_BACKENDS",
                ):
                    self.assertNotIn(key, config["environment"])
                custom = {**config, "command": ["my-session"], "environment": {"CUSTOM": "yes"}}
                path.write_text(json.dumps(custom))
                provision.configure(selection, root)
                self.assertEqual(json.loads(path.read_text()), custom)

    def test_xfce_runs_stock_rootless_xorg_inside_native_pam_login(self):
        for choices in (provision.DESKTOP_APK, provision.DESKTOP_APT):
            self.assertIn("xinit", choices["xfce"])
            self.assertNotIn("seatd", choices["xfce"])
            self.assertNotIn("xserver-xorg-legacy", choices["xfce"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provision.configure("xfce", root)
            config = json.loads((root / "desktop.json").read_text())
            self.assertEqual(config["session_manager"], "native")
            self.assertEqual(config["protocol"], "x11")
            self.assertEqual(
                config["command"],
                ["/usr/bin/startx", "/usr/bin/startxfce4", "--", "-keeptty", "-nolisten", "tcp"],
            )
            self.assertEqual(config["environment"]["XDG_CURRENT_DESKTOP"], "XFCE")
            self.assertEqual(config["environment"]["XDG_SESSION_TYPE"], "x11")
            self.assertNotIn("output_command", config)
            for key in ("DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"):
                self.assertNotIn(key, config["environment"])

    def test_all_normal_desktops_publish_actual_session_environment_via_autostart(self):
        self.assertEqual(
            set(provision.NATIVE_DESKTOP_ENVIRONMENTS), {"xfce", "lxqt", "gnome", "plasma"}
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provision.configure_native_autostart(root)
            contents = (root / "sentinel-session.desktop").read_text()
            self.assertIn("desktop-native-session.py publish", contents)
            self.assertNotIn("OnlyShowIn=", contents)
            self.assertNotIn("NotShowIn=", contents)
            for selection in provision.NATIVE_DESKTOP_ENVIRONMENTS:
                provision.configure(selection, root / selection)
                config = json.loads((root / selection / "desktop.json").read_text())
                self.assertEqual(config["session_manager"], "native")
                self.assertNotIn("dbus-run-session", config["command"])
                self.assertNotIn("seatd", config["command"])

    def test_full_desktops_install_shell_settings_files_and_terminal(self):
        for choices in (provision.DESKTOP_APK, provision.DESKTOP_APT):
            self.assertTrue(
                {
                    "gnome-shell",
                    "gnome-session",
                    "gnome-settings-daemon",
                    "gnome-control-center",
                    "gnome-terminal",
                    "nautilus",
                    "gnome-backgrounds",
                    "xdg-desktop-portal-gnome",
                }.issubset(choices["gnome"])
            )
            self.assertTrue(
                {
                    "plasma-desktop",
                    "plasma-workspace",
                    "dolphin",
                    "konsole",
                    "systemsettings",
                    "breeze",
                    "xdg-desktop-portal-kde",
                }.issubset(choices["plasma"])
            )
            for selection in ("gnome", "plasma"):
                self.assertIn("xwayland", choices[selection])
                self.assertNotIn("seatd", choices[selection])
                self.assertNotIn("wlr-randr", choices[selection])
        self.assertIn("gnome-shell-session", provision.DESKTOP_APK["gnome"])
        self.assertIn("gdm", provision.DESKTOP_APK["gnome"])
        self.assertIn("polkit-elogind", provision.DESKTOP_APK["gnome"])
        self.assertNotIn("gdm", provision.DESKTOP_APT["gnome"])
        self.assertIn("kwin", provision.DESKTOP_APK["plasma"])
        self.assertIn("kwin-wayland", provision.DESKTOP_APT["plasma"])

    def test_plasma_installs_native_output_command_owner_explicitly(self):
        # desktop_outputs invokes kscreen-doctor; do not rely on a shell's
        # incidental transitive dependencies supplying that executable.
        self.assertIn("libkscreen", provision.DESKTOP_APK["plasma"])
        self.assertIn("libkscreen-bin", provision.DESKTOP_APT["plasma"])

    def test_glibc_installs_public_glvnd_abi_and_standard_mesa_registration(self):
        self.assertTrue(
            {"libegl1", "libgl1", "libgles2", "libopengl0", "libegl-mesa0"}.issubset(
                provision.COMMON_APT
            )
        )
        self.assertIn("mesa-egl", provision.COMMON_APK)
        self.assertNotIn("libglvnd", provision.COMMON_APK)

    def test_clipboard_backend_is_selected_by_desktop_not_display_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            for selection, backend in (
                ("xfce", "x11"),
                ("gnome", "x11"),
                ("lxqt", "wayland"),
                ("plasma", "wayland"),
                ("weston", "wayland"),
            ):
                root = Path(directory) / selection
                provision.configure(selection, root)
                config = json.loads((root / "desktop.json").read_text())
                self.assertEqual(config["clipboard_backend"], backend)
            for choices in (provision.DESKTOP_APK, provision.DESKTOP_APT):
                self.assertIn("xclip", choices["gnome"])

    def test_native_custom_config_must_declare_clipboard_backend(self):
        for backend in (None, "automatic", ""):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                value = {
                    "session_manager": "native",
                    "protocol": "wayland",
                    "command": ["custom-session"],
                }
                if backend is not None:
                    value["clipboard_backend"] = backend
                (root / "desktop.json").write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError, "explicit.*clipboard_backend"):
                    provision.existing_configuration("gnome", root)

    def test_weston_installs_discoverable_application_and_file_tools(self):
        for choices in [provision.DESKTOP_APK, provision.DESKTOP_APT]:
            self.assertIn("xfce4-appfinder", choices["weston"])
            self.assertIn("thunar", choices["weston"])
        self.assertIn("libxkbcommon", provision.COMMON_APK)
        self.assertIn("libxkbcommon0", provision.COMMON_APT)
        self.assertTrue({"sudo", "shadow"}.issubset(provision.COMMON_APK))
        self.assertTrue({"sudo", "passwd"}.issubset(provision.COMMON_APT))
        self.assertIn("xauth", provision.COMMON_APK)
        self.assertIn("xauth", provision.COMMON_APT)

    def test_lxqt_uses_stock_full_desktop_and_compositor_owned_session(self):
        for choices in [provision.DESKTOP_APK, provision.DESKTOP_APT]:
            self.assertTrue(
                {
                    "labwc",
                    "lxqt-session",
                    "lxqt-panel",
                    "lxqt-runner",
                    "pcmanfm-qt",
                    "qterminal",
                    "wlr-randr",
                }.issubset(choices["lxqt"])
            )
            self.assertNotIn("openbox", choices["lxqt"])
            self.assertNotIn("seatd", choices["lxqt"])
        self.assertIn("qt6-qtsvg", provision.DESKTOP_APK["lxqt"])
        self.assertIn("qt6-svg-plugins", provision.DESKTOP_APT["lxqt"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provision.configure("lxqt", root)
            config = json.loads((root / "desktop.json").read_text())
            self.assertEqual(config["protocol"], "wayland")
            self.assertEqual(config["session_manager"], "native")
            self.assertEqual(config["command"], ["labwc", "-S", "lxqt-session"])
            self.assertEqual(config["environment"]["XDG_MENU_PREFIX"], "lxqt-")
            self.assertEqual(config["environment"]["QT_QPA_PLATFORM"], "wayland")
            self.assertEqual(config["environment"]["XDG_CURRENT_DESKTOP"], "LXQt:labwc")
            self.assertEqual(config["environment"]["XDG_CONFIG_DIRS"], "/etc:/etc/xdg:/usr/share")
            self.assertEqual(config["environment"]["XDG_DATA_DIRS"], "/usr/local/share:/usr/share")
            self.assertEqual(config["environment"]["LIBSEAT_BACKEND"], "logind")
            for key in (
                "WAYLAND_DISPLAY",
                "DISPLAY",
                "DBUS_SESSION_BUS_ADDRESS",
                "XDG_RUNTIME_DIR",
                "WLR_BACKENDS",
                "SEATD_SOCK",
            ):
                self.assertNotIn(key, config["environment"])
            self.assertNotIn("output_command", config)
            custom = {**config, "command": ["custom-session"], "output_command": []}
            (root / "desktop.json").write_text(json.dumps(custom))
            provision.configure("lxqt", root)
            self.assertEqual(json.loads((root / "desktop.json").read_text()), custom)

    def test_lxqt_selects_bundled_compositor_only_for_affected_distribution(self):
        for distribution, executable in (
            ("alpine", "/opt/sentinel/graphics/bin/labwc"),
            ("ubuntu", "labwc"),
            ("debian", "labwc"),
        ):
            with (
                self.subTest(distribution=distribution),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                provision.configure("lxqt", root, distribution=distribution)
                config = json.loads((root / "desktop.json").read_text())
                self.assertEqual(config["command"], [executable, "-S", "lxqt-session"])
                self.assertEqual(config["environment"]["LIBSEAT_BACKEND"], "logind")
                # Explicit user configuration remains authoritative. This is
                # installation-time selection, not executable probing/fallback.
                config["command"] = ["/custom/compositor", "--session"]
                (root / "desktop.json").write_text(json.dumps(config))
                provision.configure("lxqt", root, distribution=distribution)
                self.assertEqual(json.loads((root / "desktop.json").read_text()), config)
        self.assertEqual(provision.DESKTOP_COMMANDS["lxqt"], ["labwc", "-S", "lxqt-session"])

    def test_lxqt_apt_gate_checks_panel_and_session_and_accepts_new_candidates(self):
        def run(command, **_):
            if command[0] == "apt-cache":
                return SimpleNamespace(stdout="  Installed: (none)\n  Candidate: 2.4.0-1\n")
            self.assertEqual(command[0:2], ["dpkg", "--compare-versions"])
            self.assertEqual(command[-2:], ["ge", "2.1"])
            return SimpleNamespace(returncode=0)

        with patch.object(provision.subprocess, "run", side_effect=run) as commands:
            provision.require_lxqt_apt()
        queried = [
            item.args[0][-1] for item in commands.call_args_list if item.args[0][0] == "apt-cache"
        ]
        self.assertEqual(queried, ["lxqt-session", "lxqt-panel", "labwc"])

    def test_lxqt_apt_old_version_fails_before_any_install_or_session_changes(self):
        def run(command, **_):
            if command[0] == "apt-get":
                self.assertEqual(command[-1], "update")
                return SimpleNamespace(returncode=0)
            if command[0] == "apt-cache":
                return SimpleNamespace(stdout="  Candidate: 1.4.0-0ubuntu6\n")
            if command[0] == "dpkg":
                return SimpleNamespace(returncode=1)
            self.fail(f"Unexpected modifying operation: {command}")

        with (
            patch.object(
                provision.shutil,
                "which",
                side_effect=lambda name: name if name == "apt-get" else None,
            ),
            patch.object(provision.subprocess, "run", side_effect=run),
            patch.object(provision.Path, "write_text") as write,
            self.assertRaisesRegex(RuntimeError, "2.1 or newer.*1.4.0"),
        ):
            provision.packages("lxqt")
        write.assert_not_called()

    def test_lxqt_apt_unavailable_package_is_clear_error(self):
        with (
            patch.object(
                provision.subprocess,
                "run",
                return_value=SimpleNamespace(stdout="  Candidate: (none)\n"),
            ),
            self.assertRaisesRegex(
                RuntimeError, "Reinstall this workspace.*VM-only data is erased"
            ),
        ):
            provision.require_lxqt_apt()

    def test_lxqt_apk_requires_wayland_capable_versions_in_transaction(self):
        with (
            patch.object(provision.shutil, "which", return_value="apk"),
            patch.object(provision.subprocess, "run") as run,
        ):
            provision.packages("lxqt")
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["apk", "add", "--no-cache"])
        self.assertIn("lxqt-session>=2.1", command)
        self.assertIn("lxqt-panel>=2.1", command)

    def test_weston_seeds_regular_user_home_with_apps_files_and_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "home/sentinel/.config/weston.ini"
            provision.configure_weston(path)
            saved = path.read_text()
            self.assertEqual(saved.count("[launcher]"), 3)
            for program in ("xfce4-appfinder", "thunar", "weston-terminal"):
                self.assertIn(f"path=/usr/bin/{program}\n", saved)
            self.assertNotIn("binding-modifier=", saved)
            self.assertNotIn(".svg", saved)
            self.assertIn("/24x24/apps/org.xfce.appfinder.png", saved)
            self.assertIn("/16x16/apps/org.xfce.thunar.png", saved)
            provision.configure_weston(path)
            self.assertEqual(path.read_text(), saved)

    def test_weston_corrects_only_oversized_default_icons_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weston.ini"
            provision.configure_weston(path)
            saved = (
                path.read_text()
                .replace("/24x24/apps/", "/48x48/apps/")
                .replace("/16x16/apps/", "/48x48/apps/")
            )
            custom = "\n[launcher]\nicon=/custom/48x48/app.png\npath=/opt/custom-app\n"
            path.write_text(saved + custom)
            provision.configure_weston(path)
            corrected = path.read_text()
            self.assertIn("/24x24/apps/org.xfce.appfinder.png", corrected)
            self.assertIn("/16x16/apps/org.xfce.thunar.png", corrected)
            self.assertTrue(corrected.endswith(custom))
            self.assertEqual(corrected.count("[launcher]"), 4)
            provision.configure_weston(path)
            self.assertEqual(path.read_text(), corrected)

    def test_weston_adds_launchers_without_replacing_custom_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weston.ini"
            custom = (
                "# Keep the user's shell, bindings and launch arguments.\n"
                "[core]\nidle-time=300\n[shell]\npanel-position=bottom\n"
                "binding-modifier=super\n"
                "[launcher]\nicon=/custom/terminal.png\npath=/usr/bin/weston-terminal --shell=/bin/sh\n"
                "[launcher]\npath=GDK_BACKEND=wayland /usr/bin/xfce4-appfinder --collapsed\n"
                "[launcher]\npath=/opt/my-app --mode=work\n"
                "[output]\nname=Virtual-1\nmode=1920x1200\n"
                "[output]\nname=Other\nmode=1280x800"
            )
            path.write_text(custom)
            provision.configure_weston(path)
            saved = path.read_text()
            self.assertTrue(saved.startswith(custom + "\n"))
            self.assertEqual(saved.count("[launcher]"), 4)
            self.assertIn("path=/usr/bin/thunar\n", saved)
            provision.configure_weston(path)
            self.assertEqual(path.read_text(), saved)

    def test_weston_terminal_only_preferences_receive_missing_launchers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weston.ini"
            old = "[shell]\npanel-position=top\n[launcher]\npath=/usr/bin/weston-terminal\n"
            path.write_text(old)
            provision.configure_weston(path)
            saved = path.read_text()
            self.assertTrue(saved.startswith(old))
            self.assertEqual(saved.count("[launcher]"), 3)
            self.assertEqual(saved.count("path=/usr/bin/weston-terminal"), 1)

    def test_repeated_setup_preserves_agent_customization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provision.configure("xfce", root)
            config = root / "desktop.json"
            custom = {
                "protocol": "x11",
                "session_manager": "native",
                "clipboard_backend": "x11",
                "command": ["my-session"],
                "environment": {"CUSTOM": "yes"},
                "audio": False,
            }
            config.write_text(json.dumps(custom))
            provision.configure("xfce", root)
            self.assertEqual(json.loads(config.read_text()), custom)
            provision.configure("weston", root)
            self.assertEqual(json.loads(config.read_text())["protocol"], "wayland")
            self.assertEqual(json.loads((root / "desktop.previous.json").read_text()), custom)

    def test_native_custom_commands_are_preserved_without_compatibility_rewriting(self):
        for selection, command in provision.DESKTOP_COMMANDS.items():
            with self.subTest(selection=selection), tempfile.TemporaryDirectory() as directory:
                self.assertNotIn("dbus-run-session", command)
                root = Path(directory)
                provision.configure(selection, root)
                config = root / "desktop.json"
                saved = json.loads(config.read_text())
                saved["command"] = ["dbus-run-session", "--", *command]
                saved["environment"]["CUSTOM"] = "keep"
                config.write_text(json.dumps(saved))
                provision.configure(selection, root)
                self.assertEqual(json.loads(config.read_text()), saved)
                custom = {**saved, "command": ["dbus-run-session", "--", "custom-session"]}
                config.write_text(json.dumps(custom))
                provision.configure(selection, root)
                self.assertEqual(json.loads(config.read_text()), custom)

    def test_legacy_profile_requires_explicit_reinstall_and_remains_unchanged(self):
        for selection in provision.NATIVE_DESKTOP_ENVIRONMENTS:
            with self.subTest(selection=selection), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                saved = {
                    "protocol": "x11",
                    "command": ["custom-old-session"],
                    "environment": {"KEEP": "yes"},
                }
                (root / "desktop.json").write_text(json.dumps(saved))
                (root / "desktop-choice").write_text(selection + "\n")
                before = {file.name: file.read_bytes() for file in root.iterdir()}
                with self.assertRaisesRegex(RuntimeError, "Reinstall.*erases VM-only"):
                    provision.existing_configuration(selection, root)
                with self.assertRaisesRegex(RuntimeError, "Reinstall"):
                    provision.configure(selection, root)
                self.assertEqual(before, {file.name: file.read_bytes() for file in root.iterdir()})

    def test_clean_filesystem_after_explicit_reinstall_gets_native_contract_for_all_choices(self):
        # Reinstall rebuilds the VM filesystem; provisioning never erases it.
        for selection in provision.NATIVE_DESKTOP_ENVIRONMENTS:
            with self.subTest(selection=selection), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "etc/sentinel"
                self.assertEqual(provision.existing_configuration(selection, root), (None, None))
                provision.configure(selection, root)
                self.assertEqual(
                    json.loads((root / "desktop.json").read_text())["session_manager"], "native"
                )
                self.assertEqual((root / "desktop-choice").read_text(), selection + "\n")

    def test_desktop_packages_are_separate_from_tools_and_do_not_include_vnc(self):
        for common, choices in [
            (provision.COMMON_APK, provision.DESKTOP_APK),
            (provision.COMMON_APT, provision.DESKTOP_APT),
        ]:
            self.assertIn("socat", common)
            self.assertIn("pulseaudio", common)
            self.assertEqual(set(choices), {"xfce", "weston", "lxqt", "gnome", "plasma"})
            self.assertFalse(
                any("vnc" in package for package in common + sum(choices.values(), []))
            )
