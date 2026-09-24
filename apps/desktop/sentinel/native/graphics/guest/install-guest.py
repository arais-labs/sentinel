"""Install checked worker archives; restore the previous component on failure."""

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile


def install(stream, size, digest, version, kind, system=Path("/")):
    if kind not in {"graphics", "kernel", "desktop"} or not 0 < size <= 1_073_741_824:
        raise ValueError("Invalid runtime component or archive size")
    root = system / "opt/sentinel"
    root.mkdir(parents=True, exist_ok=True)
    prefix = root / kind
    work = Path(tempfile.mkdtemp(prefix=".install-", dir=root))
    previous = work / "previous"
    loader = old_loader = created_link = None
    swapped = False
    native_transaction = False
    success = False
    try:
        archive = work / "bundle.tar.xz"
        checksum = hashlib.sha256()
        with archive.open("wb") as target:
            while size:
                chunk = stream.read(min(size, 65536))
                if not chunk:
                    raise RuntimeError("Graphics bundle transfer was interrupted")
                target.write(chunk)
                checksum.update(chunk)
                size -= len(chunk)
        if checksum.hexdigest() != digest:
            raise RuntimeError("Graphics bundle checksum mismatch")
        unpacked = work / "unpacked"
        unpacked.mkdir()
        with tarfile.open(archive, "r:xz") as bundle:
            bundle.extractall(unpacked, filter="data")
        if (unpacked / "version").read_text().strip() != version:
            raise RuntimeError("Graphics bundle version mismatch")
        link = None
        if kind == "kernel":
            release = (unpacked / "kernelrelease").read_text().strip()
            if not release or any(
                c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_+"
                for c in release
            ):
                raise RuntimeError("Invalid kernel module release")
            if not (unpacked / "lib/modules" / release).is_dir():
                raise RuntimeError("Kernel module bundle is incomplete")
            link = system / "lib/modules" / release
            destination = prefix / "lib/modules" / release
            if link.is_symlink():
                if link.resolve() != destination.resolve():
                    raise RuntimeError("Kernel modules belong to another installation")
            elif link.exists():
                raise RuntimeError("Kernel module destination is already in use")
            (unpacked / "Image").unlink(missing_ok=True)
        elif kind == "graphics":
            musl = (system / "lib/ld-musl-aarch64.so.1").exists()
            if musl:
                if not (unpacked / "packages/mesa/manifest.json").is_file():
                    raise RuntimeError("Native Alpine Mesa package bundle is missing")
            else:
                # glibc uses the private GLVND vendor, not replacement dispatchers.
                loader = system / "etc/ld.so.conf.d/00-sentinel-graphics.conf"
                old_loader = loader.read_bytes() if loader.exists() else None
                loader_contents = str(prefix / "lib") + "\n"
        (unpacked / "bundle.sha256").write_text(digest)
        if prefix.exists():
            prefix.rename(previous)
        unpacked.rename(prefix)
        swapped = True
        if link is not None and not link.is_symlink():
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(destination, target_is_directory=True)
            created_link = link
        if loader is not None:
            loader.parent.mkdir(parents=True, exist_ok=True)
            staging = loader.with_name(loader.name + ".sentinel-next")
            staging.write_text(loader_contents)
            staging.replace(loader)
            if not musl:
                subprocess.run([str(system / "sbin/ldconfig")], check=True)
        if kind == "graphics" and musl:
            native_transaction = True
            subprocess.run(
                [
                    sys.executable,
                    str(prefix / "bin/install-native-mesa.py"),
                    str(prefix / "packages/mesa"),
                ],
                check=True,
            )
        if kind == "desktop":
            subprocess.run(
                [sys.executable, str(prefix / "graphics_environment.py"), str(system)],
                check=True,
            )
        success = True
    except BaseException:
        if native_transaction:
            # APK owns /usr and its transaction cannot be rolled back by swapping
            # /opt. Retain the matching package assets, but never mark a failed
            # installation reusable. A retry can complete the same transaction.
            (prefix / "bundle.sha256").unlink(missing_ok=True)
            raise
        if created_link is not None:
            created_link.unlink(missing_ok=True)
        if swapped:
            shutil.rmtree(prefix)
        if previous.exists():
            previous.rename(prefix)
        if loader is not None:
            if old_loader is None:
                loader.unlink(missing_ok=True)
            else:
                loader.write_bytes(old_loader)
            if not musl:
                subprocess.run([str(system / "sbin/ldconfig")], check=False)
        raise
    finally:
        # If rollback itself failed, retain the old component for recovery.
        if success or not previous.exists():
            shutil.rmtree(work)
        else:
            print(f"Previous runtime preserved at {previous}", file=sys.stderr)


if __name__ == "__main__":
    install(
        sys.stdin.buffer,
        int(sys.argv[1]),
        sys.argv[2],
        sys.argv[3],
        sys.argv[4] if len(sys.argv) > 4 else "graphics",
    )
    print("Workspace runtime component installed")
