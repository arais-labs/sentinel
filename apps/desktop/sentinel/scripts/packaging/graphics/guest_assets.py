"""Package guest session sources and publish the built kernel without compiling."""

import hashlib
import io
import json
import shutil
from pathlib import Path
import tarfile


def publish_session_assets(guest: Path, destination: Path, package_assets=()):
    """Refresh interpreted assets even when every native host object is cached."""
    for name in ("install-guest.py", "install-browser-graphics.py", "gpu-start.sh"):
        target = destination / name
        staged = target.with_suffix(target.suffix + ".next")
        shutil.copy2(guest / name, staged)
        staged.replace(target)
    package_desktop_runtime(guest, destination, package_assets)


def package_desktop_runtime(guest: Path, destination: Path, package_assets=()):
    files = [
        file
        for file in sorted(guest.glob("*.py"))
        if file.name not in {"install-guest.py", "install-browser-graphics.py"}
    ]
    # Session integration data is versioned with the interpreted launcher, not
    # with Mesa or the host renderer. Preserve paths for desktop-owned assets.
    files += sorted(file for file in (guest / "session-assets").rglob("*") if file.is_file())
    entries = [(file.relative_to(guest).as_posix(), file) for file in files]
    # Only the verified native target's explicit file inventory is accepted;
    # never sweep temporary build outputs into the runtime bundle.
    for distribution, root, assets in package_assets:
        if distribution not in {"alpine", "debian", "ubuntu"}:
            raise ValueError("Unknown native desktop package distribution")
        for file in assets:
            relative = file.relative_to(root)
            if not file.resolve().is_relative_to(root.resolve()):
                raise ValueError("Native package asset escapes its source directory")
            entries.append((f"native-packages/{distribution}/{relative.as_posix()}", file))
    entries.sort()
    names = [name for name, _ in entries]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate desktop runtime asset")
    identity = hashlib.sha256()
    for name, file in entries:
        with file.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").digest()
        identity.update(name.encode() + b"\0" + digest)
    version = identity.hexdigest()
    archive = destination / "desktop-runtime.tar.xz"
    metadata_path = destination / "desktop-runtime.json"
    try:
        metadata = json.loads(metadata_path.read_text())
        if metadata.get("version") == version:
            with archive.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() == metadata.get("sha256"):
                    return
    except (OSError, ValueError):
        pass
    staged = archive.with_suffix(".next")
    # Native packages and corresponding-source archives are already compressed.
    # A fast outer pass preserves their bytes without spending build time trying
    # to recompress them; unchanged payloads still take the verified cache path.
    with tarfile.open(staged, "w:xz", preset=0) as bundle:
        for name, file in entries:
            info = tarfile.TarInfo("./" + name)
            info.size, info.mode = file.stat().st_size, 0o644
            with file.open("rb") as stream:
                bundle.addfile(info, stream)
        info = tarfile.TarInfo("./version")
        info.size = len(version)
        bundle.addfile(info, io.BytesIO(version.encode()))
    staged.replace(archive)
    metadata_path.write_text(
        json.dumps({"version": version, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()})
        + "\n"
    )


def publish_kernel(graphics: Path):
    with tarfile.open(graphics / "kernel-linux-arm64.tar.xz") as bundle:
        kernel = bundle.extractfile("./Image").read()
        release = bundle.extractfile("./kernelrelease").read().decode().strip()
    path = graphics.parent / "kernel"
    staged = path.with_suffix(".next")
    staged.write_bytes(kernel)
    staged.replace(path)
    metadata = {"kernelFileSha256": hashlib.sha256(kernel).hexdigest(), "kernelRelease": release}
    (graphics.parent / "kernel-manifest.json").write_text(json.dumps(metadata) + "\n")
    return metadata
