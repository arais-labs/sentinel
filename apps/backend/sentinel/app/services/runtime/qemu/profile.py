from __future__ import annotations

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True, slots=True)
class QemuProfile:
    image: str
    ssh_key_path: str
    cpus: int
    memory_mb: int
    run_root: str
    workspace_root: str
    ssh_port: int
    vnc_port: int
    cdp_port: int
    host: str
    public_host: str
    share_tag: str
    share_mount: str


def build_qemu_profile() -> QemuProfile:
    image = (settings.runtime_qemu_image or "").strip()
    key_path = (settings.runtime_qemu_ssh_key_path or "").strip()
    if not image:
        raise ValueError("RUNTIME_QEMU_IMAGE is required for the 'qemu' backend")
    if not key_path:
        raise ValueError("RUNTIME_QEMU_SSH_KEY_PATH is required for the 'qemu' backend")
    workspace_root = (settings.runtime_qemu_workspace_root or settings.runtime_workspaces_host_dir).strip()
    if not workspace_root:
        raise ValueError("RUNTIME_QEMU_WORKSPACE_ROOT or RUNTIME_WORKSPACES_HOST_DIR is required")
    return QemuProfile(
        image=image,
        ssh_key_path=key_path,
        cpus=max(1, int(settings.runtime_qemu_cpus)),
        memory_mb=max(1024, int(settings.runtime_qemu_memory_mb)),
        run_root=(settings.runtime_qemu_run_root or "/data/runtime/qemu").strip(),
        workspace_root=workspace_root,
        ssh_port=int(settings.runtime_qemu_ssh_port),
        vnc_port=int(settings.runtime_qemu_vnc_port),
        cdp_port=int(settings.runtime_qemu_cdp_port),
        host=(settings.runtime_qemu_host or "host.docker.internal").strip(),
        public_host=(settings.runtime_qemu_public_host or "localhost").strip(),
        share_tag=(settings.runtime_qemu_share_tag or "sentinel-host-workspaces").strip(),
        share_mount=(settings.runtime_qemu_share_mount or "/mnt/sentinel-host-workspaces").strip(),
    )
