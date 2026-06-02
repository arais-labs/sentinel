from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.models.manager import Runtime
from app.schemas.runtimes import RuntimeProviderConfig
from app.services.runtime import providers
from app.services.runtime.providers import LocalRuntimeProvider, RuntimeJob


def _runtime(name: str = "local", workspaces_dir: str | None = None) -> Runtime:
    return Runtime(
        id=uuid4(),
        name=name,
        provider="local",
        status="creating",
        provider_config={},
        provider_state={},
        workspaces_dir=workspaces_dir,
    )


def _job(runtime: Runtime, action: str = "create") -> RuntimeJob:
    return RuntimeJob(id=uuid4(), runtime_id=runtime.id, provider="local", action=action)


def _force_env(
    monkeypatch: pytest.MonkeyPatch, *, desktop: bool = True, system: str = "Darwin"
) -> None:
    monkeypatch.setattr(providers, "is_desktop_app", lambda: desktop)
    monkeypatch.setattr(providers.platform, "system", lambda: system)


def test_capability_requires_desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_env(monkeypatch, desktop=False)
    cap = LocalRuntimeProvider().capability()
    assert cap.available is False
    assert cap.missing == ["Desktop app"]
    assert cap.has_lifecycle is False


def test_capability_requires_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_env(monkeypatch, system="Linux")
    cap = LocalRuntimeProvider().capability()
    assert cap.available is False
    assert cap.missing == ["macOS"]


def test_capability_available_on_desktop_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_env(monkeypatch)
    cap = LocalRuntimeProvider().capability()
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
    await LocalRuntimeProvider().create(runtime, RuntimeProviderConfig(), _job(runtime))

    # No fake SSH endpoint is stored — the UI shows "This Mac", not an IP.
    assert runtime.host is None
    assert runtime.port is None
    assert runtime.username == "tester"
    assert runtime.workspaces_dir == str(tmp_path / "sentinel" / "workspaces")
    assert (tmp_path / "sentinel" / "workspaces").is_dir()
    assert runtime.auth_type is None
    assert runtime.encrypted_secret is None
    assert runtime.provider_state == {"local": True}
    # No SSH key generated, nothing written to authorized_keys.
    assert not (tmp_path / ".ssh").exists()


@pytest.mark.asyncio
async def test_create_respects_provided_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _force_env(monkeypatch)
    monkeypatch.setenv("USER", "tester")
    monkeypatch.setenv("LOGNAME", "tester")
    custom = tmp_path / "projects" / "ws"

    runtime = _runtime(workspaces_dir=str(custom))
    await LocalRuntimeProvider().create(runtime, RuntimeProviderConfig(), _job(runtime))

    assert runtime.workspaces_dir == str(custom)
    assert custom.is_dir()


@pytest.mark.asyncio
async def test_delete_revokes_legacy_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    blob = "ssh-ed25519 LEGACYKEYDATA"
    (ssh_dir / "authorized_keys").write_text(
        f'from="127.0.0.1,::1" {blob} sentinel-local\nssh-ed25519 OTHERKEYDATA someone@host\n'
    )

    runtime = _runtime()
    runtime.provider_state = {"local": True, "authorized_key_blob": blob}
    await LocalRuntimeProvider().delete(runtime, _job(runtime, action="delete"))

    remaining = (ssh_dir / "authorized_keys").read_text()
    assert "LEGACYKEYDATA" not in remaining  # the runtime's key is revoked
    assert "OTHERKEYDATA" in remaining  # unrelated keys are preserved


@pytest.mark.asyncio
async def test_delete_is_noop_without_legacy_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    runtime = _runtime()
    runtime.provider_state = {"local": True}
    # No ~/.ssh on disk — must not raise.
    await LocalRuntimeProvider().delete(runtime, _job(runtime, action="delete"))


def test_name_with_newline_is_rejected_by_schema() -> None:
    from app.schemas.runtimes import RuntimeCreateRequest

    with pytest.raises(ValueError, match="control characters"):
        RuntimeCreateRequest(name="evil\nsecond-line", provider="local")


def test_local_provider_is_registered_and_managed() -> None:
    assert providers.runtime_provider_service.is_managed("local") is True
    assert providers.runtime_provider_service.is_managed("ssh") is False
    caps = {c.provider: c for c in providers.runtime_provider_service.capabilities().providers}
    assert "local" in caps
    assert caps["local"].has_lifecycle is False
