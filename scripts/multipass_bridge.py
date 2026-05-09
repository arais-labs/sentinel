#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


class MultipassBridgeHandler(BaseHTTPRequestHandler):
    server_version = "SentinelMultipassBridge/1.0"

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
                ["multipass", "version"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                version = (result.stdout or "").strip()
        except Exception:
            version = ""
        self._send_json(HTTPStatus.OK, {"ok": True, "multipass_version": version})

    def do_POST(self) -> None:
        if not self._require_auth():
            return

        if self.path == "/v1/command":
            payload = self._read_json()
            argv = payload.get("argv")
            cwd = payload.get("cwd")
            env = payload.get("env")
            timeout_seconds = payload.get("timeout_seconds", 120)
            if (
                not isinstance(argv, list)
                or not argv
                or any(not isinstance(item, str) or not item for item in argv)
            ):
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_argv"})
                return
            if argv[0] != "multipass":
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "only_multipass_allowed"})
                return

            run_env = os.environ.copy()
            if isinstance(env, dict):
                for key, value in env.items():
                    if isinstance(key, str) and isinstance(value, str):
                        run_env[key] = value
            try:
                result = subprocess.run(
                    argv,
                    cwd=cwd if isinstance(cwd, str) and cwd else None,
                    env=run_env,
                    capture_output=True,
                    text=True,
                    timeout=int(timeout_seconds),
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": False,
                        "timed_out": True,
                        "returncode": None,
                        "stdout": exc.stdout or "",
                        "stderr": exc.stderr or "",
                    },
                )
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": result.returncode == 0,
                    "timed_out": False,
                    "returncode": result.returncode,
                    "stdout": result.stdout or "",
                    "stderr": result.stderr or "",
                },
            )
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

        if self.path == "/v1/reset-dir":
            payload = self._read_json()
            path = payload.get("path")
            if not isinstance(path, str) or not path.strip():
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_path"})
                return
            target = Path(path)
            try:
                if target.exists():
                    shutil.rmtree(target)
                target.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                self._send_json(HTTPStatus.OK, {"ok": False, "error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "path": path})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), MultipassBridgeHandler)
    server.bridge_token = args.token
    server.serve_forever()


if __name__ == "__main__":
    main()
