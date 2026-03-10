from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

RUNTIME_EXEC_SANDBOX_WORKSPACE = "/mnt"
RUNTIME_EXEC_SANDBOX_VENV = "/tmp/.venv"


class RuntimeExecSandboxError(RuntimeError):
    """Raised when confined runtime_exec sandbox execution cannot be prepared."""


@dataclass(frozen=True)
class RuntimeExecCommandPlan:
    args: list[str]
    env_overrides: dict[str, str]


def build_runtime_exec_command_args(
    *,
    command: str,
    privilege: str,
    workspace_dir: Path,
    run_dir: Path,
    use_python_venv: bool = False,
    venv_dir: Path | None = None,
) -> RuntimeExecCommandPlan:
    mode = privilege.strip().lower()
    if mode == "root":
        if os.name == "nt":
            return RuntimeExecCommandPlan(args=["cmd", "/C", command], env_overrides={})
        return RuntimeExecCommandPlan(args=["/bin/bash", "-lc", command], env_overrides={})

    if mode != "user":
        raise RuntimeExecSandboxError(f"Unsupported runtime privilege mode '{privilege}'")
    if os.name == "nt":
        raise RuntimeExecSandboxError("runtime_exec privilege=user is not supported on Windows")

    bwrap_bin = shutil.which("bwrap")
    if not bwrap_bin:
        raise RuntimeExecSandboxError(
            "runtime_exec privilege=user requires bubblewrap (bwrap) installed in the backend image"
        )

    writable = _prepare_writable_mounts(workspace_dir=workspace_dir)
    workspace = workspace_dir.resolve()
    cwd = run_dir.resolve()
    if cwd != workspace and workspace not in cwd.parents:
        raise RuntimeExecSandboxError("runtime_exec cwd must stay within workspace for confined mode")
    relative_cwd = cwd.relative_to(workspace).as_posix()
    sandbox_workspace = RUNTIME_EXEC_SANDBOX_WORKSPACE
    sandbox_cwd = sandbox_workspace if relative_cwd in {"", "."} else f"{sandbox_workspace}/{relative_cwd}"
    return RuntimeExecCommandPlan(
        args=[
            bwrap_bin,
            "--die-with-parent",
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-ipc",
            "--ro-bind",
            "/",
            "/",
            "--proc",
            "/proc",
            "--dev-bind",
            "/dev",
            "/dev",
            "--bind",
            str(workspace_dir),
            sandbox_workspace,
            "--bind",
            str(writable["tmp"]),
            "/tmp",
            "--bind",
            str(writable["var_tmp"]),
            "/var/tmp",
            "--bind",
            str(writable["dev_shm"]),
            "/dev/shm",
            "--bind",
            str(writable["run_lock"]),
            "/run/lock",
            *(
                _venv_mount_args(
                    use_python_venv=use_python_venv,
                    venv_dir=venv_dir,
                )
            ),
            "--chdir",
            sandbox_cwd,
            "--",
            "/bin/bash",
            "-lc",
            command,
        ],
        env_overrides={
            "HOME": sandbox_workspace,
            "PWD": sandbox_cwd,
            "TMPDIR": "/tmp",
        },
    )


def _prepare_writable_mounts(*, workspace_dir: Path) -> dict[str, Path]:
    # Keep user-mode writable scratch space inside the session workspace tree.
    sandbox_root = workspace_dir / ".runtime" / "sandbox"
    paths = {
        "tmp": sandbox_root / "tmp",
        "var_tmp": sandbox_root / "var_tmp",
        "dev_shm": sandbox_root / "dev_shm",
        "run_lock": sandbox_root / "run_lock",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
        # Sticky-bit world writable temp dirs avoid accidental permission deadlocks.
        path.chmod(0o1777)
    return paths


def _venv_mount_args(*, use_python_venv: bool, venv_dir: Path | None) -> list[str]:
    if not use_python_venv:
        return []
    if venv_dir is None:
        raise RuntimeExecSandboxError("runtime_exec sandbox requires venv_dir when use_python_venv=true")
    resolved = venv_dir.resolve()
    if not resolved.exists():
        raise RuntimeExecSandboxError(f"runtime_exec venv path does not exist: {resolved}")
    return [
        "--bind",
        str(resolved),
        RUNTIME_EXEC_SANDBOX_VENV,
    ]
