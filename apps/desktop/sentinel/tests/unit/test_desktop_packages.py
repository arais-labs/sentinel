import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

source = Path(__file__).resolve().parents[2] / "native/graphics/guest/desktop_packages.py"
spec = importlib.util.spec_from_file_location("desktop_packages_test", source)
packages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packages)


class DesktopPackagesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "localsearch"
        (self.directory / "keys").mkdir(parents=True)
        self.apk = self.directory / "localsearch-3.11.1-r1.apk"
        self.key = self.directory / "keys/sentinel-localsearch.rsa.pub"
        self.apk.write_bytes(b"signed package fixture")
        self.key.write_bytes(b"public key fixture")
        self.manifest = dict(
            schema=1,
            distribution="alpine",
            architecture="aarch64",
            name="localsearch",
            version="3.11.1-r1",
            apk=self.apk.name,
            key="keys/" + self.key.name,
            sha256=hashlib.sha256(self.apk.read_bytes()).hexdigest(),
            key_sha256=hashlib.sha256(self.key.read_bytes()).hexdigest(),
        )
        self.save()

    def save(self):
        (self.directory / "manifest.json").write_text(json.dumps(self.manifest))

    def test_signature_scope_offline_install_and_package_specific_unpin(self):
        with patch.object(packages.subprocess, "run") as run:
            packages.install_bundled_apk("localsearch", self.root)
        command = [
            "apk",
            "--keys-dir",
            str(self.directory / "keys"),
            "--no-network",
            "--repositories-file",
            "/dev/null",
            "add",
        ]
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                [*command, str(self.apk)],
                [*command, "localsearch"],
                ["apk", "info", "--installed", "localsearch=3.11.1-r1"],
            ],
        )
        self.assertTrue(all(call.kwargs["check"] for call in run.call_args_list))

    def test_damaged_package_or_key_is_rejected_before_apk(self):
        for field in ("sha256", "key_sha256"):
            with self.subTest(field=field), patch.object(packages.subprocess, "run") as run:
                previous = self.manifest[field]
                self.manifest[field] = "0" * 64
                self.save()
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    packages.install_bundled_apk("localsearch", self.root)
                run.assert_not_called()
                self.manifest[field] = previous

    def test_incompatible_or_escaping_manifest_is_rejected(self):
        for field, value in (
            ("schema", 2),
            ("distribution", "ubuntu"),
            ("architecture", "x86_64"),
            ("name", "other"),
            ("version", "--flag"),
            ("apk", "../package.apk"),
            ("key", "keys/../../outside.pub"),
        ):
            with self.subTest(field=field), patch.object(packages.subprocess, "run") as run:
                previous = self.manifest[field]
                self.manifest[field] = value
                self.save()
                with self.assertRaises(ValueError):
                    packages.install_bundled_apk("localsearch", self.root)
                run.assert_not_called()
                self.manifest[field] = previous

    def test_dependency_failure_is_not_hidden_or_retried(self):
        with patch.object(
            packages.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "apk")
        ) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                packages.install_bundled_apk("localsearch", self.root)
            run.assert_called_once()


class DesktopDebPackagesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "libklipper6"
        self.directory.mkdir()
        self.deb = self.directory / "libklipper6_6.3.6-2+sentinel1_arm64.deb"
        self.deb.write_bytes(b"native DEB fixture")
        self.manifest = dict(
            schema=1,
            distribution="debian",
            release="13",
            architecture="arm64",
            name="libklipper6",
            version="4:6.3.6-2+sentinel1",
            deb=self.deb.name,
            dependencies="qt6-base-private-abi (= 6.8.2)",
            qualified=False,
            sha256=hashlib.sha256(self.deb.read_bytes()).hexdigest(),
        )
        self.release = {"ID": "debian", "VERSION_ID": "13"}
        self.save()

    def save(self):
        (self.directory / "manifest.json").write_text(json.dumps(self.manifest))

    def outputs(self):
        values = [
            "arm64",
            "libklipper6",
            self.manifest["version"],
            "arm64",
            self.manifest["dependencies"],
            "",
            f'install ok installed\nlibklipper6\n{self.manifest["version"]}\narm64',
        ]
        return [subprocess.CompletedProcess([], 0, stdout=value) for value in values]

    def test_native_install_checks_control_and_installed_state_without_apt_or_pinning(self):
        for distro, release in (("debian", "13"), ("ubuntu", "26.04")):
            self.manifest.update(distribution=distro, release=release)
            self.save()
            with (
                self.subTest(distro=distro),
                patch.object(
                    packages.platform,
                    "freedesktop_os_release",
                    return_value={"ID": distro, "VERSION_ID": release},
                ),
                patch.object(packages.subprocess, "run", side_effect=self.outputs()) as run,
            ):
                packages.install_bundled_deb("libklipper6", self.root)
                commands = [call.args[0] for call in run.call_args_list]
                self.assertEqual(commands[0], ["dpkg", "--print-architecture"])
                self.assertEqual(
                    commands[1:5],
                    [
                        ["dpkg-deb", "--field", str(self.deb), field]
                        for field in ("Package", "Version", "Architecture", "Depends")
                    ],
                )
                self.assertEqual(commands[5], ["dpkg", "--install", str(self.deb.resolve())])
                self.assertEqual(
                    commands[6],
                    [
                        "dpkg-query",
                        "--show",
                        "--showformat=${Status}\n${Package}\n${Version}\n${Architecture}",
                        "libklipper6",
                    ],
                )
                self.assertTrue(all(call.kwargs["check"] for call in run.call_args_list))

    def test_bad_manifest_and_checksum_rejected_before_any_package_command(self):
        for field, value in (
            ("distribution", "ubuntu"),
            ("release", "14"),
            ("architecture", "amd64"),
            ("schema", 2),
            ("name", "foreign"),
            ("version", "--flag"),
            ("deb", "../foreign.deb"),
            ("sha256", "0" * 64),
            ("dependencies", None),
        ):
            original = dict(self.manifest)
            self.manifest[field] = value
            self.save()
            with (
                self.subTest(field=field),
                patch.object(
                    packages.platform, "freedesktop_os_release", return_value=self.release
                ),
                patch.object(packages.subprocess, "run") as run,
            ):
                with self.assertRaises(ValueError):
                    packages.install_bundled_deb("libklipper6", self.root)
                run.assert_not_called()
            self.manifest = original

    def test_escaping_package_symlink_rejected(self):
        external = self.root / "outside.deb"
        self.deb.rename(external)
        self.deb.symlink_to(external)
        with (
            patch.object(packages.platform, "freedesktop_os_release", return_value=self.release),
            patch.object(packages.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "escapes"):
                packages.install_bundled_deb("libklipper6", self.root)
            run.assert_not_called()

    def test_wrong_native_arch_or_control_is_rejected_before_install(self):
        for index, value in (
            (0, "amd64"),
            (1, "foreign"),
            (2, "4:6.3.6-2"),
            (3, "all"),
            (4, "qt6-base-private-abi (= 6.9.0)"),
        ):
            responses = self.outputs()
            responses[index].stdout = value
            with (
                self.subTest(index=index),
                patch.object(
                    packages.platform, "freedesktop_os_release", return_value=self.release
                ),
                patch.object(packages.subprocess, "run", side_effect=responses) as run,
            ):
                with self.assertRaises(ValueError):
                    packages.install_bundled_deb("libklipper6", self.root)
                self.assertEqual(run.call_count, index + 1)

    def test_install_failure_not_retried_and_incomplete_installed_state_rejected(self):
        for result in (
            subprocess.CalledProcessError(1, "dpkg"),
            subprocess.CompletedProcess(
                [], 0, stdout="install ok unpacked\nlibklipper6\n4:6.3.6-2+sentinel1\narm64"
            ),
        ):
            responses = self.outputs()
            responses[5 if isinstance(result, Exception) else 6] = result
            with (
                self.subTest(result=result),
                patch.object(
                    packages.platform, "freedesktop_os_release", return_value=self.release
                ),
                patch.object(packages.subprocess, "run", side_effect=responses) as run,
            ):
                with self.assertRaises(
                    subprocess.CalledProcessError if isinstance(result, Exception) else ValueError
                ):
                    packages.install_bundled_deb("libklipper6", self.root)
                self.assertEqual(run.call_count, 6 if isinstance(result, Exception) else 7)


if __name__ == "__main__":
    unittest.main()
