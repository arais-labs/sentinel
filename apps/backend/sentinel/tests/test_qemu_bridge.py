from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
BRIDGE_PATH = REPO_ROOT / "infra" / "runtime" / "qemu" / "bridge.py"


@pytest.fixture(scope="module")
def bridge():
    spec = importlib.util.spec_from_file_location("sentinel_qemu_bridge", BRIDGE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _make_image(tmp_path: Path, name: str = "base.qcow2", payload: bytes = b"x" * 1024) -> Path:
    image = tmp_path / name
    image.write_bytes(payload)
    return image


def test_overlay_should_reset_returns_none_when_overlay_missing(bridge, tmp_path):
    overlay = tmp_path / "missing.qcow2"
    image = _make_image(tmp_path)
    assert bridge._overlay_should_reset(overlay, image) is None


def test_overlay_should_reset_when_metadata_unreadable(bridge, tmp_path, monkeypatch):
    overlay = tmp_path / "overlay.qcow2"
    overlay.write_bytes(b"garbage")
    image = _make_image(tmp_path)
    monkeypatch.setattr(bridge, "_qemu_img_info", lambda _path: None)
    assert bridge._overlay_should_reset(overlay, image) == "unable to read overlay metadata"


def test_overlay_should_reset_when_backing_path_drifts(bridge, tmp_path, monkeypatch):
    overlay = tmp_path / "overlay.qcow2"
    overlay.write_bytes(b"")
    image = _make_image(tmp_path, "current.qcow2")
    drifted = tmp_path / "elsewhere" / "old.qcow2"
    monkeypatch.setattr(
        bridge,
        "_qemu_img_info",
        lambda _path: {"full-backing-filename": str(drifted)},
    )
    reason = bridge._overlay_should_reset(overlay, image)
    assert reason is not None
    assert "drifted" in reason


def test_overlay_should_reset_when_backing_path_missing(bridge, tmp_path, monkeypatch):
    overlay = tmp_path / "overlay.qcow2"
    overlay.write_bytes(b"")
    image_path = tmp_path / "missing.qcow2"
    # image must exist for the resolve()-based equality check; create then remove.
    image_path.write_bytes(b"")
    monkeypatch.setattr(
        bridge,
        "_qemu_img_info",
        lambda _path: {"full-backing-filename": str(image_path)},
    )
    image_path.unlink()
    reason = bridge._overlay_should_reset(overlay, image_path)
    assert reason is not None
    assert "missing" in reason


def test_overlay_should_reset_when_base_size_changed(bridge, tmp_path, monkeypatch):
    overlay = tmp_path / "overlay.qcow2"
    overlay.write_bytes(b"")
    image = _make_image(tmp_path)
    monkeypatch.setattr(
        bridge,
        "_qemu_img_info",
        lambda _path: {"full-backing-filename": str(image)},
    )
    sidecar = bridge._overlay_base_sidecar(overlay)
    sidecar.write_text(json.dumps({"size": image.stat().st_size + 1, "mtime_ns": image.stat().st_mtime_ns}))
    reason = bridge._overlay_should_reset(overlay, image)
    assert reason is not None
    assert "size" in reason


def test_overlay_should_reset_when_base_mtime_changed(bridge, tmp_path, monkeypatch):
    overlay = tmp_path / "overlay.qcow2"
    overlay.write_bytes(b"")
    image = _make_image(tmp_path)
    monkeypatch.setattr(
        bridge,
        "_qemu_img_info",
        lambda _path: {"full-backing-filename": str(image)},
    )
    sidecar = bridge._overlay_base_sidecar(overlay)
    sidecar.write_text(json.dumps({"size": image.stat().st_size, "mtime_ns": image.stat().st_mtime_ns + 1}))
    reason = bridge._overlay_should_reset(overlay, image)
    assert reason is not None
    assert "mtime" in reason


def test_overlay_should_reset_returns_none_when_all_match(bridge, tmp_path, monkeypatch):
    overlay = tmp_path / "overlay.qcow2"
    overlay.write_bytes(b"")
    image = _make_image(tmp_path)
    monkeypatch.setattr(
        bridge,
        "_qemu_img_info",
        lambda _path: {"full-backing-filename": str(image)},
    )
    bridge._record_overlay_base(overlay, image)
    assert bridge._overlay_should_reset(overlay, image) is None


def test_overlay_should_reset_returns_none_when_no_sidecar_yet(bridge, tmp_path, monkeypatch):
    # Legacy overlays without a sidecar must not trigger a destructive recreate
    # purely because we have no recorded baseline; only the path checks apply.
    overlay = tmp_path / "overlay.qcow2"
    overlay.write_bytes(b"")
    image = _make_image(tmp_path)
    monkeypatch.setattr(
        bridge,
        "_qemu_img_info",
        lambda _path: {"full-backing-filename": str(image)},
    )
    assert bridge._overlay_should_reset(overlay, image) is None


def test_record_overlay_base_writes_size_and_mtime(bridge, tmp_path):
    overlay = tmp_path / "overlay.qcow2"
    overlay.write_bytes(b"")
    image = _make_image(tmp_path)
    bridge._record_overlay_base(overlay, image)
    sidecar = bridge._overlay_base_sidecar(overlay)
    parsed = json.loads(sidecar.read_text())
    assert parsed["size"] == image.stat().st_size
    assert parsed["mtime_ns"] == image.stat().st_mtime_ns
    assert parsed["image_path"] == str(image)
