"""Build native-init OCI roots using an explicit Docker/buildx build environment.

This never starts a daemon, pushes images, or touches workspace disks. BuildKit
exports OCI layouts; the runtime imports their actual content-addressed indexes.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

DESKTOP = Path(__file__).resolve().parents[3]
RECIPES = DESKTOP / "native/workspace-images"
INDEX_TYPE = "application/vnd.oci.image.index.v1+json"
MANIFEST_TYPE = "application/vnd.oci.image.manifest.v1+json"
DIGEST = re.compile(r"sha256:([0-9a-f]{64})\Z")


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def file_digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return "sha256:" + result.hexdigest()


def descriptor_blob(layout, descriptor):
    match = DIGEST.fullmatch(descriptor.get("digest", ""))
    if not match:
        raise ValueError("OCI descriptor must have a SHA-256 digest")
    path = layout / "blobs/sha256" / match[1]
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"OCI blob is missing or not a regular file: {path}")
    if path.stat().st_size != descriptor.get("size") or file_digest(path) != descriptor["digest"]:
        raise ValueError(f"OCI blob failed size/digest verification: {path}")
    return path


def validate_image(layout, descriptor, entrypoint):
    """Require exactly one ARM64 Linux runtime image, with no attestations."""
    if descriptor.get("mediaType") != MANIFEST_TYPE:
        raise ValueError("Expected one OCI image manifest, not an index or attestation")
    manifest = json.loads(descriptor_blob(layout, descriptor).read_bytes())
    config = json.loads(descriptor_blob(layout, manifest["config"]).read_bytes())
    if (config.get("architecture"), config.get("os")) != ("arm64", "linux"):
        raise ValueError("Workspace images must be Linux ARM64")
    if config.get("config", {}).get("Entrypoint") != entrypoint:
        raise ValueError("Workspace image must boot its native init directly")
    if config.get("config", {}).get("Cmd") not in (None, []):
        raise ValueError("Unexpected arguments to workspace init")
    for layer in manifest["layers"]:
        descriptor_blob(layout, layer)


def qualify_layout(layout, distribution, entrypoint):
    if json.loads((layout / "oci-layout").read_bytes()) != {"imageLayoutVersion": "1.0.0"}:
        raise ValueError("Unsupported OCI layout version")
    index = json.loads((layout / "index.json").read_bytes())
    if index.get("schemaVersion") != 2 or len(index.get("manifests", [])) != 1:
        raise ValueError("Export must contain exactly one image")
    validate_image(layout, index["manifests"][0], entrypoint)
    # Preserve a real content-addressed index as the import root. ImageStore.load
    # otherwise wraps a plain manifest in a new index, changing its root digest.
    image_index = {"schemaVersion": 2, "mediaType": INDEX_TYPE, "manifests": index["manifests"]}
    data = encoded(image_index)
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    (layout / "blobs/sha256" / digest.split(":")[1]).write_bytes(data)
    reference = f"sentinel.local/workspace/{distribution}@{digest}"
    root_descriptor = {
        "mediaType": INDEX_TYPE,
        "digest": digest,
        "size": len(data),
        "annotations": {"org.opencontainers.image.ref.name": reference},
    }
    (layout / "index.json").write_bytes(
        encoded({"schemaVersion": 2, "manifests": [root_descriptor]})
    )
    return reference, digest


def source_key():
    result = hashlib.sha256(Path(__file__).read_bytes())
    for path in sorted(RECIPES.rglob("*")):
        # Documentation edits cannot change the root filesystem. Keep them out
        # of artifact identity so writing build instructions never invalidates it.
        if path.is_file() and path.suffix != ".md":
            result.update(
                str(path.relative_to(RECIPES)).encode() + b"\0" + path.read_bytes() + b"\0"
            )
    return result.hexdigest()


def build(output, distributions, docker="docker", builder=None):
    bases = json.loads((RECIPES / "bases.json").read_bytes())
    if output.exists():
        raise FileExistsError(
            f"Use a new output directory; existing artifacts are preserved: {output}"
        )
    for distribution in distributions:
        if distribution not in bases["distributions"]:
            raise ValueError(f"Unknown distribution: {distribution}")
    # Fail before starting any builds if no developer-provided daemon is ready.
    subprocess.run([docker, "info", "--format", "{{.ServerVersion}}"], check=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    key = source_key()
    with tempfile.TemporaryDirectory(prefix=".workspace-images-", dir=output.parent) as temporary:
        staging = Path(temporary) / "images"
        staging.mkdir()
        manifest = {
            "schema": 1,
            "boot_contract": bases["boot_contract"],
            "platform": bases["platform"],
            "source_key": key,
            "images": {},
        }
        for distribution in distributions:
            base = bases["distributions"][distribution]
            if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", base["image"]):
                raise ValueError("Base images must be pinned by digest")
            layout = staging / distribution
            command = [docker, "buildx", "build"]
            if builder:
                command += ["--builder", builder]
            command += [
                "--platform",
                bases["platform"],
                "--provenance=false",
                "--sbom=false",
                "--build-arg",
                "BASE_IMAGE=" + base["image"],
                "--file",
                str(RECIPES / f"{distribution}.Dockerfile"),
                "--output",
                f"type=oci,dest={layout},tar=false",
                str(RECIPES),
            ]
            print(f"Building {distribution} {base['release']} native-init image", flush=True)
            subprocess.run(command, check=True)
            reference, digest = qualify_layout(layout, distribution, base["entrypoint"])
            manifest["images"][distribution] = {
                "layout": distribution,
                "reference": reference,
                "digest": digest,
                "base_image": base["image"],
                "release": base["release"],
                "entrypoint": base["entrypoint"],
            }
        (staging / "manifest.json").write_bytes(encoded(manifest))
        os.rename(staging, output)
    print(json.dumps(manifest, indent=2), flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--distribution", action="append", choices=["alpine", "ubuntu", "debian"])
    parser.add_argument(
        "--docker", default="docker", help="Docker CLI; honors DOCKER_HOST/DOCKER_CONTEXT"
    )
    parser.add_argument("--builder", help="Explicit existing buildx builder")
    arguments = parser.parse_args()
    build(
        arguments.output.resolve(),
        list(dict.fromkeys(arguments.distribution or ["alpine", "ubuntu", "debian"])),
        arguments.docker,
        arguments.builder,
    )


if __name__ == "__main__":
    main()
