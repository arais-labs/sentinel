"""Receive one streamed upload inside the guest; never replace existing files."""

import json
import os
from pathlib import Path
import sys
import tempfile

BLOCK_SIZE = 256 * 1024


def emit(value):
    print(json.dumps(value), flush=True)


def receive(request):
    root = Path(request["workspace"])
    destination = Path(request["destination"]) if request["destination"] else root
    if not destination.is_absolute():
        destination = root / destination
    destination = destination.resolve(strict=True)
    if not destination.is_dir():
        raise ValueError("The upload destination is not a folder")
    relative = request["name"]
    parts = relative.split("/")
    if not relative or any(
        part in {"", ".", ".."} or "\0" in part or "\\" in part for part in parts
    ):
        raise ValueError("Invalid upload filename")
    target = destination.joinpath(*parts)
    # Imported directory paths cannot follow a symlink out of the chosen folder.
    parent = destination
    for part in parts[:-1]:
        parent = parent / part
        parent.mkdir(exist_ok=True)
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError("An upload folder conflicts with an existing file or link")
    size = request["size"]
    if not isinstance(size, int) or size < 0:
        raise ValueError("Invalid file size")
    temporary = None
    try:
        if request["kind"] == "directory":
            if size:
                raise ValueError("Folders cannot contain a file body")
            emit({"ready": True})
            if sys.stdin.buffer.read(1):
                raise ValueError("Unexpected folder body")
            if target.is_symlink():
                raise ValueError("An upload folder conflicts with an existing link")
            target.mkdir(exist_ok=True)
        else:
            with tempfile.NamedTemporaryFile(
                prefix=".sentinel-upload-", dir=parent, delete=False
            ) as output:
                temporary = Path(output.name)
                emit({"ready": True})
                remaining = size
                while remaining:
                    expected = min(remaining, BLOCK_SIZE)
                    block = sys.stdin.buffer.read(expected)
                    if len(block) != expected:
                        raise ValueError("Upload interrupted; incomplete file was not saved")
                    output.write(block)
                    remaining -= len(block)
                    emit({"received": size - remaining})
                if sys.stdin.buffer.read(1):
                    raise ValueError("Uploaded file exceeds its declared size")
                output.flush()
                os.fsync(output.fileno())
            original = target
            for suffix in range(10000):
                target = (
                    original
                    if suffix == 0
                    else original.with_name(f"{original.stem} ({suffix}){original.suffix}")
                )
                try:
                    # Atomic no-clobber publication, including competing uploads.
                    os.link(temporary, target)
                    break
                except FileExistsError:
                    continue
            else:
                raise ValueError("Too many files already use this name")
        try:
            path = target.relative_to(root.resolve()).as_posix()
        except ValueError:
            path = str(target)
        emit({"ok": True, "path": path, "name": target.name, "size": size})
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


try:
    receive(json.loads(sys.argv[1]))
except Exception as error:
    emit({"ok": False, "detail": str(error)})
    sys.exit(1)
