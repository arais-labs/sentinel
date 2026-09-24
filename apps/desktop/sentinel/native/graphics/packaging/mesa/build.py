"""Package the normal cached musl Mesa build as a signed native APK family."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from verify import FAMILY, VERSION, package_metadata, sha, validate_stage


def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def build(inputs, work, output):
    recipe_inputs = inputs / "packaging/mesa"
    stage = work / "mesa-install"
    source = work / "mesa-26.1.6"
    validate_stage(stage)
    pin = json.loads((inputs / "sources.lock.json").read_text())["mesa-guest"]
    if sha(inputs / "mesa.tar.xz") != pin["sha256"]:
        raise ValueError("Mesa source archive does not match pinned digest")
    options = {
        item["name"]: item["value"]
        for item in json.loads((work / "build/meson-info/intro-buildoptions.json").read_text())
    }
    for name, value in {
        "prefix": "/usr",
        "libdir": "lib",
        "gles1": "enabled",
        "glvnd": "disabled",
    }.items():
        if options.get(name) != value:
            raise ValueError(f"Wrong native Mesa build option: {name}")
    patches = sorted((inputs / "patches/guest").glob("*.patch"))
    drivers = sorted((inputs / "guest/driver").glob("*.[ch]"))
    for driver in drivers:
        if sha(driver) != sha(source / "src/gallium/drivers/virgl" / driver.name):
            raise ValueError(f"Compiled driver source differs from build inputs: {driver.name}")
    changed = set()
    for patch in patches:
        for line in patch.read_text().splitlines():
            if line.startswith("+++ b/"):
                relative = line[6:].split("\t")[0]
                if ".." in Path(relative).parts or Path(relative).is_absolute():
                    raise ValueError("Invalid patched source path")
                changed.add(relative)
    provenance = dict(
        schema=1,
        upstream=pin,
        version=VERSION,
        meson_options=options,
        patches={p.name: sha(p) for p in patches},
        driver_sources={p.name: sha(p) for p in drivers},
        resulting_sources={p: sha(source / p) for p in sorted(changed)},
        recipe_inputs={p.name: sha(p) for p in sorted(recipe_inputs.glob("*")) if p.is_file()},
    )
    keys = work / "mesa-package-keys"
    keys.mkdir(exist_ok=True)
    private, public = keys / "sentinel-mesa.rsa", keys / "sentinel-mesa.rsa.pub"
    if not private.exists():
        run("openssl", "genrsa", "-out", private, "2048")
    private.chmod(0o600)
    run("openssl", "rsa", "-in", private, "-pubout", "-out", public)
    with tempfile.TemporaryDirectory(prefix="mesa-package-", dir=work) as temporary:
        temporary = Path(temporary)
        recipe, trusted = temporary / "recipe", temporary / "trusted"
        recipe.mkdir()
        trusted.mkdir()
        shutil.copy2(recipe_inputs / "APKBUILD", recipe / "APKBUILD")
        shutil.copy2(source / "docs/license.rst", recipe / "license.rst")
        (recipe / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
        for key in [*Path("/etc/apk/keys").glob("*.pub"), public]:
            shutil.copy2(key, trusted / key.name)
        environment = dict(
            os.environ,
            PACKAGER="Sentinel graphics build",
            PACKAGER_PRIVKEY=str(private),
            PACKAGER_PUBKEY=str(public),
            APK="apk --keys-dir " + str(trusted),
            SENTINEL_MESA_STAGE=str(stage),
        )
        run(
            "abuild",
            "-F",
            "-C",
            recipe,
            "-P",
            temporary / "packages",
            "rootpkg",
            "update_abuildrepo_index",
            env=environment,
        )
        destination = output / "packages/mesa"
        (destination / "keys").mkdir(parents=True, exist_ok=True)
        shutil.copy2(public, destination / "keys" / public.name)
        packages = []
        for name in FAMILY:
            filename = f"{name}-{VERSION}.apk"
            matches = list((temporary / "packages").rglob(filename))
            if len(matches) != 1:
                raise ValueError(f"Expected exactly one package: {filename}")
            archive = matches[0]
            run(
                "apk",
                "--keys-dir",
                destination / "keys",
                "--no-network",
                "--repositories-file",
                "/dev/null",
                "verify",
                archive,
            )
            dependencies = package_metadata(archive, name)
            shutil.copy2(archive, destination / filename)
            packages.append(
                dict(name=name, apk=filename, sha256=sha(archive), dependencies=dependencies)
            )
        manifest = dict(
            schema=1,
            name="mesa",
            distribution="alpine",
            architecture="aarch64",
            version=VERSION,
            key="keys/" + public.name,
            key_sha256=sha(public),
            packages=packages,
            source_sha256=pin["sha256"],
        )
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        sources = output / "share/sources/mesa-26.1.6"
        sources.mkdir(parents=True, exist_ok=True)
        for path in (recipe / "provenance.json", recipe / "license.rst", recipe / "APKBUILD"):
            shutil.copy2(path, sources / path.name)
        for label, paths in (("patches", patches), ("driver", drivers)):
            directory = sources / label
            directory.mkdir(exist_ok=True)
            for path in paths:
                shutil.copy2(path, directory / path.name)


if __name__ == "__main__":
    build(*(Path(value).resolve() for value in sys.argv[1:]))
