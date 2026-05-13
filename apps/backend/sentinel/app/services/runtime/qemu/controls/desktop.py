from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

from app.services.runtime.qemu.controls.base import QemuBridgeError
from app.services.runtime.qemu.overlay import (
    overlay_base_sidecar,
    overlay_should_reset,
    pid_alive,
    read_json_file,
    record_overlay_base,
)
from app.services.runtime.qemu.profile import QemuProfile

logger = logging.getLogger(__name__)


class DesktopQemuControl:
    def __init__(self) -> None:
        self._qemu_system = self._require_binary("qemu-system-aarch64")
        self._qemu_img = self._require_binary("qemu-img")

    @staticmethod
    def _require_binary(name: str) -> str:
        resolved = shutil.which(name)
        if not resolved:
            raise QemuBridgeError(f"Missing QEMU binary on PATH: {name}")
        return resolved

    def _firmware_paths(self) -> tuple[str, str]:
        qemu_share = Path(self._qemu_system).resolve().parent.parent / "share" / "qemu"
        code_path = qemu_share / "edk2-aarch64-code.fd"
        vars_path = qemu_share / "edk2-arm-vars.fd"
        if not code_path.exists() or not vars_path.exists():
            raise QemuBridgeError(f"Could not locate QEMU edk2 firmware files under {qemu_share}")
        return str(code_path), str(vars_path)

    async def health(self) -> dict[str, object]:
        return await asyncio.to_thread(self._health_sync)

    def _health_sync(self) -> dict[str, object]:
        version = subprocess.run(
            [self._qemu_system, "--version"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.splitlines()
        self._firmware_paths()
        return {"ok": True, "mode": "desktop", "qemu_version": version[0] if version else ""}

    async def ensure_dir(self, path: str) -> None:
        await asyncio.to_thread(Path(path).mkdir, parents=True, exist_ok=True)

    async def ensure_base_image(self, profile: QemuProfile) -> None:
        status = await self.base_image_status(profile)
        if status.get("state") == "ready":
            return
        image = Path(profile.image)
        key = Path(profile.ssh_key_path)
        for artifact in (image, key, Path(f"{key}.pub")):
            artifact.unlink(missing_ok=True)
        await self._build_base_image(profile)
        status = await self.base_image_status(profile)
        if status.get("state") != "ready":
            raise QemuBridgeError(str(status.get("message") or "QEMU base image build did not produce a usable image"))

    async def base_image_status(self, profile: QemuProfile) -> dict[str, object]:
        return await asyncio.to_thread(self._base_image_status_sync, profile)

    async def ensure_vm(self, profile: QemuProfile) -> None:
        await asyncio.to_thread(self._ensure_vm_sync, profile)

    async def stop_vm(self, profile: QemuProfile) -> None:
        await asyncio.to_thread(self._stop_vm_sync, Path(profile.run_root))

    async def status(self, profile: QemuProfile) -> dict[str, object]:
        return await asyncio.to_thread(self._status_sync, Path(profile.run_root))

    def _status_sync(self, run_dir: Path) -> dict[str, object]:
        pid_file = run_dir / "vm.pid"
        launch_config = run_dir / "launch.json"
        if not pid_file.exists():
            return {"ok": True, "running": False}
        try:
            pid = int(pid_file.read_text().strip())
        except Exception:
            return {"ok": True, "running": False}
        if not pid_alive(pid):
            return {"ok": True, "running": False, "pid": pid}
        payload: dict[str, object] = {"ok": True, "running": True, "pid": pid}
        config = read_json_file(launch_config)
        if config is not None:
            payload["config"] = config
        return payload

    def _base_image_status_sync(self, profile: QemuProfile) -> dict[str, object]:
        image = Path(profile.image)
        key = Path(profile.ssh_key_path)
        payload: dict[str, object] = {
            "image_path": str(image),
            "key_path": str(key),
            "present": image.exists() and key.exists(),
        }
        if not image.exists() or not key.exists():
            return {**payload, "state": "missing", "message": "QEMU base image has not been built yet."}
        result = subprocess.run(
            [self._qemu_img, "info", str(image)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return {
                **payload,
                "state": "invalid",
                "message": (result.stderr or result.stdout or "qemu-img could not read the base image").strip(),
            }
        return {**payload, "state": "ready", "message": "QEMU base image is ready."}

    def _qemu_resource_root(self) -> Path:
        return Path(self._qemu_system).resolve().parent.parent

    @staticmethod
    def _image_runtime_root(profile: QemuProfile) -> Path:
        return Path(profile.image).resolve().parent.parent

    @staticmethod
    def _find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    async def _build_base_image(self, profile: QemuProfile) -> None:
        script = self._qemu_resource_root() / "build-base-image.sh"
        if not script.exists():
            raise QemuBridgeError(f"QEMU base image build script is missing: {script}")
        runtime_root = self._image_runtime_root(profile)
        code_path, vars_path = self._firmware_paths()
        env = {
            **os.environ,
            "SENTINEL_QEMU_OUTPUT_DIR": str(Path(profile.image).resolve().parent),
            "SENTINEL_QEMU_OUTPUT_IMAGE_NAME": Path(profile.image).name,
            "SENTINEL_QEMU_CACHE_DIR": str(runtime_root / "cache"),
            "SENTINEL_QEMU_BUILD_ROOT": str(runtime_root / "build"),
            "SENTINEL_QEMU_RUN_DIR": str(runtime_root / "run"),
            "SENTINEL_QEMU_CPUS": str(profile.cpus),
            "SENTINEL_QEMU_MEMORY_MB": str(profile.memory_mb),
            "SENTINEL_QEMU_SSH_PORT": str(self._find_free_port()),
            "SENTINEL_QEMU_EDK2_CODE": code_path,
            "SENTINEL_QEMU_EDK2_VARS": vars_path,
        }
        logger.info("Building QEMU base image at %s", profile.image)
        process = await asyncio.create_subprocess_exec(
            str(script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        try:
            assert process.stdout is not None
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                logger.info("qemu image build: %s", line.decode(errors="replace").rstrip())
            returncode = await process.wait()
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            with contextlib.suppress(ProcessLookupError, asyncio.TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=10)
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
            raise
        if returncode != 0:
            raise QemuBridgeError(f"QEMU base image build failed with exit {returncode}")

    def _stop_vm_sync(self, run_dir: Path) -> None:
        pid_file = run_dir / "vm.pid"
        if not pid_file.exists():
            return
        try:
            pid = int(pid_file.read_text().strip())
        except Exception:
            pid_file.unlink(missing_ok=True)
            return
        if pid_alive(pid):
            try:
                os.kill(pid, 15)
                for _ in range(80):
                    if not pid_alive(pid):
                        break
                    time.sleep(0.1)
                if pid_alive(pid):
                    os.kill(pid, 9)
            except OSError:
                pass
        pid_file.unlink(missing_ok=True)

    @staticmethod
    def _launch_config(profile: QemuProfile) -> dict[str, object]:
        return {
            "image_path": profile.image,
            "ssh_port": profile.ssh_port,
            "vnc_port": profile.vnc_port,
            "cdp_port": profile.cdp_port,
            "cpus": profile.cpus,
            "memory_mb": profile.memory_mb,
            "workspace_root": profile.workspace_root,
            "share_tag": profile.share_tag,
        }

    def _ensure_vm_sync(self, profile: QemuProfile) -> None:
        run_dir = Path(profile.run_root)
        image = Path(profile.image)
        workspace_root = Path(profile.workspace_root)
        if not image.exists():
            raise QemuBridgeError(f"QEMU image not found: {image}")
        run_dir.mkdir(parents=True, exist_ok=True)
        workspace_root.mkdir(parents=True, exist_ok=True)

        requested_config = self._launch_config(profile)
        status = self._status_sync(run_dir)
        overlay = run_dir / "runtime-overlay.qcow2"
        reset_reason = overlay_should_reset(self._qemu_img, overlay, image)
        if reset_reason:
            logger.info("Recreating QEMU overlay at %s: %s", overlay, reset_reason)
            if status.get("running"):
                self._stop_vm_sync(run_dir)
            overlay.unlink(missing_ok=True)
            overlay_base_sidecar(overlay).unlink(missing_ok=True)
            status = {"ok": True, "running": False}

        if status.get("running"):
            if status.get("config") == requested_config:
                return
            self._stop_vm_sync(run_dir)

        pid_file = run_dir / "vm.pid"
        serial_log = run_dir / "serial.log"
        qemu_log = run_dir / "qemu.log"
        vars_file = run_dir / "edk2-arm-vars.fd"
        code_file, vars_template = self._firmware_paths()
        if not vars_file.exists():
            shutil.copyfile(vars_template, vars_file)
        if not overlay.exists():
            subprocess.run(
                [
                    self._qemu_img,
                    "create",
                    "-f",
                    "qcow2",
                    "-F",
                    "qcow2",
                    "-b",
                    str(image),
                    str(overlay),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            record_overlay_base(overlay, image)

        qemu_cmd = [
            self._qemu_system,
            "-name",
            "sentinel-qemu-runtime",
            "-machine",
            "virt,accel=hvf",
            "-cpu",
            "host",
            "-smp",
            str(profile.cpus),
            "-m",
            str(profile.memory_mb),
            "-device",
            "virtio-gpu-pci",
            "-device",
            "virtio-keyboard-pci",
            "-device",
            "virtio-mouse-pci",
            "-netdev",
            f"user,id=net0,hostfwd=tcp:127.0.0.1:{profile.ssh_port}-:22,hostfwd=tcp:127.0.0.1:{profile.vnc_port}-:6080,hostfwd=tcp:127.0.0.1:{profile.cdp_port}-:9223",
            "-device",
            "virtio-net-pci,netdev=net0",
            "-virtfs",
            f"local,path={profile.workspace_root},mount_tag={profile.share_tag},security_model=mapped-xattr,multidevs=remap",
            "-drive",
            f"if=pflash,format=raw,readonly=on,file={code_file}",
            "-drive",
            f"if=pflash,format=raw,file={vars_file}",
            "-drive",
            f"if=virtio,format=qcow2,file={overlay}",
            "-display",
            "none",
            "-serial",
            f"file:{serial_log}",
        ]
        with qemu_log.open("ab") as log_fp:
            process = subprocess.Popen(
                qemu_cmd,
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        pid_file.write_text(str(process.pid))
        (run_dir / "launch.json").write_text(json.dumps(requested_config, ensure_ascii=True, indent=2, sort_keys=True))
