import pytest

from app.services.runtime.workspace_image_assets import (
    local_image_files,
    runtime_version,
    validate_image_files,
)
from tests.workspace_image_assets import image_names, write_images


def test_native_bundle_inventory_is_complete_and_deterministic(tmp_path):
    write_images(tmp_path)
    assert local_image_files(tmp_path) == image_names()
    assert validate_image_files(image_names()) == image_names()


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "workspace-images/../escape",
        "workspace-images/alpine/index.json\n",
        "workspace-images/compiler/index.json",
        "workspace-images/alpine/blobs/sha256/not-a-digest",
    ],
)
def test_inventory_rejects_untrusted_or_non_workspace_paths(name):
    with pytest.raises(ValueError):
        validate_image_files(sorted([*image_names(), name]))


@pytest.mark.parametrize("names", [None, [], image_names()[1:], [*image_names(), image_names()[0]]])
def test_inventory_rejects_missing_duplicate_or_incomplete_entries(names):
    with pytest.raises(ValueError):
        validate_image_files(names)


def test_local_bundle_rejects_symlink_assets(tmp_path):
    write_images(tmp_path)
    path = tmp_path / image_names()[0]
    path.unlink()
    path.symlink_to(tmp_path / "workspace-images/manifest.json")
    with pytest.raises(ValueError, match="links"):
        local_image_files(tmp_path)


def test_version_binds_inventory_names_content_and_boot_image():
    names, hashes = ["one", "two"], ["a" * 64, "b" * 64]
    before = runtime_version(names, hashes, "init")
    assert runtime_version(["renamed", "two"], hashes, "init") != before
    assert runtime_version(names, hashes[::-1], "init") != before
    assert runtime_version(names, hashes, "other-init") != before
    with pytest.raises(ValueError):
        runtime_version(names, hashes[:1], "init")
