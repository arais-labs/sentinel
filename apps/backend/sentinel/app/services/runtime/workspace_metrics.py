"""Demand-driven guest metrics, shared by viewers of the same workspace."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass

from app.services.runtime import workspace_containers as containers

# Only the primary interface is counted: Docker bridges would count traffic twice.
PROBE = r"""printf 'BOOT\n'; cat /proc/sys/kernel/random/boot_id
printf 'UPTIME\n'; cat /proc/uptime
printf 'CPU\n'; head -n 1 /proc/stat
printf 'MEMORY\n'; cat /proc/meminfo
printf 'ROUTES\n'; cat /proc/net/route
printf 'NETWORK\n'; cat /proc/net/dev
printf 'DISK\n'; df -Pk /
"""


def parse_sample(output: str) -> dict:
    sections: dict[str, list[str]] = {}
    section = ""
    for line in output.splitlines():
        if line in {"BOOT", "UPTIME", "CPU", "MEMORY", "ROUTES", "NETWORK", "DISK"}:
            section = line
            sections[section] = []
        elif section:
            sections[section].append(line)
    cpu = [int(value) for value in sections["CPU"][0].split()[1:9]]
    memory = {line.split(":")[0]: int(line.split()[1]) * 1024 for line in sections["MEMORY"]}
    interface = next(
        (
            parts[0]
            for line in sections["ROUTES"][1:]
            if len(parts := line.split()) >= 4 and parts[1] == "00000000" and int(parts[3], 16) & 1
        ),
        None,
    )
    received = sent = None
    for line in sections["NETWORK"]:
        if ":" in line and line.split(":", 1)[0].strip() == interface:
            values = line.split(":", 1)[1].split()
            received, sent = int(values[0]), int(values[8])
    disk = sections["DISK"][-1].split()
    return {
        "boot": sections["BOOT"][0],
        "uptime": float(sections["UPTIME"][0].split()[0]),
        "cpu_total": sum(cpu),
        "cpu_idle": cpu[3] + cpu[4],
        "memory_total_bytes": memory["MemTotal"],
        "memory_used_bytes": max(0, memory["MemTotal"] - memory["MemAvailable"]),
        "disk_total_bytes": int(disk[1]) * 1024,
        "disk_used_bytes": int(disk[2]) * 1024,
        "network_interface": interface,
        "network_received_bytes": received,
        "network_sent_bytes": sent,
    }


def metrics(sample: dict, previous: dict | None) -> dict:
    result = {
        key: value for key, value in sample.items() if key not in {"boot", "cpu_total", "cpu_idle"}
    }
    result.update(
        cpu_percent=None, network_receive_bytes_per_second=None, network_send_bytes_per_second=None
    )
    if previous and previous["boot"] == sample["boot"]:
        elapsed = sample["uptime"] - previous["uptime"]
        total = sample["cpu_total"] - previous["cpu_total"]
        idle = sample["cpu_idle"] - previous["cpu_idle"]
        if total > 0 and 0 <= idle <= total:
            result["cpu_percent"] = round(100 * (total - idle) / total, 1)
        if elapsed > 0 and sample["network_interface"] == previous["network_interface"]:
            for counter, rate in [
                ("network_received_bytes", "network_receive_bytes_per_second"),
                ("network_sent_bytes", "network_send_bytes_per_second"),
            ]:
                before, after = previous[counter], sample[counter]
                if before is not None and after is not None and after >= before:
                    result[rate] = round((after - before) / elapsed)
    return result


@dataclass
class Entry:
    updated: float
    result: dict
    sample: dict | None


class WorkspaceMetrics:
    def __init__(self):
        self.cache: OrderedDict[str, Entry] = OrderedDict()
        self.pending: dict[str, asyncio.Task] = {}

    async def get(self, workspace: str) -> dict:
        previous = self.cache.get(workspace)
        if previous and time.monotonic() - previous.updated < 2:
            return previous.result
        task = self.pending.get(workspace)
        if task is None:
            task = asyncio.create_task(self._collect(workspace, previous))
            self.pending[workspace] = task
            task.add_done_callback(lambda done: self.pending.pop(workspace, None))
        return await asyncio.shield(task)

    async def _collect(self, workspace: str, previous: Entry | None) -> dict:
        sample = None
        try:
            async with asyncio.timeout(5):
                state = (await containers.statuses(workspace)).get(workspace, {})
                result = {
                    "state": state.get("state", "stopped"),
                    "resources": state.get("resources"),
                }
                if result["state"] in {"running", "preparing"}:
                    response = await containers.request(
                        "exec", workspace=workspace, arguments=["sh", "-ec", PROBE], timeout=3
                    )
                    if response.get("exitCode") != 0:
                        raise ValueError("Guest metrics probe failed")
                    sample = parse_sample(response["stdout"])
                    baseline = (
                        previous.sample
                        if previous and time.monotonic() - previous.updated < 10
                        else None
                    )
                    result.update(metrics(sample, baseline))
        except (TimeoutError, containers.WorkspaceContainerError, ValueError, KeyError, IndexError):
            result = {"state": "unavailable"}
        self.cache[workspace] = Entry(time.monotonic(), result, sample)
        self.cache.move_to_end(workspace)
        while len(self.cache) > 128:
            self.cache.popitem(last=False)
        return result


workspace_metrics = WorkspaceMetrics()
