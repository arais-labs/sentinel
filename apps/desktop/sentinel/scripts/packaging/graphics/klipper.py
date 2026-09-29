"""Transport for unqualified distro-native Klipper packages."""

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex
import shutil
import tarfile


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class KlipperTarget:
    def __init__(self, desktop, cache, distribution):
        if distribution not in ("debian", "ubuntu"):
            raise ValueError("Klipper DEB target requires Debian or Ubuntu")
        self.distribution = distribution
        self.assets = desktop / "native/graphics/packaging/klipper"
        self.pin = json.loads((self.assets / "sources.lock.json").read_text())[distribution]
        bases = json.loads((desktop / "native/workspace-images/bases.json").read_text())
        base = bases["distributions"][distribution]
        self.image = base["image"]
        if (
            bases["platform"] != "linux/arm64"
            or self.pin["architecture"] != "arm64"
            or base["release"] != self.pin["release"]
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[0-9a-f]{64}", self.image)
        ):
            raise ValueError("Klipper source and native builder pins do not match")
        # Input ownership stays in the native recipe, including its input list.
        spec = importlib.util.spec_from_file_location(
            "klipper_native_recipe", self.assets / "build-package-deb.py"
        )
        recipe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(recipe)
        self.files = {name: self.assets / name for name in recipe.INPUTS}
        patch = desktop / "native/graphics/patches/klipper/0001-startup-restore-only-empty.patch"
        self.files[patch.name] = patch
        if distribution == "debian":
            self.files["debian-arm64-symbols.patch"] = self.assets / "debian-arm64-symbols.patch"
        self.inputs = {
            name: sha(path) for name, path in self.files.items() if name != "sources.lock.json"
        }
        self.compilation_inputs = {name: self.inputs[name] for name in recipe.COMPILATION_INPUTS}
        identity = {
            "source": self.pin,
            "inputs": self.inputs,
            "declared_builder_image": self.image,
            "adapter": sha(Path(__file__)),
        }
        self.key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        self.destination = cache / ("klipper-" + distribution) / self.key

    def stage(self, output):
        directory = output / "klipper-inputs"
        directory.mkdir(parents=True, exist_ok=True)
        for name, source in self.files.items():
            shutil.copy2(source, directory / name)

    def commands(self, output, *, test_user, jobs=4):
        if (
            not output.is_absolute()
            or not test_user
            or test_user == "root"
            or type(jobs) is not int
            or jobs <= 0
        ):
            raise ValueError(
                "Absolute guest staging path, ordinary test user and positive jobs required"
            )
        inputs = output / "klipper-inputs"
        return (
            "set -eu\n"
            + shlex.join(
                [
                    "python3",
                    str(inputs / "build-package-deb.py"),
                    self.distribution,
                    str(output / "klipper-output"),
                    "--inputs",
                    str(inputs),
                    "--patch",
                    str(inputs / "0001-startup-restore-only-empty.patch"),
                    "--work",
                    "/var/cache/sentinel-build/klipper",
                    "--ccache",
                    "/var/cache/sentinel-build/ccache",
                    "--builder-image",
                    self.image,
                    "--test-user",
                    test_user,
                    "--jobs",
                    str(jobs),
                ]
            )
            + "\n"
        )

    def verify(self, root):
        """Verify transport/provenance, not release qualification or a DEB ABI."""
        manifest = root / "packages/libklipper6/manifest.json"
        metadata = json.loads(manifest.read_text())
        expected = dict(
            schema=1,
            distribution=self.distribution,
            release=self.pin["release"],
            architecture="arm64",
            name=self.pin["package"],
            version=self.pin["sourceVersion"] + "+sentinel1",
            declared_builder_image=self.image,
            regression="passed",
            qualified=False,
            source=f"share/sources/klipper-{self.distribution}/corresponding-source.tar.xz",
        )
        if (
            any(metadata.get(key) != value for key, value in expected.items())
            or metadata.get("qualified") is not False
        ):
            raise ValueError("Klipper native manifest contract mismatch")
        name = metadata["deb"]
        if not isinstance(name, str) or Path(name).name != name or not name.endswith(".deb"):
            raise ValueError("Unsafe Klipper package filename")
        package = manifest.parent / name
        source = root / metadata["source"]
        if sha(package) != metadata["sha256"] or sha(source) != metadata["source_sha256"]:
            raise ValueError("Klipper output checksum mismatch")
        with tarfile.open(source, "r:xz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise ValueError("Duplicate corresponding-source member")

            def read(name):
                member = archive.getmember(name)
                if not member.isfile():
                    raise ValueError("Corresponding-source member must be a regular file")
                return archive.extractfile(member).read()

            for name, digest in self.inputs.items():
                if hashlib.sha256(read("recipe/" + name)).hexdigest() != digest:
                    raise ValueError("Klipper recipe checksum mismatch")
            lock = json.loads(read("recipe/sources.lock.json"))
            if lock[self.distribution] != self.pin:
                raise ValueError("Klipper archived source pin mismatch")
            for name, digest in self.pin["files"].items():
                if hashlib.sha256(read("upstream/" + name)).hexdigest() != digest:
                    raise ValueError("Klipper upstream checksum mismatch")
            for name in (
                "packaging/changelog",
                "provenance/build.log",
                "provenance/regression.log",
                "provenance/package.log",
            ):
                read(name)
            provenance = json.loads(read("provenance/provenance.json"))
        identity = {
            key: provenance[key]
            for key in ("schema", "source", "inputs", "toolchain", "declared_builder_image")
        }
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        if (
            identity["schema"] != 1
            or identity["source"] != self.pin
            or identity["inputs"] != self.inputs
            or identity["declared_builder_image"] != self.image
            or key != metadata["component_key"]
            or key != provenance["component_key"]
            or provenance.get("qualified") is not False
            or provenance["regression"]["startuprestore"] != "passed"
            or provenance["regression"]["user_uid"] <= 0
        ):
            raise ValueError("Klipper native provenance mismatch")
        compilation = provenance.get("compilation")
        source_key = provenance.get("source_key")
        if (
            not isinstance(compilation, dict)
            or not isinstance(source_key, str)
            or not re.fullmatch(r"[0-9a-f]{64}", source_key)
        ):
            raise ValueError("Missing or invalid Klipper compilation provenance")
        compile_key = hashlib.sha256(json.dumps(compilation, sort_keys=True).encode()).hexdigest()
        if (
            compilation.get("schema") != 1
            or compilation.get("source_key") != source_key
            or metadata.get("source_key") != source_key
            or provenance.get("compile_key") != compile_key
            or metadata.get("compile_key") != compile_key
            or compilation.get("inputs") != self.compilation_inputs
            or compilation.get("toolchain") != identity["toolchain"]
            or compilation.get("declared_builder_image") != self.image
            or not isinstance(compilation.get("environment"), dict)
        ):
            raise ValueError("Klipper compilation identity mismatch")
        control = provenance["package_control"]
        if any(
            control[field] != metadata[value]
            for field, value in [
                ("Package", "name"),
                ("Version", "version"),
                ("Architecture", "architecture"),
                ("Depends", "dependencies"),
            ]
        ):
            raise ValueError("Klipper package control provenance mismatch")
        return package, source, manifest

    def record(self, output, publish_file):
        root = output / "klipper-output"
        # Validate everything before publishing; preserve qualified:false verbatim.
        for path in self.verify(root):
            publish_file(path, self.destination / path.relative_to(root))


class AlpineKlipperTarget(KlipperTarget):
    def __init__(self, desktop, cache):
        self.distribution = "alpine"
        self.assets = desktop / "native/graphics/packaging/klipper"
        self.pin = json.loads((self.assets / "sources.lock.json").read_text())["alpine"]
        bases = json.loads((desktop / "native/workspace-images/bases.json").read_text())
        base = bases["distributions"]["alpine"]
        self.image = base["image"]
        if (
            bases["platform"] != "linux/arm64"
            or self.pin["architecture"] != "aarch64"
            or base["release"] != self.pin["release"]
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[0-9a-f]{64}", self.image)
        ):
            raise ValueError("Klipper source and native Alpine builder pins do not match")
        spec = importlib.util.spec_from_file_location(
            "klipper_native_alpine", self.assets / "build-package-alpine.py"
        )
        self.recipe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.recipe)
        self.files = {
            name: self.assets / name for name in (*self.recipe.PACKAGE_INPUTS, "sources.lock.json")
        }
        self.patch = (
            desktop / "native/graphics/patches/klipper/0001-startup-restore-only-empty.patch"
        )
        self.files[self.patch.name] = self.patch
        self.inputs = {
            name: sha(path) for name, path in self.files.items() if name != "sources.lock.json"
        }
        self.key = self.recipe.digest(
            dict(
                source=self.pin,
                inputs=self.inputs,
                declared_builder_image=self.image,
                adapter=sha(Path(__file__)),
            )
        )
        self.destination = cache / "klipper-alpine" / self.key

    def commands(self, output, *, test_user, jobs=4):
        if (
            not output.is_absolute()
            or not test_user
            or test_user == "root"
            or type(jobs) is not int
            or jobs <= 0
        ):
            raise ValueError(
                "Absolute guest staging path, ordinary test user and positive jobs required"
            )
        inputs = output / "klipper-inputs"
        return (
            "set -eu\n"
            + shlex.join(
                [
                    "env",
                    "SENTINEL_KLIPPER_INPUTS=" + str(inputs),
                    "SENTINEL_KLIPPER_PATCH=" + str(inputs / self.patch.name),
                    "SENTINEL_BUILD_WORK=/var/cache/sentinel-build/klipper-alpine",
                    "SENTINEL_BUILD_TEST_USER=" + test_user,
                    "SENTINEL_BUILD_JOBS=" + str(jobs),
                    "sh",
                    str(inputs / "build-alpine.sh"),
                    str(output / "klipper-output"),
                ]
            )
            + "\n"
        )

    def verify(self, root):
        # Transport checks do not replace native signature/ABI or release tests.
        def asset(relative):
            path = root / relative
            if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
                raise ValueError("Missing or escaping Alpine Klipper artifact: " + str(relative))
            return path

        directory = Path("packages/plasma-workspace-libs")
        manifest = asset(directory / "manifest.json")
        metadata = json.loads(manifest.read_text())
        filename = f"plasma-workspace-libs-{self.pin['version']}-r{self.pin['packageRevision']}.apk"
        expected = dict(
            schema=1,
            distribution="alpine",
            architecture="aarch64",
            name="plasma-workspace-libs",
            version=f"{self.pin['version']}-r{self.pin['packageRevision']}",
            apk=filename,
            key="keys/sentinel-klipper.rsa.pub",
            qualified=False,
        )
        if (
            any(metadata.get(k) != v for k, v in expected.items())
            or metadata.get("qualified") is not False
        ):
            raise ValueError("Alpine Klipper manifest contract mismatch")
        package, public = asset(directory / filename), asset(directory / metadata["key"])
        if sha(package) != metadata.get("sha256") or sha(public) != metadata.get("key_sha256"):
            raise ValueError("Alpine Klipper package/key checksum mismatch")
        with tarfile.open(package, "r:gz", ignore_zeros=True) as archive:
            members = [member for member in archive if member.name == ".PKGINFO"]
            if len(members) != 1 or not members[0].isfile():
                raise ValueError("Invalid Alpine package metadata")
            control = {}
            for line in archive.extractfile(members[0]).read().decode().splitlines():
                if " = " in line:
                    field, value = line.split(" = ", 1)
                    control.setdefault(field, []).append(value)
        if (
            any(
                control.get(field) != [metadata[key]]
                for field, key in [
                    ("pkgname", "name"),
                    ("pkgver", "version"),
                    ("arch", "architecture"),
                ]
            )
            or control.get("origin") != ["plasma-workspace"]
            or sorted(control.get("depend", [])) != sorted(metadata.get("dependencies", []))
        ):
            raise ValueError("Alpine Klipper package control mismatch")
        source_dir = Path("share/sources") / ("plasma-workspace-" + self.pin["version"])
        source_files = {}
        for name in (
            *self.files,
            "APKBUILD",
            "APKBUILD.alpine",
            "upstream.tar.xz",
            self.pin["distroPatch"]["url"].rsplit("/", 1)[1],
            "builder-packages.txt",
            "provenance.json",
            "regression.log",
        ):
            source_files[name] = asset(source_dir / name)
        if any(sha(source_files[name]) != digest for name, digest in self.inputs.items()):
            raise ValueError("Alpine Klipper source recipe checksum mismatch")
        if json.loads(source_files["sources.lock.json"].read_text())["alpine"] != self.pin:
            raise ValueError("Alpine Klipper source pins mismatch")
        for name, pin in [
            ("upstream.tar.xz", self.pin["source"]),
            ("APKBUILD.alpine", self.pin["recipe"]),
            (self.pin["distroPatch"]["url"].rsplit("/", 1)[1], self.pin["distroPatch"]),
        ]:
            if sha(source_files[name]) != pin["sha256"]:
                raise ValueError("Alpine Klipper upstream checksum mismatch")
        provenance = json.loads(source_files["provenance.json"].read_text())
        regression = provenance.get("regression", {})
        if (
            regression.get("startuprestore") != "passed"
            or type(regression.get("user_uid")) is not int
            or regression["user_uid"] <= 0
            or regression.get("log_sha256") != sha(source_files["regression.log"])
            or metadata.get("regression") != regression
        ):
            raise ValueError("Alpine Klipper regression evidence mismatch")
        toolchain = provenance["compilation"]["toolchain"]
        if source_files["builder-packages.txt"].read_text().splitlines() != toolchain["packages"]:
            raise ValueError("Alpine Klipper builder inventory mismatch")
        source, compilation = self.recipe.identities(self.assets, self.patch, self.pin, toolchain)
        expected_provenance = self.recipe.package_identity(
            self.assets, self.patch, self.pin, source, compilation, source_files["APKBUILD"], public
        )
        expected_provenance["regression"] = regression
        if provenance != expected_provenance or any(
            metadata.get(key) != expected_provenance[key]
            for key in ("source_key", "compile_key", "package_key")
        ):
            raise ValueError("Alpine Klipper native provenance mismatch")
        return (package, public, *source_files.values(), manifest)
