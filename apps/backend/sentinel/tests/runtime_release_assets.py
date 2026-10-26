"""A small fake release: the two build trees a remote Mac installs."""

from pathlib import Path

from app.services.workspaces.machines.remote.release import REQUIRED


def release_names():
    return sorted(REQUIRED)


def write_release(root):
    """Returns the deployment assets the desktop app reports: {"sentineld": dir, "workspace": dir}."""
    root = Path(root)
    trees = {"sentineld": root / "sentineld", "sentinel-workspace": root / "sentinel-workspace"}
    for name in release_names():
        tree, relative = name.split("/", 1)
        path = trees[tree] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
        if "/bin/" in name or name.endswith("mke2fs"):
            path.chmod(0o755)
    return {"sentineld": str(trees["sentineld"]), "workspace": str(trees["sentinel-workspace"])}
