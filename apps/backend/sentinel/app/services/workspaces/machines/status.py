from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from app.services.workspaces.machines.environment import expected_sandbox_for_os
from app.services.workspaces.guest_commands import load_guest_command
from app.services.workspaces.managers import (
    _resolve_workspace,
    get_terminal_manager,
    workspace_attached,
)
from app.services.workspaces.machines.registry import (
    InstanceRuntimeNotConfigured,
    MachineErrorBase,
)

CheckStatus = Literal["pass", "fail", "warn", "skip"]


@dataclass(frozen=True, slots=True)
class StatusCheck:
    id: str
    label: str
    status: CheckStatus
    detail: str | None = None
    hint: str | None = None
    required: bool = True
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
            "hint": self.hint,
            "required": self.required,
            "duration_ms": self.duration_ms,
        }


def _machine_payload(runtime: object | None) -> dict[str, object | None]:
    if runtime is None:
        return {"host": None, "port": None, "username": None}
    return {
        "name": getattr(runtime, "name", None),
        "provider": getattr(runtime, "provider", None),
        "host": getattr(runtime, "host", None),
        "port": getattr(runtime, "port", None),
        "username": getattr(runtime, "username", None),
    }


def _is_local(runtime: object | None) -> bool:
    return getattr(runtime, "provider", None) == "local"


def _config_checks(runtime: object | None, error: str | None) -> list[StatusCheck]:
    if runtime is None:
        return [
            StatusCheck(
                id="config_runtime",
                label="Machine selected",
                status="fail",
                detail=error or "No runtime selected for this instance.",
            )
        ]
    checks: list[StatusCheck] = []
    checks.append(
        StatusCheck(
            id="config_runtime",
            label="Machine selected",
            status="pass",
            detail=getattr(runtime, "name", None),
        )
    )
    local = _is_local(runtime)
    if local:
        checks.append(
            StatusCheck(
                id="config_execution",
                label="Execution mode",
                status="pass",
                detail="Private workspace VM on this Mac",
            )
        )
    else:
        checks.append(
            StatusCheck(
                id="config_ssh_host",
                label="SSH host configured",
                status="pass" if str(getattr(runtime, "host", "")).strip() else "fail",
                detail=str(getattr(runtime, "host", "")).strip() or "Machine host is empty",
            )
        )
        checks.append(
            StatusCheck(
                id="config_ssh_username",
                label="SSH username configured",
                status="pass" if str(getattr(runtime, "username", "")).strip() else "fail",
                detail=str(getattr(runtime, "username", "")).strip() or "Machine username is empty",
            )
        )
    if not local:
        checks.append(
            StatusCheck(
                id="config_auth",
                label="SSH authentication configured",
                status=(
                    "pass"
                    if getattr(runtime, "auth_type", "") in {"private_key", "password"}
                    else "fail"
                ),
                detail=str(getattr(runtime, "auth_type", "") or "No SSH auth configured"),
            )
        )
    return checks


def _remote_probe_script(workspace_location: str) -> tuple[str, list[str]]:
    return load_guest_command("common/status/probe.sh"), [workspace_location]


def _parse_remote_checks(stdout: str) -> list[StatusCheck]:
    checks: list[StatusCheck] = []
    optional_ids = {
        "binary_chromium",
        "binary_Xorg",
        "binary_xfce4-session",
        "binary_dbus-run-session",
        "binary_xdpyinfo",
        "desktop_stack",
    }
    for line in stdout.splitlines():
        parts = line.split("\t", 4)
        if len(parts) not in {4, 5}:
            continue
        check_id, status, label, detail = parts[:4]
        hint = parts[4] if len(parts) == 5 else ""
        if status not in {"pass", "fail", "warn", "skip"}:
            continue
        checks.append(
            StatusCheck(
                id=check_id,
                label=label,
                status=status,  # type: ignore[arg-type]
                detail=detail or None,
                hint=hint or None,
                required=check_id not in optional_ids,
            )
        )
    return checks


def _detected_os(checks: list[StatusCheck]) -> str:
    detail = next((item.detail for item in checks if item.id == "os"), None)
    if detail in {"linux", "darwin", "unsupported", "unknown"}:
        return detail
    return "unknown"


def _detected_sandbox(checks: list[StatusCheck]) -> str:
    sandbox = next(
        (item.detail for item in checks if item.id == "sandbox" and item.status == "pass"), None
    )
    if sandbox in {"vm"}:
        return sandbox
    return "unavailable"


def _capabilities(checks: list[StatusCheck]) -> dict[str, str]:
    by_id = {item.id: item.status for item in checks}
    os_name = _detected_os(checks)
    sandbox = _detected_sandbox(checks)
    expected_sandbox = expected_sandbox_for_os(os_name)
    sandbox_ready = sandbox == expected_sandbox and expected_sandbox in {"vm"}
    supported_os = os_name in {"linux", "darwin"}

    def ready(*ids: str) -> bool:
        return supported_os and sandbox_ready and all(by_id.get(item) == "pass" for item in ids)

    return {
        "shell": (
            "ready"
            if ready(
                "ssh_connect", "ssh_command", "workspace_writable", "binary_tmux", "binary_bash"
            )
            else "unavailable"
        ),
        "files": (
            "ready"
            if ready("ssh_connect", "workspace_writable", "binary_python3")
            else "unavailable"
        ),
        "git": (
            "ready"
            if ready("ssh_connect", "workspace_writable", "binary_git", "binary_gh")
            else "unavailable"
        ),
        "jobs": (
            "ready"
            if ready(
                "ssh_connect", "workspace_writable", "binary_bash", "binary_python3", "binary_tmux"
            )
            else "unavailable"
        ),
        "port_forward": "ready" if ready("ssh_connect") else "unavailable",
        "desktop": (
            "ready"
            if os_name == "linux" and ready("ssh_connect", "workspace_writable", "desktop_stack")
            else "unavailable"
        ),
        "browser": (
            "ready"
            if os_name == "linux" and ready("ssh_connect", "workspace_writable", "binary_chromium")
            else "unavailable"
        ),
    }


def _overall_status(checks: list[StatusCheck], *, configured: bool, unreachable: bool) -> str:
    if not configured:
        return "not_configured"
    if unreachable:
        return "unreachable"
    required_failures = [item for item in checks if item.required and item.status != "pass"]
    if required_failures:
        return "failed"
    warnings = [item for item in checks if item.status == "warn"]
    if warnings:
        return "degraded"
    return "ready"


def _summary(status: str, *, local: bool = False) -> str:
    kind = "Local runtime" if local else "SSH runtime"
    return {
        "ready": f"{kind} is ready.",
        "degraded": f"{kind} is usable, but optional capabilities are missing or degraded.",
        "not_configured": f"{kind} is not configured.",
        "unreachable": f"{kind} is not reachable.",
        "failed": f"{kind} is configured but core checks failed.",
    }[status]


async def workspace_status_payload(
    *, instance_name: str, session_id: str | None = None
) -> dict[str, object]:
    runtime = None
    runtime_error = None
    try:
        runtime, binding = await _resolve_workspace(
            session_id=session_id, instance_name=instance_name, session_factory=None
        )
    except InstanceRuntimeNotConfigured as exc:
        runtime_error = str(exc)
    except MachineErrorBase as exc:
        runtime_error = str(exc)
    checks = _config_checks(runtime, runtime_error)
    required_config_ids = (
        {"config_runtime", "config_execution"}
        if _is_local(runtime)
        else {
            "config_runtime",
            "config_ssh_host",
            "config_ssh_username",
            "config_auth",
        }
    )
    configured = await workspace_attached(
        session_id=session_id, instance_name=instance_name
    ) and all(item.status == "pass" for item in checks if item.id in required_config_ids)
    unreachable = False
    if configured:
        manager = await get_terminal_manager(session_id=session_id, instance_name=instance_name)
        local = _is_local(runtime)
        connect_label = "Local execution" if local else "SSH connection"
        if local:
            connect_detail: str | None = "This Mac"
        elif runtime is not None:
            connect_detail = f"{runtime.host}:{int(runtime.port)}"
        else:
            connect_detail = None
        started = time.perf_counter()
        try:
            await manager.ssh.wait_ready(timeout=5)
            checks.append(
                StatusCheck(
                    id="ssh_connect",
                    label=connect_label,
                    status="pass",
                    detail=connect_detail,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            )
            if "shell" not in binding.features:
                # The probe is a shell script; the guest's tools are not available yet.
                checks.append(
                    StatusCheck(
                        id="workspace_features",
                        label="Workspace features",
                        status="warn",
                        detail=f"Terminals, files and agent tools are not available on "
                        f"{binding.distribution} workspaces yet.",
                        required=False,
                    )
                )
            else:
                script, args = _remote_probe_script(binding.directory)
                result = await manager.ssh.run_script(script, args=args, timeout=15)
                if result.exit_status in {0, None}:
                    checks.extend(_parse_remote_checks(result.stdout))
                else:
                    detail = (result.stderr or result.stdout or "probe failed").strip()[:500]
                    checks.append(
                        StatusCheck(
                            id="remote_probe",
                            label="Remote diagnostics",
                            status="fail",
                            detail=detail,
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            unreachable = True
            checks.append(
                StatusCheck(
                    id="ssh_connect",
                    label=connect_label,
                    status="fail",
                    detail=str(exc),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            )
    status = _overall_status(checks, configured=configured, unreachable=unreachable)
    return {
        "status": status,
        "summary": _summary(status, local=_is_local(runtime)),
        "checked_at": datetime.now(UTC),
        "os": _detected_os(checks),
        "sandbox": _detected_sandbox(checks),
        "machine": _machine_payload(runtime),
        "checks": [item.to_dict() for item in checks],
        "capabilities": _capabilities(checks),
    }
