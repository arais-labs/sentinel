"""Host-only transport checks; never downloads, builds, or qualifies a package."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shlex
import shutil
import tarfile
import tempfile
import unittest

DESKTOP = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "klipper_target", DESKTOP / "scripts/packaging/graphics/klipper.py"
)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class KlipperTargetTests(unittest.TestCase):
    def fixture(self, root, distribution="debian", mutate=None, reseal=False):
        target = adapter.KlipperTarget(DESKTOP, root / "cache", distribution)
        # Small synthetic upstream bytes, not a claim about real source archives.
        target.pin = dict(
            target.pin,
            files={"plasma-workspace_fixture.tar.xz": hashlib.sha256(b"upstream").hexdigest()},
        )
        output = root / "klipper-output"
        package = output / "packages/libklipper6/libklipper6_fixture_arm64.deb"
        package.parent.mkdir(parents=True)
        package.write_bytes(b"fixture DEB transport bytes")
        source = output / f"share/sources/klipper-{distribution}/corresponding-source.tar.xz"
        source.parent.mkdir(parents=True)
        identity = dict(
            schema=1,
            source=target.pin,
            inputs=target.inputs,
            toolchain={"packages": ["fixture"], "executables": {}},
            declared_builder_image=target.image,
        )
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        source_key = "b" * 64
        compilation = dict(
            schema=1,
            source_key=source_key,
            inputs=target.compilation_inputs,
            toolchain=identity["toolchain"],
            declared_builder_image=target.image,
            environment={"DEB_BUILD_MAINT_OPTIONS": "hardening=+all"},
        )
        compile_key = hashlib.sha256(json.dumps(compilation, sort_keys=True).encode()).hexdigest()
        control = dict(
            Package="libklipper6",
            Version=target.pin["sourceVersion"] + "+sentinel1",
            Architecture="arm64",
            Depends="fixture",
        )
        provenance = dict(
            identity,
            component_key=key,
            qualified=False,
            package_control=control,
            source_key=source_key,
            compile_key=compile_key,
            compilation=compilation,
            regression={"startuprestore": "passed", "user_uid": 1000},
        )
        if mutate:
            mutate(provenance)
        if reseal:
            compile_key = hashlib.sha256(
                json.dumps(provenance["compilation"], sort_keys=True).encode()
            ).hexdigest()
            provenance["compile_key"] = compile_key
        members = {"recipe/" + name: path.read_bytes() for name, path in target.files.items()}
        members["recipe/sources.lock.json"] = json.dumps({distribution: target.pin}).encode()
        members["upstream/plasma-workspace_fixture.tar.xz"] = b"upstream"
        members["provenance/provenance.json"] = json.dumps(provenance).encode()
        for name in (
            "packaging/changelog",
            "provenance/build.log",
            "provenance/regression.log",
            "provenance/package.log",
        ):
            members[name] = b"fixture evidence"
        with tarfile.open(source, "w:xz") as archive:
            for name, data in members.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        manifest = package.parent / "manifest.json"
        manifest.write_text(
            json.dumps(
                dict(
                    schema=1,
                    distribution=distribution,
                    release=target.pin["release"],
                    architecture="arm64",
                    name="libklipper6",
                    version=control["Version"],
                    deb=package.name,
                    sha256=adapter.sha(package),
                    component_key=key,
                    declared_builder_image=target.image,
                    source_key=source_key,
                    compile_key=compile_key,
                    source=str(source.relative_to(output)),
                    source_sha256=adapter.sha(source),
                    dependencies="fixture",
                    regression="passed",
                    qualified=False,
                )
            )
        )
        return target, output, package, source, manifest

    def test_native_inputs_and_distro_commands(self):
        for distribution in ("debian", "ubuntu"):
            with (
                self.subTest(distribution=distribution),
                tempfile.TemporaryDirectory() as temporary,
            ):
                target = adapter.KlipperTarget(DESKTOP, Path(temporary), distribution)
                output = Path(temporary) / "staging with spaces"
                target.stage(output)
                self.assertEqual(
                    {p.name for p in (output / "klipper-inputs").iterdir()}, set(target.files)
                )
                command = shlex.split(
                    target.commands(output, test_user="sentinel", jobs=3).splitlines()[1]
                )
                self.assertEqual(command[2], distribution)
                for flag, value in [
                    ("--builder-image", target.image),
                    ("--test-user", "sentinel"),
                    ("--jobs", "3"),
                    ("--ccache", "/var/cache/sentinel-build/ccache"),
                ]:
                    self.assertEqual(command[command.index(flag) + 1], value)
                self.assertEqual(
                    "debian-arm64-symbols.patch" in target.files, distribution == "debian"
                )

    def test_invalid_target_and_commands(self):
        with self.assertRaises(ValueError):
            adapter.KlipperTarget(DESKTOP, Path("/unused"), "alpine")
        target = adapter.KlipperTarget(DESKTOP, Path("/unused"), "debian")
        for user, jobs in [("root", 1), ("", 1), ("sentinel", 0), ("sentinel", True)]:
            with self.subTest(user=user, jobs=jobs), self.assertRaises(ValueError):
                target.commands(Path("/stage"), test_user=user, jobs=jobs)

    def test_verified_output_published_unchanged_manifest_last(self):
        for distribution in ("debian", "ubuntu"):
            with (
                self.subTest(distribution=distribution),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                target, output, package, source, manifest = self.fixture(root, distribution)
                self.assertEqual(target.verify(output), (package, source, manifest))
                published = []

                def publish(original, destination):
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, destination)
                    published.append(destination)

                target.record(root, publish)
                self.assertEqual(published[-1].name, "manifest.json")
                self.assertEqual(published[-1].read_bytes(), manifest.read_bytes())
                self.assertIs(json.loads(published[-1].read_text())["qualified"], False)

    def test_reject_manifest_mismatch_before_publication(self):
        changes = dict(
            distribution="ubuntu",
            release="wrong",
            architecture="amd64",
            qualified=True,
            component_key="0" * 64,
            source_key="0" * 64,
            compile_key="0" * 64,
            declared_builder_image="foreign",
            deb="../foreign.deb",
            source="/foreign.tar.xz",
            sha256="0" * 64,
            source_sha256="0" * 64,
        )
        for field, value in changes.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                target, _, _, _, manifest = self.fixture(root)
                metadata = json.loads(manifest.read_text())
                metadata[field] = value
                manifest.write_text(json.dumps(metadata))
                with self.assertRaises(ValueError):
                    target.record(root, lambda *_: self.fail("Published invalid output"))

    def test_changed_recipe_or_upstream_identity_rejected(self):
        for change in ("recipe", "source"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                target, output, *_ = self.fixture(Path(temporary))
                if change == "recipe":
                    target.inputs["build-deb.sh"] = "0" * 64
                else:
                    target.pin = dict(target.pin, version="changed")
                with self.assertRaises(ValueError):
                    target.verify(output)

    def test_compilation_fields_are_required_without_legacy_fallback(self):
        for field in ("compilation", "source_key", "compile_key"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                target, output, *_ = self.fixture(
                    Path(temporary), mutate=lambda value: value.pop(field)
                )
                with self.assertRaises(ValueError):
                    target.verify(output)
        for field in ("source_key", "compile_key"):
            with self.subTest(manifest_field=field), tempfile.TemporaryDirectory() as temporary:
                target, output, _, _, manifest = self.fixture(Path(temporary))
                metadata = json.loads(manifest.read_text())
                metadata.pop(field)
                manifest.write_text(json.dumps(metadata))
                with self.assertRaises(ValueError):
                    target.verify(output)

    def test_compilation_hash_and_resealed_semantic_mismatches_rejected(self):
        changes = dict(
            source_key="c" * 64,
            inputs={"build-deb.sh": "0" * 64},
            toolchain={"packages": ["foreign"]},
            declared_builder_image="foreign",
            schema=2,
        )
        for field, changed in changes.items():
            for reseal in (False, True):
                with (
                    self.subTest(field=field, reseal=reseal),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    target, output, *_ = self.fixture(
                        Path(temporary),
                        mutate=lambda value: value["compilation"].__setitem__(field, changed),
                        reseal=reseal,
                    )
                    with self.assertRaises(ValueError):
                        target.verify(output)


class AlpineKlipperTargetTests(unittest.TestCase):
    def fixture(self, root):
        target = adapter.AlpineKlipperTarget(DESKTOP, root / "cache")
        # Synthetic pinned bytes exercise transport, not native ABI conformance.
        target.pin = json.loads(json.dumps(target.pin))
        payloads = {
            "upstream.tar.xz": ("source", b"upstream fixture"),
            "APKBUILD.alpine": ("recipe", b"distro recipe fixture"),
            target.pin["distroPatch"]["url"].rsplit("/", 1)[1]: (
                "distroPatch",
                b"distro patch fixture",
            ),
        }
        for _, (name, data) in payloads.items():
            target.pin[name]["sha256"] = hashlib.sha256(data).hexdigest()
        output = root / "klipper-output"
        source = output / "share/sources/plasma-workspace-6.6.6"
        source.mkdir(parents=True)
        for name, original in target.files.items():
            shutil.copy2(original, source / name)
        (source / "sources.lock.json").write_text(json.dumps({"alpine": target.pin}))
        for name, (_, data) in payloads.items():
            (source / name).write_bytes(data)
        (source / "APKBUILD").write_text("generated recipe fixture")
        (source / "builder-packages.txt").write_text("fixture-package\n")
        (source / "regression.log").write_text("regression fixture\n")
        directory = output / "packages/plasma-workspace-libs"
        (directory / "keys").mkdir(parents=True)
        public = directory / "keys/sentinel-klipper.rsa.pub"
        public.write_text("public key fixture")
        package = directory / "plasma-workspace-libs-6.6.6-r1.apk"
        control = b"pkgname = plasma-workspace-libs\npkgver = 6.6.6-r1\narch = aarch64\norigin = plasma-workspace\ndepend = so:fixture.so\n"
        with tarfile.open(package, "w:gz") as archive:
            member = tarfile.TarInfo(".PKGINFO")
            member.size = len(control)
            archive.addfile(member, io.BytesIO(control))
        source_identity, compilation = target.recipe.identities(
            target.assets, target.patch, target.pin, {"packages": ["fixture-package"]}
        )
        provenance = target.recipe.package_identity(
            target.assets,
            target.patch,
            target.pin,
            source_identity,
            compilation,
            source / "APKBUILD",
            public,
        )
        regression = dict(
            startuprestore="passed",
            user_uid=1000,
            log_sha256=adapter.sha(source / "regression.log"),
        )
        provenance["regression"] = regression
        (source / "provenance.json").write_text(json.dumps(provenance))
        manifest = directory / "manifest.json"
        metadata = dict(
            schema=1,
            distribution="alpine",
            architecture="aarch64",
            name="plasma-workspace-libs",
            version="6.6.6-r1",
            apk=package.name,
            sha256=adapter.sha(package),
            key="keys/" + public.name,
            key_sha256=adapter.sha(public),
            dependencies=["so:fixture.so"],
            regression=regression,
            qualified=False,
        )
        metadata.update(
            {key: provenance[key] for key in ("source_key", "compile_key", "package_key")}
        )
        manifest.write_text(json.dumps(metadata))
        return target, output, manifest, source

    def test_stage_commands_and_manifest_last_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, output, manifest, _ = self.fixture(root)
            stage = root / "stage with spaces"
            target.stage(stage)
            self.assertEqual(
                {p.name for p in (stage / "klipper-inputs").iterdir()}, set(target.files)
            )
            command = shlex.split(
                target.commands(stage, test_user="sentinel", jobs=3).splitlines()[1]
            )
            for value in (
                "SENTINEL_BUILD_TEST_USER=sentinel",
                "SENTINEL_BUILD_JOBS=3",
                "SENTINEL_BUILD_WORK=/var/cache/sentinel-build/klipper-alpine",
            ):
                self.assertIn(value, command)
            files = target.verify(output)
            self.assertEqual(files[-1], manifest)
            copied = []
            target.record(root, lambda source, dest: copied.append((source, dest)))
            self.assertEqual([pair[0] for pair in copied], list(files))
            self.assertTrue(all(dest.is_relative_to(target.destination) for _, dest in copied))
            self.assertFalse(json.loads(manifest.read_text())["qualified"])

    def test_invalid_commands_rejected(self):
        target = adapter.AlpineKlipperTarget(DESKTOP, Path("/unused"))
        for user, jobs in [("root", 1), ("", 1), ("sentinel", 0), ("sentinel", True)]:
            with self.subTest(user=user, jobs=jobs), self.assertRaises(ValueError):
                target.commands(Path("/stage"), test_user=user, jobs=jobs)

    def test_manifest_identity_filename_key_and_hash_mismatches_rejected(self):
        for field, value in dict(
            distribution="debian",
            architecture="arm64",
            version="6.6.6-r0",
            qualified=True,
            apk="../escape.apk",
            key="../escape.pub",
            sha256="0" * 64,
            key_sha256="0" * 64,
            source_key="0" * 64,
            compile_key="0" * 64,
            package_key="0" * 64,
            dependencies=["so:foreign.so"],
        ).items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                target, output, manifest, _ = self.fixture(Path(directory))
                value_record = json.loads(manifest.read_text())
                value_record[field] = value
                manifest.write_text(json.dumps(value_record))
                with self.assertRaises(ValueError):
                    target.verify(output)

    def test_source_bytes_regression_inventory_and_provenance_mismatches_rejected(self):
        for name in (
            "upstream.tar.xz",
            "APKBUILD.alpine",
            "compile-alpine.sh",
            "regression.log",
            "builder-packages.txt",
            "APKBUILD",
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                target, output, _, source = self.fixture(Path(directory))
                (source / name).write_text("changed bytes")
                with self.assertRaises(ValueError):
                    target.verify(output)
        for field in ("source_key", "compile_key", "package_key", "pins", "regression"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                target, output, _, source = self.fixture(Path(directory))
                path = source / "provenance.json"
                value = json.loads(path.read_text())
                value[field] = {} if field in ("pins", "regression") else "0" * 64
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    target.verify(output)

    def test_asset_symlink_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, output, _, source = self.fixture(root)
            original = source / "regression.log"
            outside = root / "outside.log"
            original.rename(outside)
            original.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "escaping"):
                target.verify(output)

    def test_regression_must_pass_as_nonroot_with_matching_log(self):
        for field, value in [
            ("startuprestore", "failed"),
            ("user_uid", 0),
            ("user_uid", True),
            ("log_sha256", "0" * 64),
        ]:
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as directory:
                target, output, manifest, source = self.fixture(Path(directory))
                for path in (manifest, source / "provenance.json"):
                    data = json.loads(path.read_text())
                    data["regression"][field] = value
                    path.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, "regression"):
                    target.verify(output)


if __name__ == "__main__":
    unittest.main()
