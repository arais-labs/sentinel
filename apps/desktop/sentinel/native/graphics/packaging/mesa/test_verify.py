import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("mesa_verify", Path(__file__).with_name("verify.py"))
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class NativeMesaTests(unittest.TestCase):
    def test_development_package_has_real_pc_provides_and_exact_runtime_family(self):
        text = "pkgname = mesa-dev\npkgver = 26.1.6-r1\norigin = mesa\narch = aarch64\nprovides = pc:egl=26.1.6\n"
        text += "".join(
            f"depend = {name}=26.1.6-r1\n" for name in verify.FAMILY if name != "mesa-dev"
        )
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "test.apk"
            for value, valid in (
                (text, True),
                (text.replace("depend = mesa-gles=26.1.6-r1\n", ""), False),
            ):
                with tarfile.open(archive, "w:gz") as output:
                    entry = tarfile.TarInfo(".PKGINFO")
                    data = value.encode()
                    entry.size = len(data)
                    output.addfile(entry, io.BytesIO(data))
                if valid:
                    self.assertIn(
                        "mesa-gles=26.1.6-r1", verify.package_metadata(archive, "mesa-dev")
                    )
                else:
                    with self.assertRaises(ValueError):
                        verify.package_metadata(archive, "mesa-dev")

    def test_family_metadata_rejects_wrong_version_and_fake_provides(self):
        base = "pkgname = mesa-egl\npkgver = 26.1.6-r1\norigin = mesa\narch = aarch64\ndepend = mesa=26.1.6-r1\ndepend = mesa-gles=26.1.6-r1\n"
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "test.apk"
            for text, valid in (
                (base, True),
                (base.replace("pkgver = 26.1.6-r1", "pkgver = 26.1.6-r0"), False),
                (base.replace("depend = mesa=26.1.6-r1\n", ""), False),
                (base + "provides = mesa-dev=26.1.6-r1\n", False),
            ):
                with tarfile.open(archive, "w:gz") as output:
                    entry = tarfile.TarInfo(".PKGINFO")
                    data = text.encode()
                    entry.size = len(data)
                    output.addfile(entry, io.BytesIO(data))
                if valid:
                    self.assertIn("mesa=26.1.6-r1", verify.package_metadata(archive, "mesa-egl"))
                else:
                    with self.assertRaises(ValueError):
                        verify.package_metadata(archive, "mesa-egl")

    def test_stage_requires_real_gles1_and_native_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            elf = b"\x7fELF\x02\x01" + bytes(12) + (183).to_bytes(2, "little")
            for name in verify.REQUIRED:
                path = root / "usr/lib" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(elf)
            for name in (
                "EGL/egl.h",
                "GL/gl.h",
                "GLES/gl.h",
                "GLES2/gl2.h",
                "GLES3/gl3.h",
                "KHR/khrplatform.h",
                "gbm.h",
            ):
                path = root / "usr/include" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("/* header */")
            for name in ("dri", "egl", "gl", "glx", "gbm", "glesv1_cm", "glesv2"):
                path = root / "usr/lib/pkgconfig" / (name + ".pc")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("prefix=/usr\n")
            verify.validate_stage(root)
            gles1 = root / "usr/lib/libGLESv1_CM.so.1"
            gles1.unlink()
            with self.assertRaises(ValueError):
                verify.validate_stage(root)
            gles1.symlink_to("/usr/lib/libGLESv1_CM.so.1")
            with self.assertRaises(ValueError):
                verify.validate_stage(root)


if __name__ == "__main__":
    unittest.main()
