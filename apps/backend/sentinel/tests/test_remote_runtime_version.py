import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from app.services.runtime.remote_mac import available_version, runtime_assets
from app.services.runtime import workspace_containers


@pytest.mark.asyncio
async def test_version_comparison_does_not_prepare_and_invalidates_changed_assets(
    monkeypatch, tmp_path
):
    helper, kernel = tmp_path / "helper", tmp_path / "kernel"
    helper.write_bytes(b"helper")
    kernel.write_bytes(b"kernel")
    assets = {
        "executable": str(helper),
        "kernel": str(kernel),
        "initImage": "init",
        "workspaceImage": "workspace",
    }
    (tmp_path / "graphics").mkdir()
    for path, name in runtime_assets(assets)[2:]:
        path.write_bytes(name.encode())
    request = AsyncMock(return_value=assets)
    monkeypatch.setattr(workspace_containers, "local_request", request)
    expected = hashlib.sha256(
        json.dumps(
            [
                *[
                    hashlib.sha256(path.read_bytes()).hexdigest()
                    for path, _ in runtime_assets(assets)
                ],
                "init",
                "workspace",
            ]
        ).encode()
    ).hexdigest()[:16]
    assert await available_version() == expected
    request.assert_awaited_once_with("deployment", prepare=False)
    helper.write_bytes(b"updated helper")
    assert await available_version() != expected
    before = await available_version()
    (tmp_path / "graphics/libEGL.dylib").write_bytes(b"updated graphics")
    assert await available_version() != before


@pytest.mark.asyncio
async def test_missing_assets_have_unknown_version_without_downloading(monkeypatch, tmp_path):
    request = AsyncMock(
        return_value={"executable": str(tmp_path / "missing"), "kernel": "/missing"}
    )
    monkeypatch.setattr(workspace_containers, "local_request", request)
    assert await available_version() is None
    request.assert_awaited_once_with("deployment", prepare=False)
