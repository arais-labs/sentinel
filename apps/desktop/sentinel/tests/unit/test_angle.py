"""Offline checks for verified, selective extraction of the ANGLE bundle."""

import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from angle import prepare_angle


class AngleBundleTests(unittest.TestCase):
    def bundle(self, work, *, omit_gles=False):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Libraries/libEGL.dylib", b"egl")
            if not omit_gles:
                archive.writestr("Libraries/libGLESv2.dylib", b"gles")
            archive.writestr("LICENSE", b"license")
            archive.writestr("LICENSES.chromium.html", b"notices")
            archive.writestr("../../outside.txt", b"must not be extracted")
        data = buffer.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        folder = work / "angle" / digest
        folder.mkdir(parents=True)
        (folder / "source.zip").write_bytes(data)
        return {
            "sourceSha256": digest,
            "sourceUrl": "https://example.invalid/angle.zip",
            "archivePrefix": "Libraries",
        }, folder

    @patch("angle.subprocess.run", side_effect=AssertionError("unexpected network access"))
    def test_verified_cache_extracts_only_libraries_and_licenses(self, _download):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            config, folder = self.bundle(work)
            self.assertEqual(prepare_angle(work, config), folder)
            self.assertEqual((folder / "libEGL.dylib").read_bytes(), b"egl")
            self.assertEqual((folder / "libGLESv2.dylib").read_bytes(), b"gles")
            self.assertTrue((folder / "ANGLE-LICENSES.chromium.html").is_file())
            self.assertFalse((work / "outside.txt").exists())
            (folder / "libEGL.dylib").write_bytes(b"corrupt extracted library")
            prepare_angle(work, config)
            self.assertEqual((folder / "libEGL.dylib").read_bytes(), b"egl")

    def test_corrupt_archive_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            config, folder = self.bundle(work)
            (folder / "source.zip").write_bytes(b"corrupt archive")
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                prepare_angle(work, config)
            self.assertFalse((folder / "source.zip").exists())
            self.assertFalse((folder / "libEGL.dylib").exists())

    def test_incomplete_archive_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            config, folder = self.bundle(work, omit_gles=True)
            with self.assertRaises(KeyError):
                prepare_angle(work, config)
            self.assertFalse((folder / "libEGL.dylib").exists())


if __name__ == "__main__":
    unittest.main()
