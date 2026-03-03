from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Session

logger = logging.getLogger(__name__)

_RUNTIME_BASE_DIR = Path(
    os.environ.get("SESSION_RUNTIME_BASE_DIR", "/tmp/sentinel/session_runtime")
).expanduser()
_LEGACY_PYTHON_XAGENT_BASE_DIR = Path(
    os.environ.get("PYTHON_XAGENT_BASE_DIR", "/tmp/sentinel/python_xagent")
).expanduser()
_RUNTIME_META_FILENAME = ".runtime_meta.json"
_RUNTIME_ACTIONS_FILENAME = ".runtime_actions.jsonl"
_RUNTIME_JOBS_FILENAME = ".runtime_jobs.json"
_RUNTIME_LOGS_DIRNAME = "logs"
_DEFAULT_RUNTIME_ACTION_LIMIT = 40
_runtime_meta_lock = asyncio.Lock()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_seconds(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, value)


def runtime_root_dir(session_id: UUID | str) -> Path:
    return _RUNTIME_BASE_DIR / str(session_id)


def runtime_workspace_dir(session_id: UUID | str) -> Path:
    return runtime_root_dir(session_id) / "workspace"


def runtime_venv_dir(session_id: UUID | str) -> Path:
    return runtime_root_dir(session_id) / "venv"


def runtime_logs_dir(session_id: UUID | str) -> Path:
    return runtime_root_dir(session_id) / _RUNTIME_LOGS_DIRNAME


async def ensure_runtime_layout(session_id: UUID | str) -> Path:
    root = runtime_root_dir(session_id)
    workspace = root / "workspace"
    created = not root.exists()
    root.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    await mark_runtime_state(session_id, active=False, command=None, pid=None)
    if created:
        async with _runtime_meta_lock:
            _append_runtime_action(
                root / _RUNTIME_ACTIONS_FILENAME,
                {
                    "timestamp": _utc_now().isoformat(),
                    "action": "runtime_initialized",
                    "details": {"workspace": str(workspace)},
                },
            )
    return root


async def mark_runtime_state(
    session_id: UUID | str,
    *,
    active: bool,
    command: str | None,
    pid: int | None,
) -> None:
    now = _utc_now().isoformat()
    root = runtime_root_dir(session_id)
    root.mkdir(parents=True, exist_ok=True)
    meta_path = root / _RUNTIME_META_FILENAME
    actions_path = root / _RUNTIME_ACTIONS_FILENAME
    async with _runtime_meta_lock:
        metadata = _read_runtime_metadata(meta_path)
        previous_active = bool(metadata.get("active"))
        if not metadata.get("created_at"):
            metadata["created_at"] = now
        metadata["session_id"] = str(session_id)
        metadata["active"] = bool(active)
        metadata["active_pid"] = int(pid) if isinstance(pid, int) and pid > 0 else None
        metadata["last_used_at"] = now
        if active:
            metadata["last_active_at"] = now
        if command is not None:
            metadata["last_command"] = command
        _write_runtime_metadata(meta_path, metadata)
        if active and not previous_active and command:
            _append_runtime_action(
                actions_path,
                {
                    "timestamp": now,
                    "action": "command_started",
                    "details": {
                        "command": command,
                        "pid": int(pid) if isinstance(pid, int) and pid > 0 else None,
                    },
                },
            )
        elif not active and previous_active:
            _append_runtime_action(
                actions_path,
                {
                    "timestamp": now,
                    "action": "command_finished",
                    "details": {
                        "command": command if isinstance(command, str) and command else metadata.get("last_command"),
                        "pid": int(pid) if isinstance(pid, int) and pid > 0 else None,
                    },
                },
            )


def get_session_runtime_snapshot(
    session_id: UUID | str,
    *,
    action_limit: int = _DEFAULT_RUNTIME_ACTION_LIMIT,
) -> dict[str, Any]:
    session_key = str(session_id)
    root = runtime_root_dir(session_key)
    workspace = runtime_workspace_dir(session_key)
    venv = runtime_venv_dir(session_key)
    metadata = _read_runtime_metadata(root / _RUNTIME_META_FILENAME)
    return {
        "session_id": session_key,
        "runtime_exists": root.exists(),
        "workspace_exists": workspace.exists(),
        "venv_exists": venv.exists(),
        "active": bool(metadata.get("active")),
        "active_pid": _int_or_none(metadata.get("active_pid")),
        "last_command": _string_or_none(metadata.get("last_command")),
        "created_at": _iso_or_none(metadata.get("created_at")),
        "last_used_at": _iso_or_none(metadata.get("last_used_at")),
        "last_active_at": _iso_or_none(metadata.get("last_active_at")),
        "actions": _read_runtime_actions(
            root / _RUNTIME_ACTIONS_FILENAME,
            limit=_normalize_action_limit(action_limit),
        ),
    }


async def register_detached_runtime_job(
    session_id: UUID | str,
    *,
    command: str,
    cwd: Path,
    pid: int,
    stdout_path: Path,
    stderr_path: Path,
) -> dict[str, Any]:
    now = _utc_now().isoformat()
    root = runtime_root_dir(session_id)
    root.mkdir(parents=True, exist_ok=True)
    jobs_path = root / _RUNTIME_JOBS_FILENAME
    actions_path = root / _RUNTIME_ACTIONS_FILENAME
    async with _runtime_meta_lock:
        jobs = _read_runtime_jobs(jobs_path)
        jobs = _refresh_runtime_jobs(jobs)
        job = {
            "id": uuid4().hex,
            "status": "running",
            "command": command,
            "cwd": str(cwd),
            "pid": int(pid),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "created_at": now,
            "updated_at": now,
            "started_at": now,
            "ended_at": None,
            "returncode": None,
            "termination_signal": None,
            "termination_reason": None,
        }
        jobs.append(job)
        _write_runtime_jobs(jobs_path, jobs)
        _append_runtime_action(
            actions_path,
            {
                "timestamp": now,
                "action": "detached_job_started",
                "details": {
                    "job_id": job["id"],
                    "pid": job["pid"],
                    "command": command,
                },
            },
        )
    return job


async def finalize_detached_runtime_job(
    session_id: UUID | str,
    *,
    job_id: str,
    returncode: int | None,
    error: str | None = None,
) -> dict[str, Any] | None:
    root = runtime_root_dir(session_id)
    jobs_path = root / _RUNTIME_JOBS_FILENAME
    actions_path = root / _RUNTIME_ACTIONS_FILENAME
    now = _utc_now().isoformat()
    async with _runtime_meta_lock:
        jobs = _read_runtime_jobs(jobs_path)
        updated: dict[str, Any] | None = None
        for item in jobs:
            if str(item.get("id", "")).strip() != job_id:
                continue
            item["status"] = "completed" if returncode == 0 else "failed"
            item["returncode"] = int(returncode) if isinstance(returncode, int) else None
            item["ended_at"] = now
            item["updated_at"] = now
            if error:
                item["termination_reason"] = error
            updated = item
            break
        if updated is None:
            return None
        _write_runtime_jobs(jobs_path, jobs)
        _append_runtime_action(
            actions_path,
            {
                "timestamp": now,
                "action": "detached_job_finished",
                "details": {
                    "job_id": updated["id"],
                    "pid": updated.get("pid"),
                    "status": updated["status"],
                    "returncode": updated.get("returncode"),
                },
            },
        )
        return updated


async def list_detached_runtime_jobs(
    session_id: UUID | str,
    *,
    include_completed: bool = True,
) -> list[dict[str, Any]]:
    root = runtime_root_dir(session_id)
    jobs_path = root / _RUNTIME_JOBS_FILENAME
    async with _runtime_meta_lock:
        jobs = _read_runtime_jobs(jobs_path)
        jobs = _refresh_runtime_jobs(jobs)
        _write_runtime_jobs(jobs_path, jobs)
    jobs.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    if include_completed:
        return jobs
    return [item for item in jobs if str(item.get("status")) == "running"]


async def get_detached_runtime_job(
    session_id: UUID | str,
    *,
    job_id: str,
) -> dict[str, Any] | None:
    jobs = await list_detached_runtime_jobs(session_id, include_completed=True)
    for item in jobs:
        if str(item.get("id", "")).strip() == job_id:
            return item
    return None


async def stop_detached_runtime_job(
    session_id: UUID | str,
    *,
    job_id: str,
    force: bool = False,
    reason: str | None = None,
) -> dict[str, Any] | None:
    root = runtime_root_dir(session_id)
    jobs_path = root / _RUNTIME_JOBS_FILENAME
    actions_path = root / _RUNTIME_ACTIONS_FILENAME
    now = _utc_now().isoformat()
    async with _runtime_meta_lock:
        jobs = _read_runtime_jobs(jobs_path)
        jobs = _refresh_runtime_jobs(jobs)
        target: dict[str, Any] | None = None
        for item in jobs:
            if str(item.get("id", "")).strip() == job_id:
                target = item
                break
        if target is None:
            return None

        status = str(target.get("status") or "")
        if status != "running":
            _write_runtime_jobs(jobs_path, jobs)
            return target

        pid = _int_or_none(target.get("pid"))
        if pid is None:
            target["status"] = "cancelled"
            target["updated_at"] = now
            target["ended_at"] = now
            target["termination_reason"] = reason or "stopped"
            _write_runtime_jobs(jobs_path, jobs)
            return target

        signal_name = "SIGKILL" if force else "SIGTERM"
        await _terminate_pid(pid, force=force)
        if not force and _process_exists(pid):
            await _terminate_pid(pid, force=True)
            signal_name = "SIGKILL"

        target["status"] = "cancelled"
        target["updated_at"] = now
        target["ended_at"] = now
        target["termination_signal"] = signal_name
        target["termination_reason"] = reason or "stopped"
        _write_runtime_jobs(jobs_path, jobs)
        _append_runtime_action(
            actions_path,
            {
                "timestamp": now,
                "action": "detached_job_stopped",
                "details": {
                    "job_id": target.get("id"),
                    "pid": pid,
                    "signal": signal_name,
                    "reason": target.get("termination_reason"),
                },
            },
        )
        return target


async def stop_all_detached_runtime_jobs(
    session_id: UUID | str,
    *,
    reason: str | None = None,
) -> int:
    root = runtime_root_dir(session_id)
    jobs_path = root / _RUNTIME_JOBS_FILENAME
    if not root.exists() and not jobs_path.exists():
        return 0
    actions_path = root / _RUNTIME_ACTIONS_FILENAME
    now = _utc_now().isoformat()
    stopped = 0
    async with _runtime_meta_lock:
        jobs = _read_runtime_jobs(jobs_path)
        jobs = _refresh_runtime_jobs(jobs)
        for item in jobs:
            if str(item.get("status") or "") != "running":
                continue
            pid = _int_or_none(item.get("pid"))
            if pid is not None:
                await _terminate_pid(pid, force=False)
                if _process_exists(pid):
                    await _terminate_pid(pid, force=True)
            item["status"] = "cancelled"
            item["updated_at"] = now
            item["ended_at"] = now
            item["termination_signal"] = "SIGKILL"
            item["termination_reason"] = reason or "stopped"
            stopped += 1
            _append_runtime_action(
                actions_path,
                {
                    "timestamp": now,
                    "action": "detached_job_stopped",
                    "details": {
                        "job_id": item.get("id"),
                        "pid": pid,
                        "signal": "SIGKILL",
                        "reason": item.get("termination_reason"),
                    },
                },
            )
        if stopped > 0:
            _write_runtime_jobs(jobs_path, jobs)
    return stopped


async def read_detached_runtime_job_logs(
    session_id: UUID | str,
    *,
    job_id: str,
    tail_bytes: int = 8_000,
) -> dict[str, Any] | None:
    job = await get_detached_runtime_job(session_id, job_id=job_id)
    if job is None:
        return None
    max_bytes = max(256, min(int(tail_bytes), 200_000))
    stdout_path = Path(str(job.get("stdout_path") or ""))
    stderr_path = Path(str(job.get("stderr_path") or ""))
    stdout = _tail_text(stdout_path, max_bytes=max_bytes)
    stderr = _tail_text(stderr_path, max_bytes=max_bytes)
    return {
        "job": job,
        "stdout_tail": stdout,
        "stderr_tail": stderr,
        "tail_bytes": max_bytes,
    }


async def cleanup_session_runtime(
    session_id: UUID | str,
    *,
    remove_legacy_python_xagent: bool = True,
) -> dict[str, bool]:
    session_key = str(session_id)
    runtime_removed = await _remove_runtime_root(runtime_root_dir(session_key))
    legacy_removed = False
    if remove_legacy_python_xagent:
        legacy_removed = await _remove_tree(_LEGACY_PYTHON_XAGENT_BASE_DIR / session_key)
    return {"runtime_removed": runtime_removed, "legacy_removed": legacy_removed}


async def sweep_session_runtimes(
    db_factory: async_sessionmaker[AsyncSession],
) -> dict[str, int]:
    """
    Remove stale/orphan session runtimes.

    Policy:
    - orphan session dir => delete immediately
    - active metadata older than stale-active TTL => delete
    - inactive metadata older than idle TTL => delete
    - legacy python_xagent dirs are cleaned by TTL/orphan based on directory mtime
    """
    idle_ttl_seconds = _parse_seconds("SESSION_RUNTIME_IDLE_TTL_SECONDS", 2700)
    stale_active_ttl_seconds = _parse_seconds(
        "SESSION_RUNTIME_STALE_ACTIVE_TTL_SECONDS", 10800
    )
    now = _utc_now()

    async with db_factory() as db:
        result = await db.execute(select(Session))
        sessions = result.scalars().all()
    existing_sessions = {str(item.id): item for item in sessions}

    removed = 0
    removed_orphans = 0
    removed_idle = 0
    removed_stale_active = 0

    for root in _list_runtime_dirs(_RUNTIME_BASE_DIR):
        session_key = root.name
        session = existing_sessions.get(session_key)
        if session is None:
            if await _remove_runtime_root(root):
                removed += 1
                removed_orphans += 1
            continue

        meta = _read_runtime_metadata(root / _RUNTIME_META_FILENAME)
        active = bool(meta.get("active"))
        last_used = _parse_dt(meta.get("last_used_at")) or _mtime_dt(root)
        last_active = _parse_dt(meta.get("last_active_at")) or last_used
        if active:
            age = (now - last_active).total_seconds()
            if age > stale_active_ttl_seconds and await _remove_runtime_root(root):
                removed += 1
                removed_stale_active += 1
            continue

        idle_age = (now - last_used).total_seconds()
        if idle_age > idle_ttl_seconds and await _remove_runtime_root(root):
            removed += 1
            removed_idle += 1

    for root in _list_runtime_dirs(_LEGACY_PYTHON_XAGENT_BASE_DIR):
        session_key = root.name
        session = existing_sessions.get(session_key)
        if session is None:
            if await _remove_tree(root):
                removed += 1
                removed_orphans += 1
            continue
        idle_age = (now - _mtime_dt(root)).total_seconds()
        if idle_age > idle_ttl_seconds and await _remove_tree(root):
            removed += 1
            removed_idle += 1

    return {
        "removed": removed,
        "removed_orphans": removed_orphans,
        "removed_idle": removed_idle,
        "removed_stale_active": removed_stale_active,
    }


async def run_session_runtime_janitor(
    *,
    stop_event: asyncio.Event,
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    interval_seconds = _parse_seconds("SESSION_RUNTIME_SWEEP_INTERVAL_SECONDS", 60)
    while not stop_event.is_set():
        try:
            stats = await sweep_session_runtimes(db_factory)
            if stats["removed"] > 0:
                logger.info("Session runtime janitor removed %s runtime(s): %s", stats["removed"], stats)
        except Exception:  # noqa: BLE001
            logger.warning("Session runtime janitor sweep failed", exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=float(interval_seconds))
        except TimeoutError:
            continue


def _list_runtime_dirs(base_dir: Path) -> list[Path]:
    if not base_dir.exists():
        return []
    roots: list[Path] = []
    for item in base_dir.iterdir():
        if not item.is_dir():
            continue
        try:
            UUID(item.name)
        except ValueError:
            continue
        roots.append(item)
    return roots


def _read_runtime_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _read_runtime_jobs(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    jobs: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        job_id = _string_or_none(item.get("id"))
        status = _string_or_none(item.get("status"))
        if not job_id or not status:
            continue
        jobs.append(dict(item))
    return jobs


def _write_runtime_jobs(path: Path, jobs: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jobs, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")


def _refresh_runtime_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = _utc_now().isoformat()
    for item in jobs:
        if str(item.get("status") or "") != "running":
            continue
        pid = _int_or_none(item.get("pid"))
        if pid is None or _process_exists(pid):
            continue
        item["status"] = "completed"
        item["ended_at"] = item.get("ended_at") or now
        item["updated_at"] = now
        item["termination_reason"] = item.get("termination_reason") or "process_exited"
    return jobs


def _write_runtime_metadata(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")


def _append_runtime_action(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
        handle.write("\n")


def _normalize_action_limit(limit: int) -> int:
    if isinstance(limit, bool):
        return _DEFAULT_RUNTIME_ACTION_LIMIT
    if not isinstance(limit, int):
        return _DEFAULT_RUNTIME_ACTION_LIMIT
    return max(1, min(limit, 200))


def _read_runtime_actions(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries: list[dict[str, Any]] = []
    for raw in lines[-limit:]:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        action = _string_or_none(payload.get("action"))
        if not action:
            continue
        details = payload.get("details")
        entries.append(
            {
                "timestamp": _iso_or_none(payload.get("timestamp")),
                "action": action,
                "details": details if isinstance(details, dict) else {},
            }
        )
    return entries


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso_or_none(value: Any) -> str | None:
    parsed = _parse_dt(value)
    return parsed.isoformat() if parsed else None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _string_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _mtime_dt(path: Path) -> datetime:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return _utc_now()
    return datetime.fromtimestamp(mtime, tz=UTC)


async def _remove_tree(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        await asyncio.to_thread(shutil.rmtree, path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("Failed to remove runtime path: %s", path, exc_info=True)
        return False


async def _remove_runtime_root(path: Path) -> bool:
    await _terminate_runtime_process(path)
    await _terminate_detached_runtime_jobs(path)
    return await _remove_tree(path)


async def _terminate_runtime_process(path: Path) -> None:
    meta = _read_runtime_metadata(path / _RUNTIME_META_FILENAME)
    pid_value = meta.get("active_pid")
    if isinstance(pid_value, bool):
        return
    try:
        pid = int(pid_value)
    except (TypeError, ValueError):
        return
    if pid <= 0:
        return

    try:
        if os.name == "nt":
            proc = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=5)
            return

        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)
            return
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to terminate runtime process pid=%s for %s", pid, path, exc_info=True)


async def _terminate_detached_runtime_jobs(path: Path) -> None:
    jobs_path = path / _RUNTIME_JOBS_FILENAME
    async with _runtime_meta_lock:
        jobs = _read_runtime_jobs(jobs_path)
        if not jobs:
            return
        now = _utc_now().isoformat()
        changed = False
        for item in jobs:
            if str(item.get("status") or "") != "running":
                continue
            pid = _int_or_none(item.get("pid"))
            if pid is not None:
                await _terminate_pid(pid, force=True)
            item["status"] = "cancelled"
            item["ended_at"] = now
            item["updated_at"] = now
            item["termination_signal"] = "SIGKILL"
            item["termination_reason"] = "runtime_cleanup"
            changed = True
        if changed:
            _write_runtime_jobs(jobs_path, jobs)


async def _terminate_pid(pid: int, *, force: bool) -> None:
    if pid <= 0:
        return
    try:
        if os.name == "nt":
            args = ["taskkill", "/PID", str(pid), "/T"]
            if force:
                args.append("/F")
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=5)
            return

        sig = signal.SIGKILL if force else signal.SIGTERM
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, sig)
            return
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, sig)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to terminate pid=%s force=%s", pid, force, exc_info=True)


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _tail_text(path: Path, *, max_bytes: int) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        size = path.stat().st_size
        read_size = min(max_bytes, max(0, int(size)))
        with path.open("rb") as handle:
            if read_size > 0:
                handle.seek(-read_size, os.SEEK_END)
            data = handle.read(read_size if read_size > 0 else max_bytes)
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")
