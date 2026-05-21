from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from shlex import quote
from typing import Literal

from app.config import settings
from app.services.runtime.ssh_runtime import get_runtime_terminal_manager, runtime_configured
from app.services.runtime.workspace import normalize_workspaces_root

CheckStatus = Literal["pass", "fail", "warn", "skip"]


@dataclass(frozen=True, slots=True)
class RuntimeStatusCheck:
    id: str
    label: str
    status: CheckStatus
    detail: str | None = None
    required: bool = True
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
            "required": self.required,
            "duration_ms": self.duration_ms,
        }


def _target_payload() -> dict[str, object | None]:
    return {
        "host": settings.runtime_ssh_host.strip() or None,
        "port": int(settings.runtime_ssh_port) if settings.runtime_ssh_host.strip() else None,
        "username": settings.runtime_ssh_username.strip() or None,
        "workspaces_dir": settings.runtime_workspaces_dir.strip() or None,
    }


def _config_checks() -> list[RuntimeStatusCheck]:
    checks: list[RuntimeStatusCheck] = []
    checks.append(
        RuntimeStatusCheck(
            id="config_ssh_host",
            label="SSH host configured",
            status="pass" if settings.runtime_ssh_host.strip() else "fail",
            detail=settings.runtime_ssh_host.strip() or "SENTINEL_RUNTIME_SSH_HOST is empty",
        )
    )
    checks.append(
        RuntimeStatusCheck(
            id="config_ssh_username",
            label="SSH username configured",
            status="pass" if settings.runtime_ssh_username.strip() else "fail",
            detail=settings.runtime_ssh_username.strip() or "SENTINEL_RUNTIME_SSH_USERNAME is empty",
        )
    )
    try:
        workspaces_root = normalize_workspaces_root(settings.runtime_workspaces_dir)
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
    key_path = settings.runtime_ssh_key_path.strip()
    password = settings.runtime_ssh_password.strip()
    checks.append(
        RuntimeStatusCheck(
            id="config_auth",
            label="SSH authentication configured",
            status="pass" if key_path or password else "fail",
            detail="key" if key_path else ("password" if password else "No key path or password configured"),
        )
    )
    return checks


def _remote_probe_script(workspaces_root: str) -> str:
    required = "bash tmux python3 bwrap git gh rg jq"
    optional = "chromium vncserver startxfce4 startplasma-x11 xdpyinfo"
    root = quote(workspaces_root)
    return f"""#!/usr/bin/env bash
set +e
emit() {{
  printf '%s\\t%s\\t%s\\t%s\\n' "$1" "$2" "$3" "$4"
}}

emit ssh_command "pass" "Remote command" "$(id -un 2>/dev/null)@$(hostname 2>/dev/null)"

root={root}
if mkdir -p "$root" 2>/tmp/sentinel-runtime-status.err; then
  tmp="$root/.sentinel-health-$$"
  if ( umask 077 && : > "$tmp" ) 2>/tmp/sentinel-runtime-status.err; then
    rm -f "$tmp"
    emit workspace_writable "pass" "Workspace writable" "$root"
  else
    emit workspace_writable "fail" "Workspace writable" "$(cat /tmp/sentinel-runtime-status.err 2>/dev/null)"
  fi
else
  emit workspace_writable "fail" "Workspace writable" "$(cat /tmp/sentinel-runtime-status.err 2>/dev/null)"
fi
rm -f /tmp/sentinel-runtime-status.err

for item in {required}; do
  if path=$(command -v "$item" 2>/dev/null); then
    emit "binary_$item" "pass" "$item" "$path"
  else
    emit "binary_$item" "fail" "$item" "not found"
  fi
done

for item in {optional}; do
  if path=$(command -v "$item" 2>/dev/null); then
    emit "binary_$item" "pass" "$item" "$path"
  else
    emit "binary_$item" "warn" "$item" "not found"
  fi
done

if command -v vncserver >/dev/null 2>&1 && command -v xdpyinfo >/dev/null 2>&1; then
  if command -v startxfce4 >/dev/null 2>&1 || command -v startplasma-x11 >/dev/null 2>&1; then
    emit desktop_stack "pass" "Desktop stack" "VNC and desktop command available"
  else
    emit desktop_stack "warn" "Desktop stack" "startxfce4 or startplasma-x11 not found"
  fi
else
  emit desktop_stack "warn" "Desktop stack" "vncserver or xdpyinfo not found"
fi
"""


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
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        check_id, status, label, detail = parts
        if status not in {"pass", "fail", "warn", "skip"}:
            continue
        checks.append(
            RuntimeStatusCheck(
                id=check_id,
                label=label,
                status=status,  # type: ignore[arg-type]
                detail=detail or None,
                required=check_id not in optional_ids,
            )
        )
    return checks


def _capabilities(checks: list[RuntimeStatusCheck]) -> dict[str, str]:
    by_id = {item.id: item.status for item in checks}

    def ready(*ids: str) -> bool:
        return all(by_id.get(item) == "pass" for item in ids)

    return {
        "shell": "ready" if ready("ssh_connect", "ssh_command", "workspace_writable", "binary_tmux", "binary_bash") else "unavailable",
        "files": "ready" if ready("ssh_connect", "workspace_writable", "binary_python3") else "unavailable",
        "git": "ready" if ready("ssh_connect", "workspace_writable", "binary_git", "binary_gh") else "unavailable",
        "port_forward": "ready" if ready("ssh_connect") else "unavailable",
        "desktop": "available" if by_id.get("desktop_stack") == "pass" else "unavailable",
        "browser": "available" if by_id.get("binary_chromium") == "pass" else "unavailable",
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


async def runtime_status_payload() -> dict[str, object]:
    checks = _config_checks()
    configured = runtime_configured() and all(
        item.status == "pass"
        for item in checks
        if item.id in {"config_ssh_host", "config_ssh_username", "config_workspaces_dir", "config_auth"}
    )
    unreachable = False
    if configured:
        manager = get_runtime_terminal_manager()
        started = time.perf_counter()
        try:
            await manager.ssh.wait_ready(timeout=5)
            checks.append(
                RuntimeStatusCheck(
                    id="ssh_connect",
                    label="SSH connection",
                    status="pass",
                    detail=f"{settings.runtime_ssh_host.strip()}:{int(settings.runtime_ssh_port)}",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            )
            result = await manager.ssh.run_script(
                _remote_probe_script(normalize_workspaces_root(settings.runtime_workspaces_dir)),
                timeout=15,
            )
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
        "target": _target_payload(),
        "checks": [item.to_dict() for item in checks],
        "capabilities": _capabilities(checks),
    }
