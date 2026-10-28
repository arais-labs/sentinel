"""A small fake release: the sentineld build tree a remote Mac installs."""

from pathlib import Path

from app.services.workspaces.machines.remote.release import REQUIRED

# Not required (the runtime names no distribution), but every built tree has recipes.
DISTRIBUTION = "sentineld/distributions/example/distribution.json"


def release_names():
    return sorted([*REQUIRED, DISTRIBUTION])


def write_release(root):
    """Returns the deployment assets the desktop app reports: {"sentineld": dir}."""
    root = Path(root)
    for name in release_names():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
        if "/bin/" in name or name.endswith("mke2fs"):
            path.chmod(0o755)
    return {"sentineld": str(root / "sentineld")}
