"""Install the bundled binaries from stdin. Workspace setup never compiles."""

import hashlib
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

size, digest, version = int(sys.argv[1]), sys.argv[2], sys.argv[3]
root = Path("/opt/sentinel")
root.mkdir(parents=True, exist_ok=True)
prefix = root / "graphics"
with tempfile.TemporaryDirectory(prefix=".graphics-", dir=root) as work:
    work = Path(work)
    archive = work / "bundle.tar.xz"
    checksum = hashlib.sha256()
    with archive.open("wb") as target:
        while size:
            chunk = sys.stdin.buffer.read(min(size, 65536))
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
    previous = work / "previous"
    if prefix.exists():
        prefix.rename(previous)
    try:
        unpacked.rename(prefix)
    except BaseException:
        if previous.exists():
            previous.rename(prefix)
        raise
    # Chromium's GPU child drops LD_LIBRARY_PATH. Register the same bundled
    # driver with the guest's actual libc loader, never the macOS host loader.
    if Path("/lib/ld-musl-aarch64.so.1").exists():
        loader = Path("/etc/ld-musl-aarch64.path")
        paths = (
            loader.read_text().strip().split(":")
            if loader.exists()
            else ["/lib", "/usr/local/lib", "/usr/lib"]
        )
        paths = [str(prefix / "lib")] + [entry for entry in paths if entry != str(prefix / "lib")]
        loader.write_text(":".join(paths) + "\n")
    else:
        loader = Path("/etc/ld.so.conf.d/00-sentinel-graphics.conf")
        loader.parent.mkdir(parents=True, exist_ok=True)
        loader.write_text(str(prefix / "lib") + "\n")
        subprocess.run(["/sbin/ldconfig"], check=True)
    (prefix / "bundle.sha256").write_text(digest)
print("Metal graphics installed")
