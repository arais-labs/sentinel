from __future__ import annotations

from pathlib import Path

import httpx

from app.config import settings
from app.services.runtime.qemu.controls.base import QemuBridgeError
from app.services.runtime.qemu.profile import QemuProfile


class BridgeQemuControl:
    def __init__(self) -> None:
        self._bridge_url = settings.runtime_qemu_bridge_url.rstrip("/")
        self._bridge_token = (settings.runtime_qemu_bridge_token or "").strip()

    async def health(self) -> dict[str, object]:
        headers = {"X-Sentinel-Bridge-Token": self._bridge_token}
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                response = await client.get(f"{self._bridge_url}/healthz", headers=headers)
            except Exception as exc:  # noqa: BLE001
                raise QemuBridgeError("QEMU bridge is not reachable") from exc
        if response.status_code != 200:
            raise QemuBridgeError(f"QEMU bridge health check failed ({response.status_code})")
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise QemuBridgeError("QEMU bridge returned an invalid health response")
        return payload

    async def post(self, path: str, payload: dict, *, timeout_seconds: int = 120) -> dict[str, object]:
        headers = {"X-Sentinel-Bridge-Token": self._bridge_token}
        async with httpx.AsyncClient(timeout=timeout_seconds + 5) as client:
            response = await client.post(
                f"{self._bridge_url}{path}",
                headers=headers,
                json=payload,
            )
        if response.status_code != 200:
            raise QemuBridgeError(f"QEMU bridge request failed for {path} ({response.status_code})")
        result = response.json()
        if not isinstance(result, dict):
            raise QemuBridgeError(f"QEMU bridge returned invalid payload for {path}")
        if result.get("ok") is not True:
            raise QemuBridgeError(str(result.get("error") or f"QEMU bridge request failed for {path}"))
        return result

    async def ensure_dir(self, path: str) -> None:
        await self.post("/v1/ensure-dir", {"path": path}, timeout_seconds=20)

    async def ensure_base_image(self, profile: QemuProfile) -> None:
        _ = profile

    async def base_image_status(self, profile: QemuProfile) -> dict[str, object]:
        image = Path(profile.image)
        key = Path(profile.ssh_key_path)
        present = image.exists() and key.exists()
        return {
            "state": "ready" if present else "external",
            "image_path": str(image),
            "key_path": str(key),
            "present": present,
            "message": "QEMU base image is managed by the bridge process.",
        }

    async def ensure_vm(self, profile: QemuProfile) -> None:
        await self.post(
            "/v1/qemu/ensure",
            {
                "run_root": profile.run_root,
                "image_path": profile.image,
                "ssh_port": profile.ssh_port,
                "vnc_port": profile.vnc_port,
                "cdp_port": profile.cdp_port,
                "cpus": profile.cpus,
                "memory_mb": profile.memory_mb,
                "workspace_root": profile.workspace_root,
                "share_tag": profile.share_tag,
            },
            timeout_seconds=60,
        )

    async def stop_vm(self, profile: QemuProfile) -> None:
        await self.post("/v1/qemu/stop", {"run_root": profile.run_root}, timeout_seconds=20)

    async def status(self, profile: QemuProfile) -> dict[str, object]:
        return await self.post("/v1/qemu/status", {"run_root": profile.run_root}, timeout_seconds=10)
