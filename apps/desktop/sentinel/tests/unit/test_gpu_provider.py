"""Pure provider validation: no package downloads, ELF tools, or Linux guest."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import hashlib
import io
from unittest.mock import patch

path = Path(__file__).resolve().parents[2] / "scripts/packaging/graphics/package-gpu-provider.py"
spec = importlib.util.spec_from_file_location("gpu_provider", path)
provider = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provider)
installer_path = path.parents[3] / "native/graphics/guest/install-browser-graphics.py"
installer_spec = importlib.util.spec_from_file_location("gpu_provider_install", installer_path)
installer = importlib.util.module_from_spec(installer_spec)
installer_spec.loader.exec_module(installer)


class ProviderValidationTests(unittest.TestCase):
    header = "  Class:                             ELF64\n  Data: 2's complement, little endian\n  Machine:                           AArch64\n"
    dynamic = " 0x0000000000000001 (NEEDED) Shared library: [libc.so.6]\n 0x000000000000000e (SONAME) Library soname: [libexample.so.1]\n"

    def test_real_readelf_format_and_core24_versions(self):
        self.assertEqual(
            provider.elf_dependencies(
                self.header,
                self.dynamic,
                "Name: GLIBC_2.17 Flags: none\nName: GLIBC_2.39 Flags: none",
            ),
            ["libc.so.6"],
        )

    def test_wrong_architecture_and_abi_rejected(self):
        for old, new in [
            ("AArch64", "Advanced Micro Devices X86-64"),
            ("ELF64", "ELF32"),
            ("little endian", "big endian"),
        ]:
            with self.subTest(new=new), self.assertRaisesRegex(RuntimeError, "AArch64"):
                provider.elf_dependencies(self.header.replace(old, new), self.dynamic, "")
        with self.assertRaisesRegex(RuntimeError, "GLIBC"):
            provider.elf_dependencies(self.header, self.dynamic, "Name: GLIBC_2.40 Flags: none")

    def test_soname_and_needed_paths_rejected(self):
        for name in ["libexample.so.1", "libc.so.6"]:
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "name"):
                provider.elf_dependencies(self.header, self.dynamic.replace(name, "../" + name), "")

    def test_manifest_is_json_with_actual_trailing_newline(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "provider.json"
            provider.write_manifest(path, {"schema": 1, "identity": "fixture"})
            self.assertEqual(json.loads(path.read_bytes()), {"schema": 1, "identity": "fixture"})
            self.assertTrue(path.read_bytes().endswith(b"\n"))


class ProviderInstallTests(unittest.TestCase):
    def test_received_bytes_are_verified_before_install(self):
        contents = b"provider bytes"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "provider.snap"
            installer.receive(
                io.BytesIO(contents),
                destination,
                len(contents),
                hashlib.sha256(contents).hexdigest(),
            )
            self.assertEqual(destination.read_bytes(), contents)
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                installer.receive(io.BytesIO(contents), destination, len(contents), "0" * 64)
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                installer.receive(io.BytesIO(contents), destination, len(contents) + 1, "0" * 64)

    def test_only_connected_chromium_gpu_slot_is_reported(self):
        output = (
            "Interface Plug Slot Notes\n"
            "content[gpu-2404] chromium:gpu-2404 sentinel-gpu-2404:gpu-2404 manual\n"
            "content[gpu-2404] chromium:gpu-2404 - -\n"
            "content other:gpu-2404 mesa-2404:gpu-2404 -\n"
        )
        with patch.object(installer, "snap", return_value=output):
            self.assertEqual(installer.connections(), [installer.SLOT])

    def test_receipt_alone_does_not_skip_connection_or_version_check(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(installer, "ROOT", Path(temporary)),
        ):
            (Path(temporary) / "installed.json").write_text(
                json.dumps({"sha256": "a" * 64, "version": "1-test"})
            )
            with (
                patch.object(installer, "installed", return_value=True),
                patch.object(installer, "connections", return_value=[installer.SLOT]),
            ):
                self.assertTrue(installer.checked("a" * 64, "1-test"))
                self.assertFalse(installer.checked("b" * 64, "1-test"))
            with patch.object(installer, "installed", return_value=False):
                self.assertFalse(installer.checked("a" * 64, "1-test"))
            with (
                patch.object(installer, "installed", return_value=True),
                patch.object(installer, "connections", return_value=["mesa-2404:gpu-2404"]),
            ):
                self.assertFalse(installer.checked("a" * 64, "1-test"))


if __name__ == "__main__":
    unittest.main()
