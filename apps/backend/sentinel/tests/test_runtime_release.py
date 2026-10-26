import os

import pytest

from app.services.workspaces.machines.remote.release import (
    release_files,
    runtime_version,
    validate_release_files,
)
from tests.runtime_release_assets import release_names, write_release


def test_release_inventory_is_complete_and_deterministic(tmp_path):
    assets = write_release(tmp_path)
    files = release_files(assets["sentineld"])
    assert [name for _, name in files] == release_names()
    assert validate_release_files(release_names()) == release_names()


def test_release_skips_python_caches(tmp_path):
    assets = write_release(tmp_path)
    cache = tmp_path / "sentineld/share/desktop/__pycache__/setup.cpython-312.pyc"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"cache")
    files = release_files(assets["sentineld"])
    assert [name for _, name in files] == release_names()


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "sentineld/../escape",
        "sentineld/share/x\n",
        "other/bin/tool",
        "sentineld//share",
        "sentinel-workspace/bin/sentinel-workspace",
    ],
)
def test_inventory_rejects_untrusted_paths(name):
    with pytest.raises(ValueError):
        validate_release_files(sorted([*release_names(), name]))


@pytest.mark.parametrize(
    "names", [None, [], release_names()[1:], [*release_names(), release_names()[0]]]
)
def test_inventory_rejects_missing_duplicate_or_incomplete_entries(names):
    with pytest.raises(ValueError):
        validate_release_files(names)


def test_local_release_rejects_symlinks(tmp_path):
    assets = write_release(tmp_path)
    (tmp_path / "sentineld/lib/libkrun.1.dylib").symlink_to("libkrun.dylib")
    with pytest.raises(ValueError, match="symlinks"):
        release_files(assets["sentineld"])


def test_local_release_rejects_special_files(tmp_path):
    assets = write_release(tmp_path)
    os.mkfifo(tmp_path / "sentineld/share/fifo")
    with pytest.raises(ValueError, match="regular files"):
        release_files(assets["sentineld"])


def test_version_binds_names_and_content():
    names, hashes = ["one", "two"], ["a" * 64, "b" * 64]
    before = runtime_version(names, hashes)
    assert runtime_version(["renamed", "two"], hashes) != before
    assert runtime_version(names, hashes[::-1]) != before
    with pytest.raises(ValueError):
        runtime_version(names, hashes[:1])
