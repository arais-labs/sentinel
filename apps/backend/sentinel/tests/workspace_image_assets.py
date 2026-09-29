"""Small image-file inventories; OCI semantics are covered by packaging tests."""

from pathlib import Path


def image_names():
    return sorted(
        [
            "workspace-images/manifest.json",
            *[
                f"workspace-images/{distribution}/{name}"
                for distribution in ("alpine", "debian", "ubuntu")
                for name in ("index.json", "oci-layout", "blobs/sha256/" + "a" * 64)
            ],
        ]
    )


def write_images(root):
    for name in image_names():
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
