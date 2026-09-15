"""Bounded file/range reads over guest process I/O; ZIPs spool to guest disk."""

import json
import os
import re
import stat
import sys
import tempfile
import zipfile
from contextlib import ExitStack
from pathlib import Path

from sentinel_guest_files import REQUEST, RuntimePathError, resolve_container_path, media_type_for

BLOCK_SIZE = 256 * 1024


def byte_range(value, size):
    if not value:
        return 0, size, 200
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
    if not match or not any(match.groups()) or size == 0:
        raise ValueError("Requested range is not satisfiable")
    first, last = match.groups()
    if not first:
        count = int(last)
        if count == 0:
            raise ValueError("Requested range is not satisfiable")
        start, end = max(0, size - count), size - 1
    else:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
        if start >= size or end < start:
            raise ValueError("Requested range is not satisfiable")
    return start, end - start + 1, 206


def archive_directory(target, archive):
    # os.walk and file-backed ZipFile avoid buffering file bodies or a complete ZIP.
    with zipfile.ZipFile(archive, mode="w", compression=zipfile.ZIP_DEFLATED) as output:
        for current, directories, files in os.walk(target, followlinks=False):
            directory = Path(current)
            directories[:] = sorted(
                name for name in directories if not (directory / name).is_symlink()
            )
            if not directories and not files:
                relative = directory.relative_to(target).as_posix()
                if relative != ".":
                    output.writestr(relative + "/", b"")
            for name in sorted(files):
                item = directory / name
                if not item.is_file():
                    continue
                output.write(item, arcname=item.relative_to(target).as_posix())
    archive.seek(0)


def main():
    started = False
    try:
        with ExitStack() as stack:
            target = resolve_container_path(REQUEST["path"])
            directory = target.is_dir()
            if directory and not REQUEST.get("download"):
                raise IsADirectoryError("Choose a file to preview")
            if directory:
                source = stack.enter_context(
                    tempfile.TemporaryFile(prefix="sentinel-download-", suffix=".zip")
                )
                archive_directory(target, source)
                name, media_type = (target.name or "workspace") + ".zip", "application/zip"
            else:
                # Reject special files before open: reading a device/FIFO can block forever.
                if not stat.S_ISREG(target.stat().st_mode):
                    raise RuntimePathError("Only regular files and directories can be downloaded")
                source = stack.enter_context(target.open("rb"))
                name = target.name
                media_type = media_type_for(target)
            info = os.fstat(source.fileno())
            size = info.st_size
            etag = f'"{info.st_ino:x}-{size:x}-{info.st_mtime_ns:x}"' if not directory else None
            range_value = REQUEST.get("range")
            if REQUEST.get("if_range") and REQUEST["if_range"] != etag:
                range_value = None
            try:
                start, length, status = byte_range(range_value, size)
            except ValueError as error:
                print(
                    json.dumps({"ok": False, "status": 416, "size": size, "detail": str(error)}),
                    flush=True,
                )
                return
            print(
                json.dumps(
                    {
                        "ok": True,
                        "status": status,
                        "size": size,
                        "length": length,
                        "start": start,
                        "name": name,
                        "media_type": media_type,
                        "etag": etag,
                        "directory": directory,
                    }
                ),
                flush=True,
            )
            started = True
            if REQUEST.get("head"):
                return
            source.seek(start)
            remaining = length
            while remaining:
                data = source.read(min(BLOCK_SIZE, remaining))
                if not data:
                    raise OSError("The file changed while it was being read")
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
                remaining -= len(data)
                if sys.stdin.buffer.readline() != b"next\n":
                    return
    except Exception as error:
        # The process transport merges stderr with stdout. After the header,
        # close with a short body rather than corrupting bytes with error text.
        if not started:
            status = (
                404
                if isinstance(error, FileNotFoundError) or getattr(error, "code", "") == "not_found"
                else 422
            )
            print(json.dumps({"ok": False, "status": status, "detail": str(error)}), flush=True)
        raise SystemExit(1)


main()
