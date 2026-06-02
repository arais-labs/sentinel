from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services.runtime.status import _config_checks, _summary


def _runtime(provider: str, workspaces_dir: str, **overrides: object) -> SimpleNamespace:
    base = dict(
        name=provider,
        provider=provider,
        host=None,
        port=None,
        username="tester",
        auth_type=None,
        workspaces_dir=workspaces_dir,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_local_config_checks_skip_ssh_and_pass(tmp_path: Path) -> None:
    runtime = _runtime("local", str(tmp_path))
    checks = {c.id: c for c in _config_checks(runtime, None)}

    assert "config_ssh_host" not in checks
    assert "config_ssh_username" not in checks
    assert "config_auth" not in checks
    assert checks["config_execution"].status == "pass"
    assert checks["config_execution"].detail == "This Mac (direct, no SSH)"
    assert all(c.status == "pass" for c in checks.values())


def test_ssh_config_checks_still_present(tmp_path: Path) -> None:
    runtime = _runtime("ssh", str(tmp_path), host="1.2.3.4", auth_type="private_key")
    checks = {c.id: c for c in _config_checks(runtime, None)}

    assert checks["config_ssh_host"].status == "pass"
    assert checks["config_ssh_username"].status == "pass"
    assert checks["config_auth"].status == "pass"
    assert "config_execution" not in checks


def test_summary_wording_is_provider_aware() -> None:
    assert _summary("ready", local=True) == "Local runtime is ready."
    assert _summary("not_configured", local=True) == "Local runtime is not configured."
    assert _summary("ready", local=False) == "SSH runtime is ready."
