from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.models.manager import Machine
from app.schemas.machines import MachineProviderConfig
from app.services.runtime import providers
from app.services.runtime.providers import LocalMachineProvider, MachineJob


def _runtime(name: str = "local", workspaces_dir: str | None = None) -> Machine:
    return Machine(
        id=uuid4(),
        name=name,
        provider="local",
        status="creating",
        provider_config={},
        provider_state={},
    )


def _job(runtime: Machine, action: str = "create") -> MachineJob:
    return MachineJob(id=uuid4(), machine_id=runtime.id, provider="local", action=action)


def _force_env(monkeypatch: pytest.MonkeyPatch, *, system: str = "Darwin") -> None:
    monkeypatch.setattr(providers.platform, "system", lambda: system)


def test_capability_requires_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_env(monkeypatch, system="Linux")
    cap = LocalMachineProvider().capability()
    assert cap.available is False
    assert cap.missing == ["macOS"]


def test_capability_available_on_desktop_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_env(monkeypatch)
    cap = LocalMachineProvider().capability()
    assert cap.available is True
    assert cap.missing == []
    assert cap.has_lifecycle is False
    # No Remote Login / SSH gating anymore.
    assert "Remote Login" not in cap.detail


@pytest.mark.asyncio
async def test_create_prepares_workspace_without_ssh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _force_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USER", "tester")
    monkeypatch.setenv("LOGNAME", "tester")

    runtime = _runtime()
    await LocalMachineProvider().create(runtime, MachineProviderConfig(), _job(runtime))

    # No fake SSH endpoint is stored — the UI shows "This Mac", not an IP.
    assert runtime.host is None
    assert runtime.port is None
    assert runtime.username == "tester"
    assert not (tmp_path / "sentinel").exists()
    assert runtime.auth_type is None
    assert runtime.encrypted_secret is None
    assert runtime.provider_state == {"local": True}
    # No SSH key generated, nothing written to authorized_keys.
    assert not (tmp_path / ".ssh").exists()


@pytest.mark.asyncio
async def test_registering_machine_does_not_create_project_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _force_env(monkeypatch)
    monkeypatch.setenv("USER", "tester")
    monkeypatch.setenv("LOGNAME", "tester")
    custom = tmp_path / "projects" / "ws"

    runtime = _runtime(workspaces_dir=str(custom))
    await LocalMachineProvider().create(runtime, MachineProviderConfig(), _job(runtime))

    assert not custom.exists()


@pytest.mark.asyncio
async def test_delete_preserves_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    document = workspace / "notes.txt"
    document.write_text("Keep this workspace")
    runtime = _runtime(workspaces_dir=str(workspace))
    await LocalMachineProvider().delete(runtime, _job(runtime, action="delete"))
    assert document.read_text() == "Keep this workspace"


def test_name_with_newline_is_rejected_by_schema() -> None:
    from app.schemas.machines import MachineCreateRequest

    with pytest.raises(ValueError, match="control characters"):
        MachineCreateRequest(name="evil\nsecond-line", provider="local")


def test_local_provider_is_registered_and_managed() -> None:
    assert providers.machine_provider_service.is_managed("local") is True
    assert providers.machine_provider_service.is_managed("ssh") is False
    caps = {c.provider: c for c in providers.machine_provider_service.capabilities().providers}
    assert "local" in caps
    assert caps["local"].has_lifecycle is False
