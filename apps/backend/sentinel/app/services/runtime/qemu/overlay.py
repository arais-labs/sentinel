from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_json_file(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def overlay_base_sidecar(overlay: Path) -> Path:
    return overlay.with_name(overlay.name + ".base.json")


def qemu_img_info(qemu_img: str, path: Path) -> dict | None:
    try:
        result = subprocess.run(
            [qemu_img, "info", "--output=json", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def overlay_should_reset(qemu_img: str, overlay: Path, image: Path) -> str | None:
    if not overlay.exists():
        return None
    info = qemu_img_info(qemu_img, overlay)
    if info is None:
        return "unable to read overlay metadata"
    backing = info.get("full-backing-filename") or info.get("backing-filename")
    if not backing:
        return None
    backing_path = Path(str(backing)).resolve()
    target = image.resolve()
    if backing_path != target:
        return f"backing path drifted: {backing_path} != {target}"
    if not backing_path.exists():
        return f"backing image missing at {backing_path}"
    sidecar = overlay_base_sidecar(overlay)
    recorded = read_json_file(sidecar)
    if recorded is None:
        return None
    try:
        stat = backing_path.stat()
    except OSError:
        return f"unable to stat backing image at {backing_path}"
    if int(recorded.get("size", -1)) != stat.st_size:
        return "base image size changed since overlay creation"
    if int(recorded.get("mtime_ns", -1)) != stat.st_mtime_ns:
        return "base image mtime changed since overlay creation"
    return None


def record_overlay_base(overlay: Path, image: Path) -> None:
    try:
        stat = image.stat()
    except OSError:
        return
    overlay_base_sidecar(overlay).write_text(
        json.dumps(
            {
                "image_path": str(image),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            },
            indent=2,
            sort_keys=True,
        )
    )
