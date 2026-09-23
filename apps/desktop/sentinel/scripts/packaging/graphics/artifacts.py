"""Publish complete generated bundles without overwriting mapped executables."""

from pathlib import Path
import tempfile


def publish_directory(staged: Path, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".graphics-retired-", dir=destination.parent
    ) as retired:
        previous = Path(retired) / "graphics"
        if destination.exists():
            destination.rename(previous)
        try:
            staged.rename(destination)
        except BaseException:
            if previous.exists():
                previous.rename(destination)
            raise
