import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.workspaces import metrics as module
from tests.test_workspaces import workspace_app as workspace_app
from tests.test_workspace_removal import linked_workspace

SAMPLE = """BOOT
boot-one
UPTIME
100.0 0
CPU
cpu 100 0 100 700 100 0 0 0 500 0
MEMORY
MemTotal: 2048 kB
MemFree: 100 kB
MemAvailable: 1024 kB
ROUTES
Iface Destination Gateway Flags
eth0 00000000 01000000 0003
NETWORK
Inter-| Receive | Transmit
 lo: 99999 0 0 0 0 0 0 0 99999 0 0 0 0 0 0 0
 eth0: 1000 0 0 0 0 0 0 0 2000 0 0 0 0 0 0 0
 docker0: 88888 0 0 0 0 0 0 0 88888 0 0 0 0 0 0 0
DISK
Filesystem 1024-blocks Used Available Capacity Mounted on
/dev/vdb 10000 4000 6000 40% /
"""


def test_guest_counters_exclude_double_counting_and_reclaimable_memory():
    sample = module.parse_sample(SAMPLE)
    assert sample["cpu_total"] == 1000
    assert sample["cpu_idle"] == 800
    assert sample["memory_used_bytes"] == 1024 * 1024
    assert sample["network_received_bytes"] == 1000
    assert sample["disk_used_bytes"] == 4000 * 1024
    assert module.metrics(sample, None)["cpu_percent"] is None


# Windows' MSYS2 /proc: four CPU counters, no MemAvailable, no boot id, routes or network counters.
MSYS2_SAMPLE = """BOOT
btime 1791964633
UPTIME
49.54 30.13
CPU
cpu 33108 0 44295 120420
MEMORY
MemTotal:        8373360 kB
MemFree:         5148604 kB
SwapTotal:       1966080 kB
SwapFree:        1966080 kB
ROUTES
NETWORK
DISK
Filesystem     1024-blocks     Used Available Capacity Mounted on
C:/msys64         33269756 24086008   9183748      73% /
"""


def test_a_proc_without_routes_or_available_memory_reports_what_it_has():
    sample = module.parse_sample(MSYS2_SAMPLE)
    assert sample["boot"] == "btime 1791964633"
    assert sample["cpu_total"] == 33108 + 44295 + 120420 and sample["cpu_idle"] == 120420
    assert sample["memory_used_bytes"] == (8373360 - 5148604) * 1024
    assert sample["network_interface"] is None and sample["network_received_bytes"] is None
    assert sample["disk_used_bytes"] == 24086008 * 1024
    later = {**sample, "uptime": 51.54, "cpu_total": sample["cpu_total"] + 100}
    later["cpu_idle"] = sample["cpu_idle"] + 75
    result = module.metrics(later, sample)
    assert result["cpu_percent"] == 25.0 and result["network_receive_bytes_per_second"] is None


def test_rates_and_restart_counter_resets():
    before = module.parse_sample(SAMPLE)
    after = {
        **before,
        "uptime": 102,
        "cpu_total": 1200,
        "cpu_idle": 900,
        "network_received_bytes": 3000,
    }
    result = module.metrics(after, before)
    assert result["cpu_percent"] == 50
    assert result["network_receive_bytes_per_second"] == 1000
    assert result["network_send_bytes_per_second"] == 0
    assert module.metrics({**after, "boot": "new-boot"}, before)["cpu_percent"] is None
    assert (
        module.metrics({**after, "network_received_bytes": 0}, before)[
            "network_receive_bytes_per_second"
        ]
        is None
    )


@pytest.mark.asyncio
async def test_concurrent_viewers_share_probe_and_cache(monkeypatch):
    monkeypatch.setattr(
        module.lifecycle, "statuses", AsyncMock(return_value={"workspace": {"state": "running"}})
    )
    probe = AsyncMock(return_value={"exitCode": 0, "stdout": SAMPLE})
    monkeypatch.setattr(module.lifecycle, "request", probe)
    service = module.WorkspaceMetrics()
    results = await asyncio.gather(*(service.get("workspace") for _ in range(10)))
    assert all(item == results[0] for item in results)
    await service.get("workspace")
    probe.assert_awaited_once()


@pytest.mark.asyncio
async def test_stopped_workspace_is_not_started_or_probed(monkeypatch):
    monkeypatch.setattr(
        module.lifecycle, "statuses", AsyncMock(return_value={"workspace": {"state": "stopped"}})
    )
    probe = AsyncMock()
    monkeypatch.setattr(module.lifecycle, "request", probe)
    assert (await module.WorkspaceMetrics().get("workspace"))["state"] == "stopped"
    probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_probe_returns_unavailable_without_fake_usage(monkeypatch):
    monkeypatch.setattr(
        module.lifecycle, "statuses", AsyncMock(return_value={"workspace": {"state": "running"}})
    )
    monkeypatch.setattr(module.lifecycle, "request", AsyncMock(side_effect=TimeoutError))
    assert await module.WorkspaceMetrics().get("workspace") == {"state": "unavailable"}


@pytest.mark.asyncio
async def test_metrics_route_checks_workspace_in_current_instance(workspace_app, monkeypatch):
    client, *_ = workspace_app
    workspace_id, _ = await linked_workspace(workspace_app)
    get = AsyncMock(return_value={"state": "stopped"})
    monkeypatch.setattr(module.workspace_metrics, "get", get)
    response = await client.get(f"workspaces/{uuid4()}/metrics")
    assert response.status_code == 404
    get.assert_not_awaited()
    response = await client.get(f"workspaces/{workspace_id}/metrics")
    assert response.status_code == 200
    get.assert_awaited_once_with(str(workspace_id))
