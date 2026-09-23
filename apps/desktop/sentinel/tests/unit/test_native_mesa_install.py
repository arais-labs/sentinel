import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

source = Path(__file__).resolve().parents[2] / "native/graphics/guest/install-native-mesa.py"
spec = importlib.util.spec_from_file_location("native_mesa_install", source)
mesa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mesa)


class NativeMesaInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "keys").mkdir()
        self.key = self.root / "keys/sentinel-mesa.rsa.pub"
        self.key.write_bytes(b"fixture key")
        self.repository = self.root / "repository-keys"
        self.repository.mkdir()
        (self.repository / "alpine.pub").write_bytes(b"repository key")
        self.manifest = dict(
            schema=1,
            distribution="alpine",
            architecture="aarch64",
            name="mesa",
            version="26.1.6-r1",
            key="keys/sentinel-mesa.rsa.pub",
            key_sha256=self.digest(self.key),
            packages=[],
        )
        for name in sorted(mesa.FAMILY):
            path = self.root / f"{name}-26.1.6-r1.apk"
            dependencies = [] if name == "mesa" else ["mesa=26.1.6-r1"]
            content = (
                f"pkgname = {name}\npkgver = 26.1.6-r1\narch = aarch64\n"
                + "".join(f"depend = {value}\n" for value in dependencies)
            ).encode()
            # Model APK's concatenated signature and control gzip streams.
            streams = []
            for filename, data in ((".SIGN.RSA.fixture", b"signature"), (".PKGINFO", content)):
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w:gz") as archive:
                    member = tarfile.TarInfo(filename)
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
                streams.append(stream.getvalue())
            path.write_bytes(b"".join(streams))
            self.manifest["packages"].append(
                dict(name=name, apk=path.name, sha256=self.digest(path), dependencies=dependencies)
            )
        self.save()
        release = patch.object(
            mesa.platform, "freedesktop_os_release", return_value={"ID": "alpine"}
        )
        release.start()
        self.addCleanup(release.stop)

    @staticmethod
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def save(self):
        (self.root / "manifest.json").write_text(json.dumps(self.manifest))

    def test_one_signed_family_transaction_with_scoped_trust_and_dependencies(self):
        def run(command, **kwargs):
            if "--keys-dir" in command:
                keys = Path(command[2])
                self.assertEqual((keys / "alpine.pub").read_bytes(), b"repository key")
                self.assertEqual((keys / self.key.name).read_bytes(), self.key.read_bytes())
                self.assertEqual(command[3], "add")
                self.assertEqual(len(command[4:]), 7)
                self.assertNotIn("--no-network", command)
                self.assertNotIn("--allow-untrusted", command)
                self.assertNotIn("--force", command)
            return subprocess.CompletedProcess(command, 0, stdout="aarch64\n")

        with patch.object(mesa.subprocess, "run", side_effect=run) as runner:
            mesa.install(self.root, self.repository)
        self.assertEqual(len(runner.call_args_list), 9)
        self.assertEqual(len(list(self.repository.iterdir())), 1)
        for call in runner.call_args_list[2:]:
            self.assertEqual(call.args[0][:3], ["apk", "info", "--installed"])
            self.assertTrue(call.args[0][3].endswith("=26.1.6-r1"))

    def test_corruption_rejected_before_any_apk_command(self):
        for asset in (self.key, self.root / self.manifest["packages"][-1]["apk"]):
            with self.subTest(asset=asset):
                original = asset.read_bytes()
                asset.write_bytes(b"damaged")
                with patch.object(mesa.subprocess, "run") as runner:
                    with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                        mesa.install(self.root, self.repository)
                    runner.assert_not_called()
                asset.write_bytes(original)

    def test_missing_family_and_wrong_dependencies_rejected(self):
        for change in (
            lambda: self.manifest["packages"].pop(),
            lambda: self.manifest["packages"][0].update(dependencies=["wrong=1"]),
        ):
            original = json.loads(json.dumps(self.manifest))
            change()
            self.save()
            with patch.object(mesa.subprocess, "run") as runner:
                with self.assertRaises(ValueError):
                    mesa.install(self.root, self.repository)
                runner.assert_not_called()
            self.manifest = original

    def test_escaping_key_rejected(self):
        self.manifest["key"] = "keys/../../outside.pub"
        self.save()
        with patch.object(mesa.subprocess, "run") as runner:
            with self.assertRaises(ValueError):
                mesa.install(self.root, self.repository)
            runner.assert_not_called()

    def test_wrong_native_architecture_never_installs(self):
        with patch.object(
            mesa.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="x86_64")
        ) as runner:
            with self.assertRaisesRegex(ValueError, "native aarch64"):
                mesa.install(self.root, self.repository)
        self.assertEqual(runner.call_count, 1)

    def test_apk_failure_propagates_without_force_or_retry(self):
        with patch.object(
            mesa.subprocess,
            "run",
            side_effect=[
                subprocess.CompletedProcess([], 0, stdout="aarch64"),
                subprocess.CalledProcessError(1, ["apk", "add"]),
            ],
        ) as runner:
            with self.assertRaises(subprocess.CalledProcessError):
                mesa.install(self.root, self.repository)
        self.assertEqual(runner.call_count, 2)


if __name__ == "__main__":
    unittest.main()
