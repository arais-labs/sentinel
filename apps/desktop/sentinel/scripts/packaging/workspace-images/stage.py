"""Verify and stage the complete native-init image catalog, without running VMs."""

import argparse
import json
import shutil
import tempfile
from pathlib import Path

import build


def read_json(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing regular image metadata: {path}")
    return json.loads(path.read_bytes())


def regular_tree(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"OCI layout must be a directory, not a symlink: {root}")
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError(f"OCI layout contains a non-regular entry: {path}")


def verify_layout(layout, distribution, image, base):
    regular_tree(layout)
    if read_json(layout / "oci-layout") != {"imageLayoutVersion": "1.0.0"}:
        raise ValueError("Unsupported OCI layout version")
    if image.get("base_image") != base["image"] or image.get("release") != base["release"]:
        raise ValueError(f"Stale base image metadata for {distribution}")
    if image.get("entrypoint") != base["entrypoint"]:
        raise ValueError(f"Wrong native init entrypoint for {distribution}")
    reference = f"sentinel.local/workspace/{distribution}@{image.get('digest')}"
    if image.get("reference") != reference:
        raise ValueError(f"Invalid content-addressed image reference for {distribution}")
    outer = read_json(layout / "index.json")
    if outer.get("schemaVersion") != 2 or len(outer.get("manifests", [])) != 1:
        raise ValueError("OCI import root must contain exactly one index")
    descriptor = outer["manifests"][0]
    if (
        descriptor.get("mediaType") != build.INDEX_TYPE
        or descriptor.get("digest") != image.get("digest")
        or descriptor.get("annotations", {}).get("org.opencontainers.image.ref.name") != reference
    ):
        raise ValueError("OCI import index does not match the image catalog")
    index = read_json(build.descriptor_blob(layout, descriptor))
    if (
        index.get("schemaVersion") != 2
        or index.get("mediaType") != build.INDEX_TYPE
        or len(index.get("manifests", [])) != 1
    ):
        raise ValueError("OCI index must contain exactly one runtime image")
    build.validate_image(layout, index["manifests"][0], base["entrypoint"])


def verify_catalog(source):
    bases = read_json(build.RECIPES / "bases.json")
    expected = {
        "schema": 1,
        "boot_contract": bases["boot_contract"],
        "platform": bases["platform"],
        "source_key": build.source_key(),
    }
    catalog = {**expected, "images": {}}
    layouts = {}
    # The Linux builder can export one merged bundle or one bundle per distro.
    merged = source / "manifest.json"
    for distribution, base in bases["distributions"].items():
        bundle = source if merged.is_file() else source / distribution
        if bundle.is_symlink():
            raise ValueError(f"Image bundle must not be a symlink: {bundle}")
        metadata = read_json(bundle / "manifest.json")
        for key, value in expected.items():
            if metadata.get(key) != value:
                raise ValueError(
                    f"Workspace image {distribution} has stale/unsupported {key}; rebuild native workspace images"
                )
        images = metadata.get("images", {})
        if not isinstance(images, dict) or distribution not in images:
            raise ValueError(f"Missing native workspace image: {distribution}")
        if set(images) - set(bases["distributions"]):
            raise ValueError("Image catalog contains an unsupported distribution")
        image = images[distribution]
        if image.get("layout") != distribution:
            raise ValueError(f"Invalid OCI layout path for {distribution}")
        layout = bundle / distribution
        verify_layout(layout, distribution, image, base)
        catalog["images"][distribution] = image
        layouts[distribution] = layout
    return catalog, layouts


def stage(source, destination):
    catalog, layouts = verify_catalog(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ValueError("Workspace image staging destination must not be a symlink")
    with tempfile.TemporaryDirectory(
        prefix=".workspace-images-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary) / "next"
        staging.mkdir()
        for distribution, layout in layouts.items():
            shutil.copytree(layout, staging / distribution, symlinks=True)
        (staging / "manifest.json").write_bytes(build.encoded(catalog))
        # Verify copied bytes, too: do not publish a partially changed source.
        verify_catalog(staging)
        retired = Path(temporary) / "previous"
        if destination.exists():
            destination.rename(retired)
        try:
            staging.rename(destination)
        except BaseException:
            if retired.exists():
                retired.rename(destination)
            raise
    return catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path, nargs="?")
    parser.add_argument("--verify-only", action="store_true")
    arguments = parser.parse_args()
    if arguments.verify_only:
        if arguments.destination:
            parser.error("--verify-only does not accept a destination")
        catalog, _ = verify_catalog(arguments.source.resolve())
    else:
        if not arguments.destination:
            parser.error("staging requires a destination")
        catalog = stage(arguments.source.resolve(), arguments.destination.absolute())
    print(json.dumps(catalog))


if __name__ == "__main__":
    main()
