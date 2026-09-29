"""Native package contract tests; no real builds, packages or guest required."""

import gzip
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "verify_alpine", Path(__file__).with_name("verify-alpine.py")
)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def elf():
    value = bytearray(64)
    value[:6] = b"\x7fELF\x02\x01"
    value[16:18] = (3).to_bytes(2, "little")
    value[18:20] = (183).to_bytes(2, "little")
    return bytes(value)


def entries():
    result = []
    for name in (
        "batterycontrol",
        "kfontinst",
        "kfontinstui",
        "klipper",
        "klookandfeel",
        "kmpris",
        "kworkspace6",
        "notificationmanager",
        "taskmanager",
    ):
        real = f"lib{name}.so.6.6.6"
        record = tarfile.TarInfo(f"usr/lib/{real}")
        record.size = len(elf())
        result.append((record, elf()))
        record = tarfile.TarInfo(
            f"usr/lib/lib{name}.so.{1 if name == 'notificationmanager' else 6}"
        )
        record.type = tarfile.SYMTYPE
        record.linkname = real
        result.append((record, None))
    return result


def member_archive(records):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for record, content in records:
            archive.addfile(record, io.BytesIO(content) if content is not None else None)
    return gzip.compress(stream.getvalue())


def readelf(command, **kwargs):
    filename = Path(command[-1]).name
    stem = filename.removesuffix(".6.6.6")
    soname = stem + (".1" if stem == "libnotificationmanager.so" else ".6")
    return f" 0x000000000000000e (SONAME) Library soname: [{soname}]\n"


class NativeLibraryContract(unittest.TestCase):
    def check_archive(self, records, dynamic=readelf):
        # Exercise APK-style concatenated gzip streams, including tar padding.
        signature = tarfile.TarInfo(".SIGN.RSA.builder.rsa.pub")
        control = tarfile.TarInfo(".PKGINFO")
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "candidate.apk"
            archive.write_bytes(
                member_archive([(signature, b"")])
                + member_archive([(control, b"")])
                + member_archive(records)
            )
            with patch.object(verifier.subprocess, "check_output", side_effect=dynamic):
                return verifier.libraries(archive)

    def test_complete_owner_and_notificationmanager_soname(self):
        self.assertEqual(len(self.check_archive(entries())), 18)

    def test_wrong_link_destination(self):
        for destination in (
            "missing.so",
            "/usr/lib/libbatterycontrol.so.6.6.6",
            "../../elsewhere.so",
        ):
            with self.subTest(destination=destination):
                records = entries()
                records[1][0].linkname = destination
                with self.assertRaisesRegex(ValueError, "Wrong SONAME symlink"):
                    self.check_archive(records)

    def test_soname_path_must_be_symlink(self):
        for kind in (tarfile.REGTYPE, tarfile.LNKTYPE):
            with self.subTest(kind=kind):
                records = entries()
                records[1][0].type = kind
                with self.assertRaisesRegex(ValueError, "Wrong SONAME symlink"):
                    self.check_archive(records)

    def test_real_library_must_be_regular(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            with self.subTest(kind=kind):
                records = entries()
                record = records[0][0]
                record.type, record.linkname, record.size = kind, "other.so", 0
                records[0] = (record, None)
                with self.assertRaisesRegex(ValueError, "Expected regular library"):
                    self.check_archive(records)

    def test_elf_requires_one_exact_soname(self):
        for value in (
            "",
            "(SONAME) Library soname: [libwrong.so.6]",
            "(SONAME) [libbatterycontrol.so.6]\n(SONAME) [libbatterycontrol.so.6]",
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "Wrong ELF SONAME"):
                    self.check_archive(entries(), dynamic=lambda *a, **kw: value)

    def test_missing_and_duplicate_libraries(self):
        for records in (entries()[1:], entries() + entries()[:1]):
            with self.assertRaisesRegex(ValueError, "payload changed or duplicated"):
                self.check_archive(records)

    def test_foreign_elf_rejected(self):
        records = entries()
        value = bytearray(elf())
        value[18:20] = (62).to_bytes(2, "little")
        records[0] = (records[0][0], bytes(value))
        with self.assertRaisesRegex(ValueError, "Expected aarch64 shared ELF"):
            self.check_archive(records)


if __name__ == "__main__":
    unittest.main()
