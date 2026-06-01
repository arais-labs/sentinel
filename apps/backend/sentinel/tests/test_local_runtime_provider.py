from __future__ import annotations

import stat
from pathlib import Path
from uuid import uuid4

import pytest

from app.models.manager import Runtime
from app.schemas.runtimes import RuntimeProviderConfig
from app.services.runtime import providers
from app.services.runtime.providers import LocalRuntimeProvider, RuntimeJob


def _runtime(name: str = "local") -> Runtime:
    return Runtime(
        id=uuid4(),
        name=name,
        provider="local",
        status="creating",
        provider_config={},
        provider_state={},
    )


def _job(runtime: Runtime, action: str = "create") -> RuntimeJob:
    return RuntimeJob(id=uuid4(), runtime_id=runtime.id, provider="local", action=action)


def _force_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    desktop: bool = True,
    system: str = "Darwin",
    sshd: bool = True,
) -> None:
    monkeypatch.setattr(providers, "is_desktop_app", lambda: desktop)
    monkeypatch.setattr(providers.platform, "system", lambda: system)
    monkeypatch.setattr(providers, "_local_sshd_reachable", lambda *a, **k: sshd)


def _stub_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USER", "tester")
    monkeypatch.setenv("LOGNAME", "tester")


async def _probe_ok(self, username, private_key):  # noqa: ANN001
    return None


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


def test_capability_guides_when_remote_login_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_env(monkeypatch, sshd=False)
    cap = LocalRuntimeProvider().capability()
    assert cap.available is False
    assert cap.missing == ["macOS Remote Login"]
    assert "Remote Login" in cap.detail


def test_capability_available_when_remote_login_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_env(monkeypatch)
    cap = LocalRuntimeProvider().capability()
    assert cap.available is True
    assert cap.missing == []
    assert cap.has_lifecycle is False


@pytest.mark.asyncio
async def test_create_generates_loopback_restricted_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _force_env(monkeypatch)
    _stub_home(monkeypatch, tmp_path)
    monkeypatch.setattr(LocalRuntimeProvider, "_probe_auth", _probe_ok)

    runtime = _runtime()
    await LocalRuntimeProvider().create(runtime, RuntimeProviderConfig(), _job(runtime))

    assert runtime.host == "127.0.0.1"
    assert runtime.port == 22
    assert runtime.username == "tester"
    assert runtime.workspaces_dir == str(tmp_path / "sentinel" / "workspaces")
    assert (tmp_path / "sentinel" / "workspaces").is_dir()
    assert runtime.auth_type == "private_key"
    # Stored secret is a valid OpenSSH private key (verify by importing).
    providers.asyncssh.import_private_key(runtime.encrypted_secret)

    blob = runtime.provider_state["authorized_key_blob"]
    assert blob.startswith("ssh-ed25519 ")
    assert "authorized_key" not in runtime.provider_state  # no full multi-token line stored

    authorized = tmp_path / ".ssh" / "authorized_keys"
    content = authorized.read_text()
    # Key is present, loopback-restricted, and on a single line.
    assert 'from="127.0.0.1,::1"' in content
    assert blob in content
    assert content.count(blob) == 1
    assert all('from="127.0.0.1,::1"' in line for line in content.splitlines() if line.strip())
    assert stat.S_IMODE((tmp_path / ".ssh").stat().st_mode) == 0o700
    assert stat.S_IMODE(authorized.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_create_does_not_duplicate_authorized_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _force_env(monkeypatch)
    _stub_home(monkeypatch, tmp_path)
    monkeypatch.setattr(LocalRuntimeProvider, "_probe_auth", _probe_ok)

    # Pin one keypair so two creates produce the same key, exercising the
    # blob-based idempotent-append guard.
    fixed = providers.asyncssh.generate_private_key("ssh-ed25519")
    monkeypatch.setattr(providers.asyncssh, "generate_private_key", lambda *a, **k: fixed)

    provider = LocalRuntimeProvider()
    runtime = _runtime()
    await provider.create(runtime, RuntimeProviderConfig(), _job(runtime))
    await provider.create(runtime, RuntimeProviderConfig(), _job(runtime))

    blob = runtime.provider_state["authorized_key_blob"]
    content = (tmp_path / ".ssh" / "authorized_keys").read_text()
    assert content.count(blob) == 1


@pytest.mark.asyncio
async def test_create_refuses_when_remote_login_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _force_env(monkeypatch, sshd=False)
    _stub_home(monkeypatch, tmp_path)
    runtime = _runtime()
    with pytest.raises(providers.RuntimeProviderError, match="Remote Login"):
        await LocalRuntimeProvider().create(runtime, RuntimeProviderConfig(), _job(runtime))


@pytest.mark.asyncio
async def test_create_revokes_key_when_auth_probe_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _force_env(monkeypatch)
    _stub_home(monkeypatch, tmp_path)

    async def _probe_fail(self, username, private_key):  # noqa: ANN001
        raise providers.RuntimeProviderError("rejected for user")

    monkeypatch.setattr(LocalRuntimeProvider, "_probe_auth", _probe_fail)

    runtime = _runtime()
    with pytest.raises(providers.RuntimeProviderError, match="rejected"):
        await LocalRuntimeProvider().create(runtime, RuntimeProviderConfig(), _job(runtime))

    # The key it tentatively authorized must be rolled back, leaving no dangling grant.
    authorized = tmp_path / ".ssh" / "authorized_keys"
    assert not authorized.exists() or authorized.read_text().strip() == ""


@pytest.mark.asyncio
async def test_name_with_newline_is_rejected_by_schema() -> None:
    from app.schemas.runtimes import RuntimeCreateRequest

    with pytest.raises(ValueError, match="control characters"):
        RuntimeCreateRequest(name="evil\nsecond-line", provider="local")


@pytest.mark.asyncio
async def test_delete_removes_authorized_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _force_env(monkeypatch)
    _stub_home(monkeypatch, tmp_path)
    monkeypatch.setattr(LocalRuntimeProvider, "_probe_auth", _probe_ok)

    provider = LocalRuntimeProvider()
    runtime = _runtime()
    await provider.create(runtime, RuntimeProviderConfig(), _job(runtime))
    blob = runtime.provider_state["authorized_key_blob"]
    authorized = tmp_path / ".ssh" / "authorized_keys"
    assert blob in authorized.read_text()

    await provider.delete(runtime, _job(runtime, action="delete"))
    assert blob not in authorized.read_text()


@pytest.mark.asyncio
async def test_create_respects_provided_workspaces_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _force_env(monkeypatch)
    _stub_home(monkeypatch, tmp_path)
    monkeypatch.setattr(LocalRuntimeProvider, "_probe_auth", _probe_ok)

    runtime = _runtime()
    custom = tmp_path / "projects" / "workspaces"
    runtime.workspaces_dir = str(custom)
    await LocalRuntimeProvider().create(runtime, RuntimeProviderConfig(), _job(runtime))

    assert runtime.workspaces_dir == str(custom)
    assert custom.is_dir()


def test_local_provider_is_registered_and_managed() -> None:
    assert providers.runtime_provider_service.is_managed("local") is True
    assert providers.runtime_provider_service.is_managed("ssh") is False
    caps = {c.provider: c for c in providers.runtime_provider_service.capabilities().providers}
    assert "local" in caps
    assert caps["local"].has_lifecycle is False
    assert caps["ssh"].has_lifecycle is False
