"""Require actual driver telemetry proving upload-only mappings had no readbacks."""

from pathlib import Path
import sys


def check_report(text):
    reports = 0
    for line in text.splitlines():
        if not line.startswith("storage_stats "):
            continue
        fields = dict(field.split("=", 1) for field in line.split()[1:])
        for name in ("read_captures", "capture_bytes"):
            if fields.get(name) != "0":
                raise ValueError(f"Upload-only {name} must be zero: {line}")
        reports += 1
    if not reports:
        raise ValueError("Upload-only test produced no storage_stats report")


if __name__ == "__main__":
    check_report(Path(sys.argv[1]).read_text())
    print("PASS upload-only mappings: zero read captures and capture bytes")
