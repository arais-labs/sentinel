from __future__ import annotations

from typing import Protocol

from app.services.runtime.qemu.profile import QemuProfile


class QemuBridgeError(RuntimeError):
    pass


class QemuControl(Protocol):
    async def health(self) -> dict[str, object]: ...

    async def ensure_base_image(self, profile: QemuProfile) -> None: ...

    async def base_image_status(self, profile: QemuProfile) -> dict[str, object]: ...

    async def ensure_dir(self, path: str) -> None: ...

    async def ensure_vm(self, profile: QemuProfile) -> None: ...

    async def stop_vm(self, profile: QemuProfile) -> None: ...

    async def status(self, profile: QemuProfile) -> dict[str, object]: ...
