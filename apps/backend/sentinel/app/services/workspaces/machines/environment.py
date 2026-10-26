from __future__ import annotations

import platform
from dataclasses import dataclass

from app.services.workspaces.transport import guest
from app.services.workspaces.transport.local import MachineTransport
from app.services.workspaces.guest_commands import load_guest_command
from app.services.workspaces.machines.transport import machine_transport


@dataclass(frozen=True, slots=True)
class MachineEnvironment:
    os: str
    sandbox: str
    home: str = ""

    @property
    def supported(self) -> bool:
        return self.os == "linux" and self.sandbox == "vm"


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
    return "vm" if os_name == "linux" else "unavailable"


async def detect_machine_environment(ssh: MachineTransport) -> MachineEnvironment:

    if isinstance(ssh, guest.GuestTransport):
        await ssh.wait_ready()
        return MachineEnvironment(os="linux", sandbox="vm", home="/root")
    result = await ssh.run_script(load_guest_command("common/environment.sh"), timeout=10)
    fields = (result.stdout or "").split("\0")
    if result.exit_status != 0 or len(fields) != 3:
        raise RuntimeError("Could not detect the machine environment")
    os_name, sandbox, home = fields
    return MachineEnvironment(os=normalize_remote_os(os_name), sandbox=sandbox, home=home)


async def machine_os(machine) -> str:
    if machine.provider == "local":
        return normalize_remote_os(platform.system())
    transport = machine_transport(machine)
    try:
        return (await detect_machine_environment(transport)).os
    finally:
        await transport.close()
