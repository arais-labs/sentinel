#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _find_edk2() -> tuple[str, str]:
    candidates = [
        Path("/opt/homebrew/Cellar/qemu"),
        Path("/usr/local/Cellar/qemu"),
    ]
    code_path = ""
    vars_path = ""
    for base in candidates:
        if not base.exists():
            continue
        matches_code = list(base.glob("*/share/qemu/edk2-aarch64-code.fd"))
        matches_vars = list(base.glob("*/share/qemu/edk2-arm-vars.fd"))
        if matches_code and matches_vars:
            code_path = str(matches_code[0])
            vars_path = str(matches_vars[0])
            break
    if not code_path or not vars_path:
        raise RuntimeError("Could not locate QEMU edk2 firmware files")
    return code_path, vars_path


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _launch_config(payload: dict) -> dict:
    return {
        "image_path": str(payload.get("image_path") or ""),
        "ssh_port": int(payload.get("ssh_port") or 0),
        "vnc_port": int(payload.get("vnc_port") or 0),
        "cdp_port": int(payload.get("cdp_port") or 0),
        "cpus": int(payload.get("cpus") or 0),
        "memory_mb": int(payload.get("memory_mb") or 0),
        "workspace_root": str(payload.get("workspace_root") or ""),
        "share_tag": str(payload.get("share_tag") or ""),
    }


def _read_json_file(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


class QemuBridgeHandler(BaseHTTPRequestHandler):
    server_version = "SentinelQemuBridge/1.0"

    def _token(self) -> str:
        return getattr(self.server, "bridge_token", "")

    def _require_auth(self) -> bool:
        expected = self._token()
        provided = self.headers.get("X-Sentinel-Bridge-Token", "")
        if not expected or provided != expected:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return False
        return True

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/healthz":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        if not self._require_auth():
            return
        version = ""
        try:
            result = subprocess.run(
                ["qemu-system-aarch64", "--version"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                version = (result.stdout or result.stderr or "").strip().splitlines()[0]
        except Exception:
            version = ""
        self._send_json(HTTPStatus.OK, {"ok": True, "qemu_version": version})

    def do_POST(self) -> None:
        if not self._require_auth():
            return

        if self.path == "/v1/ensure-dir":
            payload = self._read_json()
            path = payload.get("path")
            if not isinstance(path, str) or not path.strip():
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_path"})
                return
            try:
                Path(path).mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                self._send_json(HTTPStatus.OK, {"ok": False, "error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "path": path})
            return

        if self.path == "/v1/qemu/status":
            payload = self._read_json()
            run_root = payload.get("run_root")
            if not isinstance(run_root, str) or not run_root.strip():
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_run_root"})
                return
            try:
                state = self._status(Path(run_root))
            except Exception as exc:
                self._send_json(HTTPStatus.OK, {"ok": False, "error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, **state})
            return

        if self.path == "/v1/qemu/stop":
            payload = self._read_json()
            run_root = payload.get("run_root")
            if not isinstance(run_root, str) or not run_root.strip():
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_run_root"})
                return
            try:
                self._stop_vm(Path(run_root))
            except Exception as exc:
                self._send_json(HTTPStatus.OK, {"ok": False, "error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"ok": True})
            return

        if self.path == "/v1/qemu/ensure":
            payload = self._read_json()
            try:
                result = self._ensure_vm(payload)
            except Exception as exc:
                self._send_json(HTTPStatus.OK, {"ok": False, "error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, result)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

    def _status(self, run_root: Path) -> dict:
        pid_file = run_root / "vm.pid"
        overlay = run_root / "runtime-overlay.qcow2"
        serial_log = run_root / "serial.log"
        launch_config = run_root / "launch.json"
        if not pid_file.exists():
            return {
                "running": False,
                "pid": None,
                "overlay_path": str(overlay),
                "serial_log": str(serial_log),
                "launch_config": str(launch_config),
            }
        pid = int(pid_file.read_text().strip())
        if not _pid_alive(pid):
            pid_file.unlink(missing_ok=True)
            return {
                "running": False,
                "pid": pid,
                "overlay_path": str(overlay),
                "serial_log": str(serial_log),
                "launch_config": str(launch_config),
            }
        return {
            "running": True,
            "pid": pid,
            "overlay_path": str(overlay),
            "serial_log": str(serial_log),
            "launch_config": str(launch_config),
            "config": _read_json_file(launch_config),
        }

    def _stop_vm(self, run_root: Path) -> None:
        pid_file = run_root / "vm.pid"
        if not pid_file.exists():
            return
        pid = int(pid_file.read_text().strip())
        if _pid_alive(pid):
            os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                if not _pid_alive(pid):
                    break
                time.sleep(0.1)
            if _pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
        pid_file.unlink(missing_ok=True)

    def _ensure_vm(self, payload: dict) -> dict:
        run_root = payload.get("run_root")
        image_path = payload.get("image_path")
        ssh_port = int(payload.get("ssh_port") or 0)
        vnc_port = int(payload.get("vnc_port") or 0)
        cdp_port = int(payload.get("cdp_port") or 0)
        cpus = int(payload.get("cpus") or 0)
        memory_mb = int(payload.get("memory_mb") or 0)
        workspace_root = payload.get("workspace_root")
        share_tag = payload.get("share_tag")
        if (
            not isinstance(run_root, str) or not run_root.strip()
            or not isinstance(image_path, str) or not image_path.strip()
            or not isinstance(workspace_root, str) or not workspace_root.strip()
            or not isinstance(share_tag, str) or not share_tag.strip()
        ):
            raise ValueError("Missing required QEMU launch parameters")
        if min(ssh_port, vnc_port, cdp_port, cpus, memory_mb) <= 0:
            raise ValueError("Invalid QEMU port or sizing parameters")

        run_dir = Path(run_root)
        run_dir.mkdir(parents=True, exist_ok=True)
        Path(workspace_root).mkdir(parents=True, exist_ok=True)
        requested_config = _launch_config(payload)
        launch_config = run_dir / "launch.json"

        status = self._status(run_dir)
        if status.get("running"):
            current_config = status.get("config")
            if current_config == requested_config:
                return {"ok": True, **status}
            self._stop_vm(run_dir)

        image = Path(image_path)
        if not image.exists():
            raise FileNotFoundError(f"QEMU image not found: {image}")

        overlay = run_dir / "runtime-overlay.qcow2"
        pid_file = run_dir / "vm.pid"
        serial_log = run_dir / "serial.log"
        qemu_log = run_dir / "qemu.log"
        vars_file = run_dir / "edk2-arm-vars.fd"
        code_file, vars_template = _find_edk2()
        if not vars_file.exists():
            shutil.copyfile(vars_template, vars_file)
        if not overlay.exists():
            subprocess.run(
                [
                    "qemu-img",
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

        qemu_cmd = [
            "qemu-system-aarch64",
            "-name",
            "sentinel-qemu-runtime",
            "-machine",
            "virt,accel=hvf",
            "-cpu",
            "host",
            "-smp",
            str(cpus),
            "-m",
            str(memory_mb),
            "-device",
            "virtio-gpu-pci",
            "-device",
            "virtio-keyboard-pci",
            "-device",
            "virtio-mouse-pci",
            "-netdev",
            f"user,id=net0,hostfwd=tcp:127.0.0.1:{ssh_port}-:22,hostfwd=tcp:127.0.0.1:{vnc_port}-:6080,hostfwd=tcp:127.0.0.1:{cdp_port}-:9223",
            "-device",
            "virtio-net-pci,netdev=net0",
            "-virtfs",
            f"local,path={workspace_root},mount_tag={share_tag},security_model=mapped-xattr,multidevs=remap",
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
        launch_config.write_text(json.dumps(requested_config, ensure_ascii=True, indent=2, sort_keys=True))
        return {
            "ok": True,
            "running": True,
            "pid": process.pid,
            "overlay_path": str(overlay),
            "serial_log": str(serial_log),
            "qemu_log": str(qemu_log),
        }

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), QemuBridgeHandler)
    server.bridge_token = args.token
    server.serve_forever()


if __name__ == "__main__":
    main()
