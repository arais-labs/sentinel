from __future__ import annotations

from dataclasses import dataclass

from app.services.runtime import container_transport
from app.services.runtime.local_transport import RuntimeTransport
from app.services.runtime.guest_commands import load_guest_command


@dataclass(frozen=True, slots=True)
class RuntimeEnvironment:
    os: str
    sandbox: str
    home: str = ""

    @property
    def supported(self) -> bool:
        return self.os == "linux" and self.sandbox == "container"


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
    return "container" if os_name == "linux" else "unavailable"


async def detect_runtime_environment(ssh: RuntimeTransport) -> RuntimeEnvironment:

    if isinstance(ssh, container_transport.ContainerTransport):
        await ssh.wait_ready()
        return RuntimeEnvironment(os="linux", sandbox="container", home="/root")
    result = await ssh.run_script(load_guest_command("common/environment.sh"), timeout=10)
    fields = (result.stdout or "").split("\0")
    if result.exit_status != 0 or len(fields) != 3:
        raise RuntimeError("Could not detect the machine environment")
    os_name, sandbox, home = fields
    return RuntimeEnvironment(os=normalize_remote_os(os_name), sandbox=sandbox, home=home)
