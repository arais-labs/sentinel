"""Prepare pinned third-party sources and apply explicit patches at build time."""

import hashlib
import filecmp
import os
from pathlib import Path
import subprocess
import tempfile
import time


def preserve_source_times(tree: Path, previous: Path):
    """Publish unchanged inputs with stable mtimes, but dirty reverted patches too.

    Archive mtimes are not build timestamps: restoring an upstream file must
    not make it appear older than an object built from a now-retired patch.
    Symlink topology is separately part of the configuration identity.
    """
    now = time.time_ns()
    for file in tree.rglob("*"):
        if file.is_symlink() or not file.is_file():
            continue
        old = previous / file.relative_to(tree)
        if (
            not old.is_symlink()
            and old.is_file()
            and old.stat().st_mode == file.stat().st_mode
            and filecmp.cmp(old, file, shallow=False)
        ):
            modified = old.stat().st_mtime_ns
        else:
            modified = now
        os.utime(file, ns=(now, modified))


def prepare_source(work: Path, repo: str, revision: str, name: str, patches=()) -> Path:
    patches = tuple(patches)
    key = hashlib.sha256(
        f"{repo}@{revision}".encode() + b"".join(p.name.encode() + p.read_bytes() for p in patches)
    ).hexdigest()
    work.mkdir(parents=True, exist_ok=True)
    target = work / name
    stamp = target / ".sentinel-source"
    upstream_stamp = target / ".sentinel-upstream"
    if stamp.is_file() and stamp.read_text() == key and upstream_stamp.is_file():
        return target

    archive = work / f"{name}-{revision}.tar.gz"
    with tempfile.TemporaryDirectory(prefix=f".{name}-", dir=work) as directory:
        staging = Path(directory)
        if not archive.is_file():
            download = staging / "source.tar.gz"
            subprocess.run(
                [
                    "curl",
                    "--fail",
                    "--location",
                    "--retry",
                    "2",
                    "--connect-timeout",
                    "15",
                    "--max-time",
                    "180",
                    "--silent",
                    "--show-error",
                    f"https://codeload.github.com/{repo}/tar.gz/{revision}",
                    "-o",
                    str(download),
                ],
                check=True,
            )
            download.replace(archive)
        tree = staging / "source"
        tree.mkdir()
        subprocess.run(
            ["tar", "xf", str(archive), "--strip-components=1", "-C", str(tree)], check=True
        )
        for patch in patches:
            result = subprocess.run(
                ["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(patch.resolve())],
                cwd=tree,
                capture_output=True,
                text=True,
            )
            if result.returncode:
                raise RuntimeError(
                    f"{name}@{revision}: {patch.name} failed\n{result.stdout}{result.stderr}"
                )
        (tree / stamp.name).write_text(key)
        (tree / upstream_stamp.name).write_text(f"{repo}@{revision}")
        preserve_source_times(tree, target)
        # Never publish a partially patched source tree. Recreate from upstream
        # when patches change, so removed patches leave no edits behind.
        previous = staging / "previous"
        if target.exists():
            target.replace(previous)
        try:
            tree.replace(target)
        except BaseException:
            if previous.exists():
                previous.replace(target)
            raise
    return target
