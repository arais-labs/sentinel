"""Guest-source packaging is deterministic and independent of compilation."""

import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from guest_assets import package_desktop_runtime, publish_kernel


class GuestAssetsTests(unittest.TestCase):
    def test_native_package_inventory_is_versioned_without_sweeping_build_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            guest, graphics, native = root / "guest", root / "graphics", root / "native"
            for directory in (guest, graphics, native):
                directory.mkdir()
            package = native / "package.deb"
            package.write_bytes(b"native package")
            (native / "unverified-log").write_text("not shipped")
            assets = [("debian", native, (package,))]
            package_desktop_runtime(guest, graphics, assets)
            before = (graphics / "desktop-runtime.json").read_bytes()
            with tarfile.open(graphics / "desktop-runtime.tar.xz") as archive:
                self.assertEqual(
                    archive.getnames(), ["./native-packages/debian/package.deb", "./version"]
                )
                self.assertEqual(
                    archive.extractfile(archive.getmembers()[0]).read(), package.read_bytes()
                )
            package.write_bytes(b"changed native package")
            package_desktop_runtime(guest, graphics, assets)
            self.assertNotEqual(before, (graphics / "desktop-runtime.json").read_bytes())
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                package_desktop_runtime(guest, graphics, assets + assets)
            outside = root / "foreign"
            outside.write_bytes(b"outside")
            link = native / "link"
            link.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "escapes"):
                package_desktop_runtime(guest, graphics, [("debian", native, (link,))])

    def test_nested_session_assets_are_packaged_and_invalidate_session_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            guest, graphics = root / "guest", root / "graphics"
            asset = guest / "session-assets/gnome/extension.js"
            asset.parent.mkdir(parents=True)
            graphics.mkdir()
            asset.write_text("first")
            package_desktop_runtime(guest, graphics)
            before = json.loads((graphics / "desktop-runtime.json").read_text())
            with tarfile.open(graphics / "desktop-runtime.tar.xz") as bundle:
                self.assertEqual(
                    bundle.extractfile("./session-assets/gnome/extension.js").read(), b"first"
                )
            asset.write_text("second")
            package_desktop_runtime(guest, graphics)
            after = json.loads((graphics / "desktop-runtime.json").read_text())
            self.assertNotEqual(before["version"], after["version"])
            self.assertNotEqual(before["sha256"], after["sha256"])

    def test_session_payload_is_deterministic_and_includes_generated_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            guest, graphics = root / "guest", root / "graphics"
            guest.mkdir()
            graphics.mkdir()
            (guest / "desktop_modes.py").write_text("DESKTOP_REFRESH_HZ = 120\n")
            (guest / "install-guest.py").write_text("not a session module")
            package_desktop_runtime(guest, graphics)
            archive = graphics / "desktop-runtime.tar.xz"
            original = archive.read_bytes()
            with tarfile.open(archive) as bundle:
                self.assertEqual(bundle.getnames(), ["./desktop_modes.py", "./version"])
            metadata = json.loads((graphics / "desktop-runtime.json").read_text())
            self.assertEqual(metadata["sha256"], hashlib.sha256(original).hexdigest())
            stamp = archive.stat().st_mtime_ns
            package_desktop_runtime(guest, graphics)
            self.assertEqual(archive.read_bytes(), original)
            self.assertEqual(archive.stat().st_mtime_ns, stamp)
            archive.write_bytes(b"corrupted")
            package_desktop_runtime(guest, graphics)
            self.assertEqual(archive.read_bytes(), original)
            (guest / "desktop_modes.py").write_text("DESKTOP_REFRESH_HZ = 60\n")
            package_desktop_runtime(guest, graphics)
            changed = json.loads((graphics / "desktop-runtime.json").read_text())
            self.assertNotEqual(changed["version"], metadata["version"])

    def test_kernel_publication_preserves_previous_kernel_on_invalid_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            graphics = root / "graphics"
            graphics.mkdir()
            archive = graphics / "kernel-linux-arm64.tar.xz"
            with tarfile.open(archive, "w:xz") as bundle:
                for name, content in (("Image", b"kernel"), ("kernelrelease", b"test-release\n")):
                    info = tarfile.TarInfo("./" + name)
                    info.size = len(content)
                    bundle.addfile(info, io.BytesIO(content))
            metadata = publish_kernel(graphics)
            self.assertEqual(metadata["kernelRelease"], "test-release")
            self.assertEqual(metadata["kernelFileSha256"], hashlib.sha256(b"kernel").hexdigest())
            archive.write_bytes(b"broken")
            with self.assertRaises(tarfile.ReadError):
                publish_kernel(graphics)
            self.assertEqual((root / "kernel").read_bytes(), b"kernel")


if __name__ == "__main__":
    unittest.main()
