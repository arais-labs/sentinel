from __future__ import annotations

from app.services.workspaces.transport.local import LocalTransport, MachineTransport
from app.services.workspaces.machines.registry import ResolvedMachine
from app.services.workspaces.machines.remote.ssh import SSHClient


def machine_transport(machine: ResolvedMachine) -> MachineTransport:
    return LocalTransport() if machine.provider == "local" else SSHClient(machine.credentials())
