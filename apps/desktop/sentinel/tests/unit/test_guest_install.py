"""Archive validation and rollback use a disposable filesystem, not a VM."""

import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

source = Path(__file__).resolve().parents[2] / "native/graphics/guest/install-guest.py"
spec = importlib.util.spec_from_file_location("guest_install", source)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class GuestInstallTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.prefix = self.root / "opt/sentinel/graphics"
        self.prefix.mkdir(parents=True)
        (self.prefix / "version").write_text("previous")
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:xz") as bundle:
            value = b"next"
            entry = tarfile.TarInfo("version")
            entry.size = len(value)
            bundle.addfile(entry, io.BytesIO(value))
        self.archive = archive.getvalue()
        self.digest = hashlib.sha256(self.archive).hexdigest()

    def install(self, **changes):
        kwargs = dict(
            stream=io.BytesIO(self.archive),
            size=len(self.archive),
            digest=self.digest,
            version="next",
            kind="graphics",
            system=self.root,
        )
        installer.install(**{**kwargs, **changes})

    def test_invalid_archive_never_replaces_current_component(self):
        for changes, error in [
            ({"digest": "0" * 64}, "checksum"),
            ({"version": "wrong"}, "version"),
            ({"size": len(self.archive) + 10}, "interrupted"),
        ]:
            with self.subTest(changes=changes), self.assertRaisesRegex(RuntimeError, error):
                self.install(**changes)
            self.assertEqual((self.prefix / "version").read_text(), "previous")

    def test_success_registers_the_guest_loader(self):
        with patch.object(subprocess, "run") as run:
            self.install()
        self.assertEqual((self.prefix / "version").read_text(), "next")
        self.assertEqual((self.prefix / "bundle.sha256").read_text(), self.digest)
        run.assert_called_once_with([str(self.root / "sbin/ldconfig")], check=True)

    def test_musl_requires_native_packages_without_global_loader_override(self):
        (self.root / "lib").mkdir()
        (self.root / "lib/ld-musl-aarch64.so.1").touch()
        with self.assertRaisesRegex(RuntimeError, "Native Alpine Mesa"):
            self.install()
        self.assertFalse((self.root / "etc/ld-musl-aarch64.path").exists())
        self.assertEqual((self.prefix / "version").read_text(), "previous")

    def test_native_package_failure_retains_matching_assets_without_success_marker(self):
        (self.root / "lib").mkdir()
        (self.root / "lib/ld-musl-aarch64.so.1").touch()
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:xz") as bundle:
            for name, value in (("version", b"next"), ("packages/mesa/manifest.json", b"{}")):
                entry = tarfile.TarInfo(name)
                entry.size = len(value)
                bundle.addfile(entry, io.BytesIO(value))
        data = archive.getvalue()
        with patch.object(subprocess, "run", side_effect=subprocess.CalledProcessError(1, "apk")):
            with self.assertRaises(subprocess.CalledProcessError):
                self.install(
                    stream=io.BytesIO(data), size=len(data), digest=hashlib.sha256(data).hexdigest()
                )
        self.assertEqual((self.prefix / "version").read_text(), "next")
        self.assertFalse((self.prefix / "bundle.sha256").exists())
        self.assertFalse((self.root / "etc/ld-musl-aarch64.path").exists())

    def test_replacement_removes_old_public_gl_libraries_and_keeps_global_lookup(self):
        # A GLVND update must not leave the previous monolithic libEGL/libGL
        # shadowing the OS dispatchers, including in clients stripping LD_*.
        library = self.prefix / "lib"
        library.mkdir()
        for name in ("libEGL.so.1", "libGL.so.1"):
            (library / name).write_bytes(b"old monolithic ABI")
        with patch.object(subprocess, "run"):
            self.install()
        self.assertFalse((library / "libEGL.so.1").exists())
        self.assertFalse((library / "libGL.so.1").exists())
        loader = self.root / "etc/ld.so.conf.d/00-sentinel-graphics.conf"
        self.assertEqual(loader.read_text(), str(library) + "\n")

    def test_loader_failure_restores_previous_component_and_configuration(self):
        loader = self.root / "etc/ld.so.conf.d/00-sentinel-graphics.conf"
        loader.parent.mkdir(parents=True)
        loader.write_text("original\n")
        with patch.object(
            subprocess, "run", side_effect=[subprocess.CalledProcessError(1, "ldconfig"), None]
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                self.install()
        self.assertEqual((self.prefix / "version").read_text(), "previous")
        self.assertEqual(loader.read_text(), "original\n")
        self.assertEqual(list(self.prefix.parent.glob(".install-*")), [])
