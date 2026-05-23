from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from app.database import ManagerSessionLocal
from app.services.runtime.environment import expected_sandbox_for_os
from app.services.runtime.remote_commands import load_remote_command
from app.services.runtime.ssh_runtime import get_runtime_terminal_manager, runtime_configured
from app.services.runtime.targets import (
    InstanceRuntimeTargetNotConfigured,
    RuntimeTargetError,
    resolve_instance_runtime_target,
)
from app.services.runtime.workspace import normalize_workspaces_root

CheckStatus = Literal["pass", "fail", "warn", "skip"]


@dataclass(frozen=True, slots=True)
class RuntimeStatusCheck:
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


def _target_payload(target: object | None) -> dict[str, object | None]:
    if target is None:
        return {"host": None, "port": None, "username": None, "workspaces_dir": None}
    return {
        "name": getattr(target, "name", None),
        "host": getattr(target, "host", None),
        "port": getattr(target, "port", None),
        "username": getattr(target, "username", None),
        "workspaces_dir": getattr(target, "workspaces_dir", None),
    }


def _config_checks(target: object | None, error: str | None) -> list[RuntimeStatusCheck]:
    if target is None:
        return [
            RuntimeStatusCheck(
                id="config_runtime_target",
                label="Runtime target selected",
                status="fail",
                detail=error or "No runtime target selected for this instance.",
            )
        ]
    checks: list[RuntimeStatusCheck] = []
    checks.append(
        RuntimeStatusCheck(
            id="config_runtime_target",
            label="Runtime target selected",
            status="pass",
            detail=getattr(target, "name", None),
        )
    )
    checks.append(
        RuntimeStatusCheck(
            id="config_ssh_host",
            label="SSH host configured",
            status="pass" if str(getattr(target, "host", "")).strip() else "fail",
            detail=str(getattr(target, "host", "")).strip() or "Runtime target host is empty",
        )
    )
    checks.append(
        RuntimeStatusCheck(
            id="config_ssh_username",
            label="SSH username configured",
            status="pass" if str(getattr(target, "username", "")).strip() else "fail",
            detail=str(getattr(target, "username", "")).strip() or "Runtime target username is empty",
        )
    )
    try:
        workspaces_root = normalize_workspaces_root(str(getattr(target, "workspaces_dir", "")))
        checks.append(
            RuntimeStatusCheck(
                id="config_workspaces_dir",
                label="Workspaces directory configured",
                status="pass",
                detail=workspaces_root,
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            RuntimeStatusCheck(
                id="config_workspaces_dir",
                label="Workspaces directory configured",
                status="fail",
                detail=str(exc),
            )
        )
    checks.append(
        RuntimeStatusCheck(
            id="config_auth",
            label="SSH authentication configured",
            status="pass" if getattr(target, "auth_type", "") in {"private_key", "password"} else "fail",
            detail=str(getattr(target, "auth_type", "") or "No SSH auth configured"),
        )
    )
    return checks


def _remote_probe_script(workspaces_root: str) -> tuple[str, list[str]]:
    return load_remote_command("common/status/probe.sh"), [workspaces_root]


def _parse_remote_checks(stdout: str) -> list[RuntimeStatusCheck]:
    checks: list[RuntimeStatusCheck] = []
    optional_ids = {
        "binary_chromium",
        "binary_vncserver",
        "binary_startxfce4",
        "binary_startplasma-x11",
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
            RuntimeStatusCheck(
                id=check_id,
                label=label,
                status=status,  # type: ignore[arg-type]
                detail=detail or None,
                hint=hint or None,
                required=check_id not in optional_ids,
            )
        )
    return checks


def _detected_os(checks: list[RuntimeStatusCheck]) -> str:
    detail = next((item.detail for item in checks if item.id == "os"), None)
    if detail in {"linux", "darwin", "unsupported", "unknown"}:
        return detail
    return "unknown"


def _detected_sandbox(checks: list[RuntimeStatusCheck]) -> str:
    sandbox = next((item.detail for item in checks if item.id == "sandbox" and item.status == "pass"), None)
    if sandbox in {"bubblewrap", "seatbelt"}:
        return sandbox
    return "unavailable"


def _capabilities(checks: list[RuntimeStatusCheck]) -> dict[str, str]:
    by_id = {item.id: item.status for item in checks}
    os_name = _detected_os(checks)
    sandbox = _detected_sandbox(checks)
    expected_sandbox = expected_sandbox_for_os(os_name)
    sandbox_ready = sandbox == expected_sandbox and expected_sandbox in {"bubblewrap", "seatbelt"}
    supported_os = os_name in {"linux", "darwin"}

    def ready(*ids: str) -> bool:
        return supported_os and sandbox_ready and all(by_id.get(item) == "pass" for item in ids)

    return {
        "shell": "ready" if ready("ssh_connect", "ssh_command", "workspace_writable", "binary_tmux", "binary_bash") else "unavailable",
        "files": "ready" if ready("ssh_connect", "workspace_writable", "binary_python3") else "unavailable",
        "git": "ready" if ready("ssh_connect", "workspace_writable", "binary_git", "binary_gh") else "unavailable",
        "jobs": "ready" if ready("ssh_connect", "workspace_writable", "binary_bash", "binary_python3", "binary_tmux") else "unavailable",
        "port_forward": "ready" if ready("ssh_connect") else "unavailable",
        "desktop": "ready" if os_name == "linux" and ready("ssh_connect", "workspace_writable", "desktop_stack") else "unavailable",
        "browser": "ready" if os_name == "linux" and ready("ssh_connect", "workspace_writable", "desktop_stack", "binary_chromium") else "unavailable",
    }


def _overall_status(checks: list[RuntimeStatusCheck], *, configured: bool, unreachable: bool) -> str:
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


def _summary(status: str) -> str:
    return {
        "ready": "SSH runtime is ready.",
        "degraded": "SSH runtime is usable, but optional capabilities are missing or degraded.",
        "not_configured": "SSH runtime is not configured.",
        "unreachable": "SSH runtime is not reachable.",
        "failed": "SSH runtime is configured but core checks failed.",
    }[status]


async def runtime_status_payload(*, instance_name: str) -> dict[str, object]:
    target = None
    target_error = None
    try:
        async with ManagerSessionLocal() as db:
            target = await resolve_instance_runtime_target(db, instance_name=instance_name)
    except InstanceRuntimeTargetNotConfigured as exc:
        target_error = str(exc)
    except RuntimeTargetError as exc:
        target_error = str(exc)
    checks = _config_checks(target, target_error)
    configured = await runtime_configured(instance_name=instance_name) and all(
        item.status == "pass"
        for item in checks
        if item.id in {"config_runtime_target", "config_ssh_host", "config_ssh_username", "config_workspaces_dir", "config_auth"}
    )
    unreachable = False
    if configured:
        manager = await get_runtime_terminal_manager(instance_name=instance_name)
        started = time.perf_counter()
        try:
            await manager.ssh.wait_ready(timeout=5)
            checks.append(
                RuntimeStatusCheck(
                    id="ssh_connect",
                    label="SSH connection",
                    status="pass",
                    detail=f"{target.host}:{int(target.port)}" if target is not None else None,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            )
            script, args = _remote_probe_script(normalize_workspaces_root(target.workspaces_dir if target else ""))
            result = await manager.ssh.run_script(script, args=args, timeout=15)
            if result.exit_status in {0, None}:
                checks.extend(_parse_remote_checks(result.stdout))
            else:
                checks.append(
                    RuntimeStatusCheck(
                        id="remote_probe",
                        label="Remote diagnostics",
                        status="fail",
                        detail=(result.stderr or result.stdout or "probe failed").strip()[:500],
                    )
                )
        except Exception as exc:  # noqa: BLE001
            unreachable = True
            checks.append(
                RuntimeStatusCheck(
                    id="ssh_connect",
                    label="SSH connection",
                    status="fail",
                    detail=str(exc),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            )
    status = _overall_status(checks, configured=configured, unreachable=unreachable)
    return {
        "status": status,
        "summary": _summary(status),
        "checked_at": datetime.now(UTC),
        "os": _detected_os(checks),
        "sandbox": _detected_sandbox(checks),
        "target": _target_payload(target),
        "checks": [item.to_dict() for item in checks],
        "capabilities": _capabilities(checks),
    }
