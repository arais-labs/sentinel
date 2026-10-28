import hashlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.workspaces.machines.remote.worker import available_version, runtime_assets
from app.services.workspaces import lifecycle
from app.services.workspaces.machines.remote.release import runtime_version
from tests.runtime_release_assets import write_release


@pytest.mark.asyncio
async def test_version_comparison_does_not_prepare_and_invalidates_changed_assets(
    monkeypatch, tmp_path
):
    assets = write_release(tmp_path)
    request = AsyncMock(return_value=assets)
    monkeypatch.setattr(lifecycle, "local_request", request)
    files = runtime_assets(assets)
    expected = runtime_version(
        [name for _, name in files],
        [hashlib.sha256(path.read_bytes()).hexdigest() for path, _ in files],
    )
    assert await available_version() == expected
    request.assert_awaited_once_with("deployment", prepare=False)
    before = expected
    for changed in (
        "sentineld/bin/sentineld",
        "sentinel-workspace/bin/sentinel-workspace",
        "sentineld/share/catalog.json",
        "sentineld/tools/mke2fs",
    ):
        (Path(tmp_path) / changed).write_text("changed " + changed)
        assert await available_version() != before
        before = await available_version()


@pytest.mark.asyncio
async def test_missing_assets_have_unknown_version_without_downloading(monkeypatch, tmp_path):
    request = AsyncMock(
        return_value={"sentineld": str(tmp_path / "missing"), "workspace": "/missing"}
    )
    monkeypatch.setattr(lifecycle, "local_request", request)
    assert await available_version() is None
    request.assert_awaited_once_with("deployment", prepare=False)
