from __future__ import annotations

from dataclasses import dataclass

from app.services.runtime.ssh_client import SSHClient


@dataclass(frozen=True, slots=True)
class RuntimeEnvironment:
    os: str
    sandbox: str

    @property
    def supported(self) -> bool:
        return self.os in {"linux", "darwin"} and self.sandbox in {"bubblewrap", "seatbelt"}


def normalize_remote_os(value: str) -> str:
    lowered = value.strip().lower()
    if lowered == "linux":
        return "linux"
    if lowered == "darwin":
        return "darwin"
    if not lowered:
        return "unknown"
    return "unsupported"


def expected_sandbox_for_os(os_name: str) -> str:
    if os_name == "linux":
        return "bubblewrap"
    if os_name == "darwin":
        return "seatbelt"
    return "unavailable"


async def detect_runtime_environment(ssh: SSHClient) -> RuntimeEnvironment:
    uname = await ssh.run("uname -s 2>/dev/null || true", timeout=10)
    os_name = normalize_remote_os(uname.stdout or "")
    if os_name == "linux":
        probe = await ssh.run("command -v bwrap >/dev/null 2>&1 && echo yes || echo no", timeout=10)
        sandbox = "bubblewrap" if (probe.stdout or "").strip() == "yes" else "unavailable"
    elif os_name == "darwin":
        probe = await ssh.run("command -v sandbox-exec >/dev/null 2>&1 && echo yes || echo no", timeout=10)
        sandbox = "seatbelt" if (probe.stdout or "").strip() == "yes" else "unavailable"
    else:
        sandbox = "unavailable"
    return RuntimeEnvironment(os=os_name, sandbox=sandbox)
