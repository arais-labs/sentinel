"""Opt-in qualification download reuse; normal snap install verifies assertions."""

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path


def seed(source, destination):
    source, destination = Path(source), Path(destination)
    manifest = json.loads((source / "manifest.json").read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported Snap cache manifest")
    verified = []
    for entry in manifest["blobs"]:
        filename, digest = entry["filename"], entry["sha3_384"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.snap", filename):
            raise ValueError("Invalid Snap cache filename")
        if not re.fullmatch(r"[0-9a-f]{96}", digest):
            raise ValueError("Invalid SHA3-384 digest")
        blob = source / filename
        if blob.is_symlink() or not blob.is_file():
            raise ValueError("Snap cache blob must be a regular non-symlink file")
        destination.mkdir(parents=True, exist_ok=True)
        # Copy onto the guest filesystem: snapd's cache lookup uses hardlinks.
        with tempfile.NamedTemporaryFile(dir=destination, delete=False) as output:
            temporary = Path(output.name)
            try:
                with blob.open("rb") as incoming:
                    shutil.copyfileobj(incoming, output)
                output.flush()
                with temporary.open("rb") as copied:
                    actual = hashlib.file_digest(copied, "sha3_384").hexdigest()
                if temporary.stat().st_size != entry["size"] or actual != digest:
                    raise ValueError(f"Snap cache integrity failure: {filename}")
                os.chmod(temporary, 0o600)
                os.replace(temporary, destination / digest)
            finally:
                temporary.unlink(missing_ok=True)
        verified.append({"filename": filename, "sha3_384": digest, "size": entry["size"]})
    return verified


if __name__ == "__main__":
    print(json.dumps(seed(sys.argv[1], sys.argv[2])))
