from __future__ import annotations

from app.services.runtime.local_transport import LocalTransport, RuntimeTransport
from app.services.runtime.machines import ResolvedMachine
from app.services.runtime.ssh_client import SSHClient


def machine_transport(machine: ResolvedMachine) -> RuntimeTransport:
    return LocalTransport() if machine.provider == "local" else SSHClient(machine.credentials())
