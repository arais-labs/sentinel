from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.config import settings
from app.services.runtime import get_runtime


class RuntimeForwardError(RuntimeError):
    pass


class RuntimeForwardUnavailableError(RuntimeForwardError):
    pass


class RuntimeForwardConflictError(RuntimeForwardError):
    pass


@dataclass(slots=True)
class RuntimeForwardRecord:
    forward_id: str
    runtime_session_id: str
    created_session_id: str | None
    target_host: str
    target_port: int
    relay_port: int
    protocol: str
    label: str | None
    status: str
    relay_pid: int | None
    created_at: str


@dataclass(slots=True)
class RuntimeForwardPayload:
    forward_id: str
    runtime_session_id: str
    created_session_id: str | None
    target_host: str
    target_port: int
    relay_port: int
    protocol: str
    label: str | None
    status: str
    url: str
    host: str
    host_port: int
    created_at: datetime
    closed_at: datetime | None


def _state_file_path(workspace_path: str) -> str:
    return f"{workspace_path.rstrip('/')}/.runtime/port_forwards.json"


def _public_host(runtime_session_id: UUID | str) -> str:
    provider = get_runtime()
    try:
        host = provider.get_public_host(runtime_session_id)
    except Exception:
        host = None
    if host:
        return str(host)
    fallback = (settings.runtime_forward_public_host or "").strip()
    return fallback or "localhost"


def _public_url(*, runtime_session_id: UUID | str, host_port: int, protocol: str) -> str:
    host = _public_host(runtime_session_id)
    normalized_protocol = (protocol or "http").strip().lower()
    if normalized_protocol == "http":
        return f"http://{host}:{int(host_port)}/"
    return f"tcp://{host}:{int(host_port)}"


async def ensure_runtime_forward(
    *,
    runtime_session_id: UUID,
    created_session_id: UUID | None,
    target_host: str,
    target_port: int,
    protocol: str,
    label: str | None,
) -> RuntimeForwardRecord:
    runtime = await _ensure_runtime_instance(runtime_session_id)
    records = await _load_live_records(runtime_session_id, runtime)
    normalized_host = _normalize_target_host(target_host)
    normalized_protocol = (protocol or "http").strip().lower()

    for record in records:
        if (
            record.target_host == normalized_host
            and int(record.target_port) == int(target_port)
            and record.protocol == normalized_protocol
            and record.status == "open"
        ):
            if label and not record.label:
                record.label = label
                await _write_records(runtime, records)
            return record

    relay_port = _reserve_relay_port(records)
    host_port = await _require_public_host_port(runtime_session_id=runtime_session_id, relay_port=relay_port)
    _ = host_port
    stdout_path = f"/tmp/runtime-forward-{relay_port}.stdout.log"
    stderr_path = f"/tmp/runtime-forward-{relay_port}.stderr.log"
    pid = await runtime.client.run_detached(
        f"socat TCP-LISTEN:{int(relay_port)},fork,reuseaddr,bind=0.0.0.0 TCP:{normalized_host}:{int(target_port)}",
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    await _wait_for_relay_listener(runtime_session_id=runtime_session_id, relay_port=relay_port, relay_pid=pid)
    record = RuntimeForwardRecord(
        forward_id=str(uuid4()),
        runtime_session_id=str(runtime_session_id),
        created_session_id=str(created_session_id) if created_session_id is not None else None,
        target_host=normalized_host,
        target_port=int(target_port),
        relay_port=int(relay_port),
        protocol=normalized_protocol,
        label=label,
        status="open",
        relay_pid=int(pid),
        created_at=datetime.now(UTC).isoformat(),
    )
    records.append(record)
    await _write_records(runtime, records)
    return record


async def list_runtime_forwards(
    *,
    runtime_session_id: UUID,
) -> list[RuntimeForwardRecord]:
    runtime = await _get_existing_runtime_instance(runtime_session_id)
    if runtime is None:
        return []
    return await _load_live_records(runtime_session_id, runtime)


async def close_runtime_forward(
    *,
    runtime_session_id: UUID,
    forward_id: str,
) -> RuntimeForwardRecord | None:
    runtime = await _get_existing_runtime_instance(runtime_session_id)
    if runtime is None:
        return None
    records = await _load_live_records(runtime_session_id, runtime)
    target = next((record for record in records if record.forward_id == forward_id), None)
    if target is None:
        return None
    await _stop_runtime_relay(runtime, relay_pid=target.relay_pid, relay_port=target.relay_port)
    records = [record for record in records if record.forward_id != forward_id]
    await _write_records(runtime, records)
    return target


def serialize_forward(record: RuntimeForwardRecord) -> RuntimeForwardPayload:
    runtime_session_id = UUID(record.runtime_session_id)
    host_port = _resolve_public_host_port(runtime_session_id=runtime_session_id, relay_port=record.relay_port)
    host = _public_host(runtime_session_id)
    created_at = _parse_iso_datetime(record.created_at)
    return RuntimeForwardPayload(
        forward_id=record.forward_id,
        runtime_session_id=record.runtime_session_id,
        created_session_id=record.created_session_id,
        target_host=record.target_host,
        target_port=record.target_port,
        relay_port=record.relay_port,
        protocol=record.protocol,
        label=record.label,
        status=record.status,
        url=_public_url(runtime_session_id=runtime_session_id, host_port=host_port, protocol=record.protocol),
        host=host,
        host_port=host_port,
        created_at=created_at,
        closed_at=None,
    )


def _parse_iso_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)


def _normalize_target_host(target_host: str) -> str:
    normalized = (target_host or "").strip()
    if not normalized or normalized.lower() in {"localhost", "::1"}:
        return "127.0.0.1"
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:")
    if normalized.startswith("-") or any(ch not in allowed for ch in normalized):
        raise RuntimeForwardUnavailableError("Invalid target host")
    return normalized


def _reserve_relay_port(records: list[RuntimeForwardRecord]) -> int:
    used = {int(record.relay_port) for record in records if record.status == "open"}
    for port in range(int(settings.runtime_forward_port_start), int(settings.runtime_forward_port_end) + 1):
        if port not in used:
            return int(port)
    raise RuntimeForwardConflictError("No runtime relay ports available")


async def _ensure_runtime_instance(runtime_session_id: UUID):
    provider = get_runtime()
    try:
        return await provider.ensure(runtime_session_id)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeForwardUnavailableError("Runtime is not available for this session") from exc


async def _get_existing_runtime_instance(runtime_session_id: UUID):
    provider = get_runtime()
    runtime = provider.get(runtime_session_id) if hasattr(provider, "get") else None
    if runtime is not None:
        return runtime
    if provider.get_host(runtime_session_id):
        try:
            return await provider.ensure(runtime_session_id)
        except Exception:
            return None
    return None


def _resolve_public_host_port(*, runtime_session_id: UUID, relay_port: int) -> int:
    provider = get_runtime()
    try:
        mapped = provider.resolve_port(runtime_session_id, relay_port)
    except Exception:
        mapped = None
    if mapped:
        return int(mapped)
    return int(relay_port)


async def _require_public_host_port(*, runtime_session_id: UUID, relay_port: int) -> int:
    provider = get_runtime()
    for _ in range(10):
        mapped = provider.resolve_port(runtime_session_id, relay_port)
        if mapped:
            return int(mapped)
        await asyncio.sleep(0.1)
    raise RuntimeForwardUnavailableError("Runtime relay port is not published to the host")


async def _wait_for_relay_listener(
    *,
    runtime_session_id: UUID,
    relay_port: int,
    relay_pid: int,
) -> None:
    runtime = await _ensure_runtime_instance(runtime_session_id)
    for _ in range(20):
        if await _relay_is_alive(runtime, relay_port=relay_port, relay_pid=relay_pid):
            return
        await asyncio.sleep(0.2)
    await _stop_runtime_relay(runtime, relay_pid=relay_pid, relay_port=relay_port)
    raise RuntimeForwardUnavailableError("Failed to start runtime relay")


async def _load_live_records(runtime_session_id: UUID, runtime) -> list[RuntimeForwardRecord]:
    records = await _read_records(runtime)
    live_records: list[RuntimeForwardRecord] = []
    changed = False
    for record in records:
        if await _relay_is_alive(runtime, relay_port=record.relay_port, relay_pid=record.relay_pid):
            live_records.append(record)
        else:
            changed = True
    if changed:
        await _write_records(runtime, live_records)
    return live_records


async def _read_records(runtime) -> list[RuntimeForwardRecord]:
    path = _state_file_path(runtime.workspace_path)
    script = f"""
import json
from pathlib import Path

path = Path({path!r})
if not path.exists():
    print("[]")
else:
    try:
        data = json.loads(path.read_text())
    except Exception:
        data = []
    if not isinstance(data, list):
        data = []
    print(json.dumps(data))
"""
    result = await runtime.client.run(f"python3 - <<'PY'\n{script}\nPY", timeout=10)
    if result.exit_status not in (0, None):
        raise RuntimeForwardUnavailableError("Failed to read runtime forward state")
    try:
        payload = json.loads(result.stdout.strip() or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeForwardUnavailableError("Runtime forward state is invalid") from exc
    records: list[RuntimeForwardRecord] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            records.append(
                RuntimeForwardRecord(
                    forward_id=str(item["forward_id"]),
                    runtime_session_id=str(item["runtime_session_id"]),
                    created_session_id=str(item["created_session_id"]) if item.get("created_session_id") else None,
                    target_host=str(item["target_host"]),
                    target_port=int(item["target_port"]),
                    relay_port=int(item["relay_port"]),
                    protocol=str(item["protocol"]),
                    label=str(item["label"]) if item.get("label") else None,
                    status=str(item.get("status") or "open"),
                    relay_pid=int(item["relay_pid"]) if item.get("relay_pid") is not None else None,
                    created_at=str(item.get("created_at") or datetime.now(UTC).isoformat()),
                )
            )
        except Exception:
            continue
    return records


async def _write_records(runtime, records: list[RuntimeForwardRecord]) -> None:
    path = _state_file_path(runtime.workspace_path)
    encoded = base64.b64encode(json.dumps([asdict(record) for record in records]).encode("utf-8")).decode("ascii")
    script = f"""
import base64
from pathlib import Path

path = Path({path!r})
path.parent.mkdir(parents=True, exist_ok=True)
path.write_bytes(base64.b64decode({encoded!r}))
"""
    result = await runtime.client.run(f"python3 - <<'PY'\n{script}\nPY", timeout=10)
    if result.exit_status not in (0, None):
        raise RuntimeForwardUnavailableError("Failed to write runtime forward state")


async def _relay_is_alive(runtime, *, relay_port: int, relay_pid: int | None) -> bool:
    checks = [
        "import os, socket",
        f"pid = {int(relay_pid) if relay_pid else 0}",
        "if pid:",
        "    try:\n        os.kill(pid, 0)\n    except OSError:\n        raise SystemExit(1)",
        "sock = socket.socket()",
        "sock.settimeout(0.2)",
        "try:",
        f"    sock.connect(('127.0.0.1', {int(relay_port)}))",
        "except OSError:",
        "    raise SystemExit(1)",
        "finally:",
        "    sock.close()",
    ]
    result = await runtime.client.run("python3 - <<'PY'\n" + "\n".join(checks) + "\nPY", timeout=5)
    return result.exit_status == 0


async def _stop_runtime_relay(runtime, *, relay_pid: int | None, relay_port: int) -> None:
    commands: list[str] = []
    if relay_pid:
        commands.append(f"kill {int(relay_pid)} || true")
    commands.append(f"rm -f /tmp/runtime-forward-{int(relay_port)}.stdout.log")
    commands.append(f"rm -f /tmp/runtime-forward-{int(relay_port)}.stderr.log")
    await runtime.client.run("; ".join(commands), timeout=10)
