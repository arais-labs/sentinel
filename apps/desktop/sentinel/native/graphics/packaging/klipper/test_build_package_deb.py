import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "build_package_deb", Path(__file__).with_name("build-package-deb.py")
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class DebBuildTests(unittest.TestCase):
    pin = {
        "release": "13",
        "architecture": "arm64",
        "package": "libklipper6",
        "sourceVersion": "4:6.3.6-2",
    }
    image = "docker.io/library/debian@sha256:" + "a" * 64

    def test_build_log_is_streamed_and_nonzero_exit_remains_failure(self):
        with tempfile.TemporaryDirectory() as directory, io.TextIOWrapper(io.BytesIO()) as output:
            log = Path(directory) / "build.log"
            with patch.object(builder.sys, "stdout", output):
                with self.assertRaises(subprocess.CalledProcessError) as failure:
                    builder.logged(
                        log,
                        sys.executable,
                        "-c",
                        "print('compile failed', flush=True); raise SystemExit(3)",
                    )
            self.assertEqual(failure.exception.returncode, 3)
            self.assertEqual(log.read_bytes(), b"compile failed\n")
            self.assertEqual(output.buffer.getvalue(), log.read_bytes())

    def test_builder_must_match_distro_release_native_arch_and_ordinary_user(self):
        valid = (
            "debian",
            self.pin,
            {"ID": "debian", "VERSION_ID": "13"},
            "arm64",
            self.image,
            1000,
        )
        builder.validate_builder(*valid)
        for index, value in (
            (0, "ubuntu"),
            (2, {"ID": "debian", "VERSION_ID": "14"}),
            (3, "amd64"),
            (4, "debian:13"),
            (5, 0),
        ):
            with self.subTest(index=index):
                arguments = list(valid)
                arguments[index] = value
                with self.assertRaises(ValueError):
                    builder.validate_builder(*arguments)

    def test_dependency_comparison_preserves_exact_qt_private_abi(self):
        original = {
            name: ""
            for name in ("Pre-Depends", "Provides", "Conflicts", "Breaks", "Replaces", "Multi-Arch")
        }
        original.update(
            Package="libklipper6",
            Version="4:6.3.6-2",
            Architecture="arm64",
            Depends="libc6 (>= 2.34), qt6-base-private-abi (= 6.8.2)",
        )
        current = {
            **original,
            "Version": "4:6.3.6-2+sentinel1",
            "Depends": "qt6-base-private-abi (= 6.8.2), libc6 (>= 2.34)",
        }
        builder.verify_package(current, original, self.pin)
        for field, value in (
            ("Depends", "libc6 (>= 2.34), qt6-base-private-abi (= 6.9.0)"),
            ("Architecture", "amd64"),
            ("Package", "plasma-workspace"),
            ("Version", "4:6.3.6-2"),
            ("Breaks", "other-library"),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                builder.verify_package({**current, field: value}, original, self.pin)

    def test_component_key_tracks_recipe_toolchain_source_and_declared_image(self):
        with tempfile.TemporaryDirectory() as directory:
            recipe = Path(directory) / "recipe.sh"
            recipe.write_text("build")
            files = {"recipe.sh": recipe}
            toolchain = {"compiler": "compiler 1", "packages": ["qt 6.8.2"]}
            initial, record = builder.identity(self.pin, files, toolchain, self.image)
            self.assertEqual(record["declared_builder_image"], self.image)
            self.assertEqual(builder.identity(self.pin, files, toolchain, self.image)[0], initial)
            for pin, tools, image in (
                ({**self.pin, "sourceVersion": "new"}, toolchain, self.image),
                (self.pin, {**toolchain, "compiler": "compiler 2"}, self.image),
                (self.pin, {**toolchain, "packages": ["qt 6.9.0"]}, self.image),
                (self.pin, toolchain, self.image.replace("a" * 64, "b" * 64)),
            ):
                self.assertNotEqual(builder.identity(pin, files, tools, image)[0], initial)
            recipe.write_text("changed build flags")
            self.assertNotEqual(
                builder.identity(self.pin, files, toolchain, self.image)[0], initial
            )

    def test_payload_inventory_keeps_file_types_links_and_native_elf(self):
        def archive(machine=183, target="libklipper.so.6.3.6", duplicate=False):
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode="w") as bundle:
                library = tarfile.TarInfo("./usr/lib/aarch64-linux-gnu/libklipper.so.6.3.6")
                header = (
                    b"\x7fELF\x02\x01"
                    + bytes(10)
                    + (3).to_bytes(2, "little")
                    + machine.to_bytes(2, "little")
                )
                library.size = len(header)
                bundle.addfile(library, io.BytesIO(header))
                if duplicate:
                    bundle.addfile(library, io.BytesIO(header))
                link = tarfile.TarInfo("./usr/lib/aarch64-linux-gnu/libklipper.so.6")
                link.type, link.linkname = tarfile.SYMTYPE, target
                bundle.addfile(link)
            return data.getvalue()

        inventory = builder.payload_inventory(archive())
        self.assertEqual(
            inventory["usr/lib/aarch64-linux-gnu/libklipper.so.6"],
            ("symlink", "libklipper.so.6.3.6"),
        )
        self.assertNotEqual(builder.payload_inventory(archive(target="foreign.so")), inventory)
        for data in (archive(machine=62), archive(duplicate=True)):
            with self.assertRaises(ValueError):
                builder.payload_inventory(data)

    def test_package_only_edits_reuse_compiler_identity_with_fresh_package_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            files = {name: Path(directory) / name for name in builder.INPUTS}
            for name, path in files.items():
                path.write_text(name)
            tools = {"packages": ["qt 6.8.2"], "executables": {"c++": {"sha256": "a" * 64}}}
            source_key = "b" * 64
            environment = {"DEB_BUILD_MAINT_OPTIONS": "hardening=+all"}
            compile_key, record = builder.compilation_identity(
                source_key, files, tools, self.image, environment
            )
            package_key, package_record = builder.identity(self.pin, files, tools, self.image)
            self.assertEqual(set(record["inputs"]), set(builder.COMPILATION_INPUTS))
            self.assertEqual(record["source_key"], source_key)
            for name in builder.PACKAGING_INPUTS:
                with self.subTest(name=name):
                    files[name].write_text(name + " packaging-only change")
                    current, current_record = builder.identity(self.pin, files, tools, self.image)
                    self.assertNotEqual(current, package_key)
                    self.assertNotEqual(
                        current_record["inputs"][name], package_record["inputs"][name]
                    )
                    self.assertEqual(
                        builder.compilation_identity(
                            source_key, files, tools, self.image, environment
                        )[0],
                        compile_key,
                    )
                    files[name].write_text(name)

    def test_compiler_identity_invalidates_source_build_toolchain_and_environment_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "build-deb.sh"
            script.write_text("build flags and targets")
            files = {"build-deb.sh": script}
            tools = {
                "packages": ["qt 6.8.2"],
                "flags": "-O2",
                "executables": {"c++": {"sha256": "a" * 64}},
            }
            environment = {"DEB_BUILD_MAINT_OPTIONS": "hardening=+all"}
            arguments = ("b" * 64, files, tools, self.image, environment)
            initial = builder.compilation_identity(*arguments)[0]
            for index, changed in (
                (0, "c" * 64),
                (2, {**tools, "packages": ["qt 6.9.0"]}),
                (2, {**tools, "flags": "-O3"}),
                (2, {**tools, "executables": {"c++": {"sha256": "d" * 64}}}),
                (3, self.image.replace("a" * 64, "e" * 64)),
                (4, {"DEB_BUILD_MAINT_OPTIONS": "hardening=none"}),
            ):
                with self.subTest(index=index, changed=changed):
                    updated = list(arguments)
                    updated[index] = changed
                    self.assertNotEqual(builder.compilation_identity(*updated)[0], initial)
            script.write_text("different flags or target list")
            self.assertNotEqual(builder.compilation_identity(*arguments)[0], initial)
            for invalid in ("", "legacy", "b" * 63):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    builder.compilation_identity(invalid, files, tools, self.image, environment)


if __name__ == "__main__":
    unittest.main()
