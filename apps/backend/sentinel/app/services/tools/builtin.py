from __future__ import annotations

import asyncio
import contextlib
import io
import ipaddress
import json
import os
import shlex
import socket
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Memory, Session, SubAgentTask
from app.services.embeddings import EmbeddingService
from app.services.memory_search import MemorySearchService
from app.services.tools.browser_tool import BrowserManager
from app.services.tools.executor import ToolValidationError
from app.services.tools.registry import ToolDefinition, ToolRegistry
from app.services.tools.trigger_tools import (
    trigger_create_tool,
    trigger_delete_tool,
    trigger_list_tool,
    trigger_update_tool,
)

_MAX_HTTP_RESPONSE_BYTES = 1_048_576
_ALLOWED_MEMORY_CATEGORIES = {"core", "preference", "project", "correction"}
_PYTHON_XAGENT_BASE_DIR = Path(
    os.environ.get("PYTHON_XAGENT_BASE_DIR", "/tmp/sentinel/python_xagent")
).expanduser()
_MAX_PYTHON_XAGENT_OUTPUT_CHARS = 20_000
_python_xagent_runtime_lock = asyncio.Lock()


def build_default_registry(
    *,
    memory_search_service: MemorySearchService | None = None,
    embedding_service: EmbeddingService | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    browser_manager: BrowserManager | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    manager = browser_manager or BrowserManager()
    registry.register(_file_read_tool())
    registry.register(_http_request_tool())
    registry.register(_shell_exec_tool())
    registry.register(_browser_navigate_tool(manager))
    registry.register(_browser_screenshot_tool(manager))
    registry.register(_browser_click_tool(manager))
    registry.register(_browser_type_tool(manager))
    registry.register(_browser_press_key_tool(manager))
    registry.register(_browser_get_text_tool(manager))
    registry.register(_browser_snapshot_tool(manager))
    registry.register(_browser_reset_tool(manager))

    if session_factory is not None:
        registry.register(_memory_store_tool(session_factory=session_factory, embedding_service=embedding_service))
        registry.register(_memory_roots_tool(session_factory=session_factory))
        registry.register(_memory_get_node_tool(session_factory=session_factory))
        registry.register(_memory_list_children_tool(session_factory=session_factory))
        registry.register(_memory_update_tool(session_factory=session_factory, embedding_service=embedding_service))
        registry.register(_memory_touch_tool(session_factory=session_factory))
        registry.register(trigger_create_tool(session_factory=session_factory))
        registry.register(trigger_list_tool(session_factory=session_factory))
        registry.register(trigger_update_tool(session_factory=session_factory))
        registry.register(trigger_delete_tool(session_factory=session_factory))
    if session_factory is not None and memory_search_service is not None:
        registry.register(_memory_search_tool(session_factory=session_factory, memory_search_service=memory_search_service))

    return registry


def _file_read_tool() -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        path_raw = payload.get("path")
        if not isinstance(path_raw, str) or not path_raw.strip():
            raise ToolValidationError("Field 'path' must be a non-empty string")
        max_bytes = payload.get("max_bytes", 4096)
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
            raise ToolValidationError("Field 'max_bytes' must be a positive integer")

        allowed_base = Path(os.environ.get("TOOL_FILE_READ_BASE_DIR", "/tmp/sentinel")).expanduser().resolve()
        path = Path(path_raw).expanduser().resolve()
        if path != allowed_base and allowed_base not in path.parents:
            raise ToolValidationError(f"Path outside allowed directory: {allowed_base}")
        if not path.exists() or not path.is_file():
            raise ToolValidationError("File not found")

        data = path.read_bytes()
        chunk = data[:max_bytes]
        return {
            "path": str(path.resolve()),
            "content": chunk.decode("utf-8", errors="replace"),
            "bytes_read": len(chunk),
            "truncated": len(data) > max_bytes,
        }

    return ToolDefinition(
        name="file_read",
        description="Read text content from a local file path with byte limit.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["path"],
            "properties": {
                "path": {"type": "string"},
                "max_bytes": {"type": "integer"},
            },
        },
        execute=_execute,
    )


def _http_request_tool() -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        url = payload.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ToolValidationError("Field 'url' must be a non-empty string")
        parsed_url = urlparse(url.strip())
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            raise ToolValidationError("Field 'url' must be a valid http/https URL")
        await _validate_public_hostname(parsed_url.hostname)
        method = payload.get("method", "GET")
        if not isinstance(method, str):
            raise ToolValidationError("Field 'method' must be a string")
        method = method.upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ToolValidationError("Unsupported HTTP method")

        timeout_seconds = payload.get("timeout_seconds", 10)
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ToolValidationError("Field 'timeout_seconds' must be a positive integer")

        headers = payload.get("headers", {})
        if headers is None:
            headers = {}
        if not isinstance(headers, dict):
            raise ToolValidationError("Field 'headers' must be an object")

        request_kwargs: dict[str, Any] = {"headers": {str(k): str(v) for k, v in headers.items()}}
        if "body" in payload:
            body = payload["body"]
            if isinstance(body, (dict, list)):
                request_kwargs["json"] = body
            else:
                request_kwargs["content"] = str(body)

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.request(method, url, **request_kwargs)

        content_type = response.headers.get("content-type", "")
        response_bytes = response.content
        truncated = len(response_bytes) > _MAX_HTTP_RESPONSE_BYTES
        visible_bytes = response_bytes[:_MAX_HTTP_RESPONSE_BYTES]

        if "application/json" in content_type and not truncated:
            try:
                parsed_body: Any = response.json()
            except ValueError:
                parsed_body = response.text
        else:
            parsed_body = visible_bytes.decode("utf-8", errors="replace")
            if truncated:
                parsed_body += "\n... [truncated - response exceeded 1 MB]"

        return {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": parsed_body,
            "truncated": truncated,
        }

    return ToolDefinition(
        name="http_request",
        description="Make outbound HTTP requests to external endpoints.",
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["url"],
            "properties": {
                "url": {"type": "string"},
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                "headers": {"type": "object"},
                "body": {"type": "object"},
                "timeout_seconds": {"type": "integer"},
            },
        },
        execute=_execute,
    )


def _shell_exec_tool() -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        command = payload.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ToolValidationError("Field 'command' must be a non-empty string")
        timeout_seconds = payload.get("timeout_seconds", 30)
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ToolValidationError("Field 'timeout_seconds' must be a positive integer")

        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise ToolValidationError("Command execution timed out")

        return {
            "returncode": process.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
        }

    return ToolDefinition(
        name="shell_exec",
        description="Execute shell command inside backend runtime container.",
        risk_level="high",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["command"],
            "properties": {
                "command": {"type": "string"},
                "timeout_seconds": {"type": "integer"},
            },
        },
        execute=_execute,
    )


def python_xagent_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    orchestrator: Any,
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        session_id_raw = payload.get("session_id")
        if not isinstance(session_id_raw, str) or not session_id_raw.strip():
            raise ToolValidationError("Field 'session_id' must be a non-empty string")
        try:
            session_id = UUID(session_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'session_id' must be a valid UUID string") from exc

        code = payload.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ToolValidationError("Field 'code' must be a non-empty string")

        timeout_seconds = payload.get("timeout_seconds", 60)
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds < 1:
            raise ToolValidationError("Field 'timeout_seconds' must be a positive integer")
        timeout_seconds = min(timeout_seconds, 600)

        requirements = payload.get("requirements", [])
        if requirements is None:
            requirements = []
        if not isinstance(requirements, list):
            raise ToolValidationError("Field 'requirements' must be an array of strings")
        normalized_requirements = [
            item.strip() for item in requirements if isinstance(item, str) and item.strip()
        ]
        if len(normalized_requirements) > 20:
            raise ToolValidationError("At most 20 requirement entries are allowed")

        sub_agent_timeout = payload.get("sub_agent_timeout_seconds", 300)
        if (
            not isinstance(sub_agent_timeout, int)
            or isinstance(sub_agent_timeout, bool)
            or sub_agent_timeout < 1
        ):
            raise ToolValidationError("Field 'sub_agent_timeout_seconds' must be a positive integer")
        sub_agent_timeout = min(sub_agent_timeout, 3600)

        await _ensure_session_exists(session_factory, session_id)

        session_root = _PYTHON_XAGENT_BASE_DIR / str(session_id)
        workspace_dir = session_root / "workspace"
        venv_dir = session_root / "venv"
        python_bin = _venv_python_path(venv_dir)
        pip_bin = _venv_pip_path(venv_dir)

        async with _python_xagent_runtime_lock:
            await _ensure_python_xagent_venv(venv_dir, python_bin)
            if normalized_requirements:
                await _install_python_xagent_requirements(
                    pip_bin=pip_bin,
                    requirements=normalized_requirements,
                    timeout_seconds=min(timeout_seconds, 240),
                )

            loop = asyncio.get_running_loop()
            sub_agent_calls: list[dict[str, Any]] = []

            def call_sub_agent(
                objective: str,
                context: Any | None = None,
                *,
                max_steps: int = 10,
                timeout_seconds: int | None = None,
                allowed_tools: list[str] | None = None,
            ) -> dict[str, Any]:
                if not isinstance(objective, str) or not objective.strip():
                    raise ValueError("objective must be a non-empty string")
                if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps < 1:
                    raise ValueError("max_steps must be a positive integer")
                max_steps = min(max_steps, 50)

                effective_timeout = timeout_seconds if timeout_seconds is not None else sub_agent_timeout
                if (
                    not isinstance(effective_timeout, int)
                    or isinstance(effective_timeout, bool)
                    or effective_timeout < 1
                ):
                    raise ValueError("timeout_seconds must be a positive integer")
                effective_timeout = min(effective_timeout, 3600)
                normalized_tools = [str(t) for t in (allowed_tools or []) if isinstance(t, str) and t]

                future = asyncio.run_coroutine_threadsafe(
                    _run_python_xagent_sub_agent(
                        session_factory=session_factory,
                        orchestrator=orchestrator,
                        session_id=session_id,
                        objective=objective.strip(),
                        context=_stringify_sub_agent_context(context),
                        max_steps=max_steps,
                        timeout_seconds=effective_timeout,
                        allowed_tools=normalized_tools,
                    ),
                    loop,
                )
                result = future.result(timeout=max(30, effective_timeout + 15))
                sub_agent_calls.append(
                    {
                        "task_id": result.get("task_id"),
                        "objective": objective.strip(),
                        "status": result.get("status"),
                    }
                )
                return result

            try:
                execution = await asyncio.wait_for(
                    asyncio.to_thread(
                        _run_python_xagent_code_sync,
                        code=code,
                        workspace_dir=workspace_dir,
                        venv_dir=venv_dir,
                        call_sub_agent=call_sub_agent,
                    ),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                return {
                    "ok": False,
                    "error": f"pythonXagent timed out after {timeout_seconds}s",
                    "stdout": "",
                    "stderr": "",
                    "session_id": str(session_id),
                    "workspace": str(workspace_dir),
                    "venv": str(venv_dir),
                    "requirements_installed": normalized_requirements,
                    "sub_agent_calls": sub_agent_calls,
                }

            return {
                "ok": execution["ok"],
                "stdout": execution["stdout"],
                "stderr": execution["stderr"],
                "exception": execution["exception"],
                "result": execution["result"],
                "result_repr": execution["result_repr"],
                "session_id": str(session_id),
                "workspace": str(workspace_dir),
                "venv": str(venv_dir),
                "requirements_installed": normalized_requirements,
                "sub_agent_calls": sub_agent_calls,
            }

    return ToolDefinition(
        name="pythonXagent",
        description=(
            "Run Python code in a per-session virtualenv workspace. "
            "Code can call call_sub_agent(objective, context, ...) to delegate sub-tasks."
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["code"],
            "properties": {
                "session_id": {"type": "string", "description": "Current session ID (auto-injected in agent loop)"},
                "code": {"type": "string", "description": "Python code to execute. Optional `result` var is returned."},
                "requirements": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional pip requirements to install in this session venv",
                },
                "timeout_seconds": {"type": "integer", "description": "Execution timeout (default 60, max 600)"},
                "sub_agent_timeout_seconds": {
                    "type": "integer",
                    "description": "Default timeout for call_sub_agent helper calls (default 300, max 3600)",
                },
            },
        },
        execute=_execute,
    )


def _memory_search_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    memory_search_service: MemorySearchService,
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        query = payload.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolValidationError("Field 'query' must be a non-empty string")

        category = payload.get("category")
        if category is not None:
            if not isinstance(category, str) or category not in _ALLOWED_MEMORY_CATEGORIES:
                raise ToolValidationError("Field 'category' must be one of: core, preference, project, correction")

        limit = payload.get("limit", 10)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ToolValidationError("Field 'limit' must be a positive integer")
        limit = min(limit, 200)

        root_id_raw = payload.get("root_id")
        root_id: UUID | None = None
        if root_id_raw is not None:
            if not isinstance(root_id_raw, str) or not root_id_raw.strip():
                raise ToolValidationError("Field 'root_id' must be a UUID string")
            try:
                root_id = UUID(root_id_raw.strip())
            except ValueError as exc:
                raise ToolValidationError("Field 'root_id' must be a valid UUID string") from exc

        auto_expand = payload.get("auto_expand", True)
        if not isinstance(auto_expand, bool):
            raise ToolValidationError("Field 'auto_expand' must be a boolean")

        async with session_factory() as db:
            results = await memory_search_service.search(db, query, category=category, limit=limit)
            memories = await _all_memories(db)

        items = [item.memory for item in results]
        if root_id is not None:
            items = _filter_by_root(items, memories, root_id)

        expanded: list[Memory] = []
        if auto_expand:
            expanded = _expand_memory_branches(items, memories)
            if root_id is not None:
                expanded = _filter_by_root(expanded, memories, root_id)

        item_ids = {item.id for item in items}
        return {
            "items": [
                {
                    "id": str(item.memory.id),
                    "content": item.memory.content,
                    "title": item.memory.title,
                    "summary": item.memory.summary,
                    "category": item.memory.category,
                    "parent_id": str(item.memory.parent_id) if item.memory.parent_id else None,
                    "importance": int(item.memory.importance or 0),
                    "pinned": bool(item.memory.pinned),
                    "score": item.score,
                }
                for item in results
                if item.memory.id in item_ids
            ],
            "expanded_items": [
                {
                    "id": str(item.id),
                    "content": item.content,
                    "title": item.title,
                    "summary": item.summary,
                    "category": item.category,
                    "parent_id": str(item.parent_id) if item.parent_id else None,
                    "importance": int(item.importance or 0),
                    "pinned": bool(item.pinned),
                }
                for item in expanded
            ],
            "total": len(items),
        }

    return ToolDefinition(
        name="memory_search",
        description="Search stored memories using hybrid semantic and keyword ranking.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "category": {"type": "string", "enum": ["core", "preference", "project", "correction"]},
                "limit": {"type": "integer"},
                "root_id": {"type": "string"},
                "auto_expand": {"type": "boolean"},
            },
        },
        execute=_execute,
    )


def _memory_store_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    embedding_service: EmbeddingService | None,
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ToolValidationError("Field 'content' must be a non-empty string")

        category = payload.get("category", "project")
        if not isinstance(category, str) or category not in _ALLOWED_MEMORY_CATEGORIES:
            raise ToolValidationError("Field 'category' must be one of: core, preference, project, correction")

        title = payload.get("title")
        if title is not None and not isinstance(title, str):
            raise ToolValidationError("Field 'title' must be a string")
        title = title.strip() if isinstance(title, str) else None
        if title == "":
            title = None

        summary = payload.get("summary")
        if summary is not None and not isinstance(summary, str):
            raise ToolValidationError("Field 'summary' must be a string")
        summary = summary.strip() if isinstance(summary, str) else None
        if summary == "":
            summary = None

        parent_id_raw = payload.get("parent_id")
        parent_id: UUID | None = None
        if parent_id_raw is not None:
            if not isinstance(parent_id_raw, str) or not parent_id_raw.strip():
                raise ToolValidationError("Field 'parent_id' must be a UUID string")
            try:
                parent_id = UUID(parent_id_raw.strip())
            except ValueError as exc:
                raise ToolValidationError("Field 'parent_id' must be a valid UUID string") from exc

        importance = payload.get("importance", 0)
        if not isinstance(importance, int) or isinstance(importance, bool) or importance < 0 or importance > 100:
            raise ToolValidationError("Field 'importance' must be an integer between 0 and 100")

        pinned = payload.get("pinned", False)
        if not isinstance(pinned, bool):
            raise ToolValidationError("Field 'pinned' must be a boolean")

        metadata = payload.get("metadata", {})
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ToolValidationError("Field 'metadata' must be an object")

        embedding = payload.get("embedding")
        if embedding is not None:
            if not isinstance(embedding, list) or not all(isinstance(x, (int, float)) for x in embedding):
                raise ToolValidationError("Field 'embedding' must be a list of numbers")
            embedding = [float(x) for x in embedding]

        if embedding is None and embedding_service is not None:
            embedding = await embedding_service.embed(content.strip())

        async with session_factory() as db:
            if parent_id is not None:
                parent = await _get_memory(db, parent_id)
                if parent is None:
                    raise ToolValidationError("Parent memory node not found")
            memory = Memory(
                content=content.strip(),
                title=title,
                summary=summary,
                category=category,
                parent_id=parent_id,
                importance=importance,
                pinned=pinned,
                metadata_json=metadata,
                embedding=embedding,
            )
            db.add(memory)
            await db.commit()
            await db.refresh(memory)

        return {
            "id": str(memory.id),
            "content": memory.content,
            "title": memory.title,
            "summary": memory.summary,
            "category": memory.category,
            "parent_id": str(memory.parent_id) if memory.parent_id else None,
            "importance": int(memory.importance or 0),
            "pinned": bool(memory.pinned),
            "embedded": memory.embedding is not None,
        }

    return ToolDefinition(
        name="memory_store",
        description="Store a new memory item and auto-generate embedding when configured.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["content"],
            "properties": {
                "content": {"type": "string"},
                "category": {"type": "string", "enum": ["core", "preference", "project", "correction"]},
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "parent_id": {"type": "string"},
                "importance": {"type": "integer"},
                "pinned": {"type": "boolean"},
                "metadata": {"type": "object"},
                "embedding": {"type": "array", "items": {"type": "number"}},
            },
        },
        execute=_execute,
    )


def _memory_roots_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        if payload:
            raise ToolValidationError("memory_roots does not accept input fields")
        async with session_factory() as db:
            memories = await _all_memories(db)
        roots = [item for item in memories if item.parent_id is None]
        roots.sort(
            key=lambda item: (
                bool(item.pinned),
                int(item.importance or 0),
                item.last_accessed_at or item.updated_at or item.created_at or datetime.min.replace(tzinfo=UTC),
            ),
            reverse=True,
        )
        return {
            "items": [
                {
                    "id": str(item.id),
                    "title": item.title,
                    "summary": item.summary,
                    "content": item.content,
                    "category": item.category,
                    "importance": int(item.importance or 0),
                    "pinned": bool(item.pinned),
                }
                for item in roots
            ],
            "total": len(roots),
        }

    return ToolDefinition(
        name="memory_roots",
        description="List all root memory nodes (no limit).",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        execute=_execute,
    )


def _memory_get_node_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        node_id_raw = payload.get("id")
        if not isinstance(node_id_raw, str) or not node_id_raw.strip():
            raise ToolValidationError("Field 'id' must be a non-empty UUID string")
        try:
            node_id = UUID(node_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'id' must be a valid UUID string") from exc
        async with session_factory() as db:
            node = await _get_memory(db, node_id)
            if node is None:
                raise ToolValidationError("Memory node not found")
            node.last_accessed_at = datetime.now(UTC)
            await db.commit()
            await db.refresh(node)
        return {
            "id": str(node.id),
            "title": node.title,
            "summary": node.summary,
            "content": node.content,
            "category": node.category,
            "parent_id": str(node.parent_id) if node.parent_id else None,
            "importance": int(node.importance or 0),
            "pinned": bool(node.pinned),
            "metadata": node.metadata_json or {},
        }

    return ToolDefinition(
        name="memory_get_node",
        description="Get a memory node by ID and mark it as recently accessed.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["id"],
            "properties": {"id": {"type": "string"}},
        },
        execute=_execute,
    )


def _memory_list_children_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        parent_id_raw = payload.get("parent_id")
        if not isinstance(parent_id_raw, str) or not parent_id_raw.strip():
            raise ToolValidationError("Field 'parent_id' must be a non-empty UUID string")
        try:
            parent_id = UUID(parent_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'parent_id' must be a valid UUID string") from exc
        async with session_factory() as db:
            parent = await _get_memory(db, parent_id)
            if parent is None:
                raise ToolValidationError("Parent memory node not found")
            memories = await _all_memories(db)
        children = [item for item in memories if item.parent_id == parent_id]
        children.sort(key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        return {
            "parent_id": str(parent_id),
            "items": [
                {
                    "id": str(item.id),
                    "title": item.title,
                    "summary": item.summary,
                    "content": item.content,
                    "category": item.category,
                    "importance": int(item.importance or 0),
                    "pinned": bool(item.pinned),
                }
                for item in children
            ],
            "total": len(children),
        }

    return ToolDefinition(
        name="memory_list_children",
        description="List direct child memory nodes for a parent node.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["parent_id"],
            "properties": {"parent_id": {"type": "string"}},
        },
        execute=_execute,
    )


def _memory_update_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    embedding_service: EmbeddingService | None,
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        node_id_raw = payload.get("id")
        if not isinstance(node_id_raw, str) or not node_id_raw.strip():
            raise ToolValidationError("Field 'id' must be a non-empty UUID string")
        try:
            node_id = UUID(node_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'id' must be a valid UUID string") from exc

        allowed_updates = {"content", "title", "summary", "category", "parent_id", "importance", "pinned", "metadata"}
        unknown = [key for key in payload if key not in allowed_updates and key != "id"]
        if unknown:
            raise ToolValidationError(f"Unknown update fields: {', '.join(sorted(unknown))}")

        async with session_factory() as db:
            node = await _get_memory(db, node_id)
            if node is None:
                raise ToolValidationError("Memory node not found")
            memories = await _all_memories(db)

            if "content" in payload:
                content = payload.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise ToolValidationError("Field 'content' must be a non-empty string")
                node.content = content.strip()
                if embedding_service is not None:
                    node.embedding = await embedding_service.embed(node.content)

            if "title" in payload:
                title = payload.get("title")
                if title is not None and not isinstance(title, str):
                    raise ToolValidationError("Field 'title' must be a string or null")
                node.title = title.strip() if isinstance(title, str) and title.strip() else None

            if "summary" in payload:
                summary = payload.get("summary")
                if summary is not None and not isinstance(summary, str):
                    raise ToolValidationError("Field 'summary' must be a string or null")
                node.summary = summary.strip() if isinstance(summary, str) and summary.strip() else None

            if "category" in payload:
                category = payload.get("category")
                if not isinstance(category, str) or category not in _ALLOWED_MEMORY_CATEGORIES:
                    raise ToolValidationError("Field 'category' must be one of: core, preference, project, correction")
                node.category = category

            if "importance" in payload:
                importance = payload.get("importance")
                if not isinstance(importance, int) or isinstance(importance, bool) or importance < 0 or importance > 100:
                    raise ToolValidationError("Field 'importance' must be an integer between 0 and 100")
                node.importance = importance

            if "pinned" in payload:
                pinned = payload.get("pinned")
                if not isinstance(pinned, bool):
                    raise ToolValidationError("Field 'pinned' must be a boolean")
                node.pinned = pinned

            if "metadata" in payload:
                metadata = payload.get("metadata")
                if metadata is None:
                    metadata = {}
                if not isinstance(metadata, dict):
                    raise ToolValidationError("Field 'metadata' must be an object")
                node.metadata_json = metadata

            if "parent_id" in payload:
                parent_id_raw = payload.get("parent_id")
                if parent_id_raw is None:
                    node.parent_id = None
                else:
                    if not isinstance(parent_id_raw, str) or not parent_id_raw.strip():
                        raise ToolValidationError("Field 'parent_id' must be a UUID string or null")
                    try:
                        parent_id = UUID(parent_id_raw.strip())
                    except ValueError as exc:
                        raise ToolValidationError("Field 'parent_id' must be a valid UUID string") from exc
                    if parent_id == node.id:
                        raise ToolValidationError("A node cannot be its own parent")
                    parent = _memory_by_id(memories, parent_id)
                    if parent is None:
                        raise ToolValidationError("Parent memory node not found")
                    if _is_descendant(target_parent_id=parent_id, node_id=node.id, memories=memories):
                        raise ToolValidationError("Cannot move node under its own descendant")
                    node.parent_id = parent_id

            await db.commit()
            await db.refresh(node)

        return {
            "id": str(node.id),
            "title": node.title,
            "summary": node.summary,
            "content": node.content,
            "category": node.category,
            "parent_id": str(node.parent_id) if node.parent_id else None,
            "importance": int(node.importance or 0),
            "pinned": bool(node.pinned),
        }

    return ToolDefinition(
        name="memory_update",
        description="Update an existing memory node (hierarchical fields included).",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["id"],
            "properties": {
                "id": {"type": "string"},
                "content": {"type": "string"},
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "category": {"type": "string", "enum": ["core", "preference", "project", "correction"]},
                "parent_id": {"type": "string"},
                "importance": {"type": "integer"},
                "pinned": {"type": "boolean"},
                "metadata": {"type": "object"},
            },
        },
        execute=_execute,
    )


def _memory_touch_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        node_id_raw = payload.get("id")
        if not isinstance(node_id_raw, str) or not node_id_raw.strip():
            raise ToolValidationError("Field 'id' must be a non-empty UUID string")
        try:
            node_id = UUID(node_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'id' must be a valid UUID string") from exc
        async with session_factory() as db:
            node = await _get_memory(db, node_id)
            if node is None:
                raise ToolValidationError("Memory node not found")
            node.last_accessed_at = datetime.now(UTC)
            await db.commit()
            await db.refresh(node)
        return {"id": str(node.id), "last_accessed_at": node.last_accessed_at.isoformat()}

    return ToolDefinition(
        name="memory_touch",
        description="Mark a memory node as recently accessed.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["id"],
            "properties": {"id": {"type": "string"}},
        },
        execute=_execute,
    )


def spawn_sub_agent_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    orchestrator: Any,
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        from uuid import UUID as _UUID

        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ToolValidationError("Field 'session_id' must be a non-empty string")
        objective = payload.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            raise ToolValidationError("Field 'objective' must be a non-empty string")

        scope = payload.get("scope")
        if scope is not None and not isinstance(scope, str):
            raise ToolValidationError("Field 'scope' must be a string")

        allowed_tools = payload.get("allowed_tools", [])
        if not isinstance(allowed_tools, list):
            raise ToolValidationError("Field 'allowed_tools' must be an array")

        max_steps = payload.get("max_steps", 10)
        if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps < 1:
            raise ToolValidationError("Field 'max_steps' must be a positive integer")
        max_steps = min(max_steps, 50)

        timeout_seconds = payload.get("timeout_seconds", 300)
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds < 1:
            raise ToolValidationError("Field 'timeout_seconds' must be a positive integer")
        timeout_seconds = min(timeout_seconds, 3600)

        sid = _UUID(session_id.strip())

        async with session_factory() as db:
            from sqlalchemy import select as _select

            # Enforce max 3 concurrent tasks per session
            result = await db.execute(_select(SubAgentTask).where(SubAgentTask.session_id == sid))
            tasks = result.scalars().all()
            active = [t for t in tasks if t.status in {"pending", "running"}]
            if len(active) >= 3:
                raise ToolValidationError("Max 3 concurrent sub-agent tasks per session")

            task = SubAgentTask(
                session_id=sid,
                objective=objective.strip(),
                context=scope.strip() if isinstance(scope, str) and scope.strip() else None,
                constraints=[],
                allowed_tools=[str(t) for t in allowed_tools if isinstance(t, str)],
                max_turns=max_steps,
                timeout_seconds=timeout_seconds,
                status="pending",
            )
            db.add(task)
            await db.commit()
            await db.refresh(task)
            task_id = task.id

        orchestrator.start_task(task_id)
        return {
            "task_id": str(task_id),
            "status": "pending",
            "objective": objective.strip(),
            "timeout_seconds": timeout_seconds,
            "note": (
                f"Sub-agent spawned (timeout: {timeout_seconds}s). "
                "If this is a quick task (≤30s), use pythonXagent to sleep the appropriate amount then check results. "
                "For longer tasks, end your turn now and ask the user if they want to wait — "
                "the system will automatically resume you with all results when every sub-agent completes."
            ),
        }

    return ToolDefinition(
        name="spawn_sub_agent",
        description=(
            "Spawn a sub-agent for a bounded one-off task. "
            "After spawning, decide based on expected duration: "
            "for quick tasks (≤30s) use pythonXagent to sleep the appropriate time then check results; "
            "for longer tasks, end your turn and inform the user — "
            "the system will automatically resume your turn with all results once every sub-agent completes."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "objective"],
            "properties": {
                "session_id": {"type": "string", "description": "Current session ID"},
                "objective": {
                    "type": "string",
                    "description": "Concrete one-off outcome the sub-agent should produce",
                },
                "scope": {"type": "string", "description": "Extra context or constraints for the sub-agent"},
                "allowed_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Minimal tool names the sub-agent may use",
                },
                "max_steps": {"type": "integer", "description": "Maximum iterations (default 10, max 50). Typical range: 15-30 for research tasks — use more steps for tasks that require many browser calls or deep investigation."},
                "timeout_seconds": {"type": "integer", "description": "Timeout in seconds (default 300)"},
            },
        },
        execute=_execute,
    )


def check_sub_agent_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        from uuid import UUID as _UUID

        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ToolValidationError("Field 'task_id' must be a non-empty string")

        tid = _UUID(task_id.strip())
        async with session_factory() as db:
            from sqlalchemy import select as _select

            result = await db.execute(_select(SubAgentTask).where(SubAgentTask.id == tid))
            task = result.scalars().first()
            if task is None:
                raise ToolValidationError("Sub-agent task not found")

            return {
                "task_id": str(task.id),
                "objective": task.objective,
                "status": task.status,
                "turns_used": task.turns_used or 0,
                "tokens_used": task.tokens_used or 0,
                "result": task.result if isinstance(task.result, dict) else None,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            }

    return ToolDefinition(
        name="check_sub_agent",
        description="Check the status and result of a sub-agent task.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["task_id"],
            "properties": {
                "task_id": {"type": "string", "description": "The sub-agent task ID to check"},
            },
        },
        execute=_execute,
    )


def list_sub_agents_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        from uuid import UUID as _UUID

        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ToolValidationError("Field 'session_id' must be a non-empty string")

        sid = _UUID(session_id.strip())
        async with session_factory() as db:
            from sqlalchemy import select as _select

            result = await db.execute(_select(SubAgentTask).where(SubAgentTask.session_id == sid))
            tasks = result.scalars().all()
            tasks.sort(key=lambda t: t.created_at, reverse=True)

            return {
                "tasks": [
                    {
                        "task_id": str(t.id),
                        "objective": t.objective,
                        "status": t.status,
                        "turns_used": t.turns_used or 0,
                        "tokens_used": t.tokens_used or 0,
                    }
                    for t in tasks
                ],
                "total": len(tasks),
            }

    return ToolDefinition(
        name="list_sub_agents",
        description="List all sub-agent tasks for the current session.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string", "description": "Current session ID"},
            },
        },
        execute=_execute,
    )


async def _all_memories(db: AsyncSession) -> list[Memory]:
    result = await db.execute(select(Memory))
    return result.scalars().all()


async def _get_memory(db: AsyncSession, memory_id: UUID) -> Memory | None:
    result = await db.execute(select(Memory).where(Memory.id == memory_id))
    return result.scalars().first()


def _memory_by_id(memories: list[Memory], memory_id: UUID) -> Memory | None:
    for item in memories:
        if item.id == memory_id:
            return item
    return None


def _children_map(memories: list[Memory]) -> dict[UUID | None, list[Memory]]:
    mapping: dict[UUID | None, list[Memory]] = {}
    for memory in memories:
        mapping.setdefault(memory.parent_id, []).append(memory)
    return mapping


def _descendant_ids(mapping: dict[UUID | None, list[Memory]], root_id: UUID) -> set[UUID]:
    result: set[UUID] = set()
    stack = [root_id]
    while stack:
        current = stack.pop()
        for child in mapping.get(current, []):
            if child.id in result:
                continue
            result.add(child.id)
            stack.append(child.id)
    return result


def _is_descendant(*, target_parent_id: UUID, node_id: UUID, memories: list[Memory]) -> bool:
    mapping = _children_map(memories)
    return node_id in _descendant_ids(mapping, target_parent_id)


def _filter_by_root(items: list[Memory], memories: list[Memory], root_id: UUID) -> list[Memory]:
    mapping = _children_map(memories)
    allowed = _descendant_ids(mapping, root_id)
    allowed.add(root_id)
    return [item for item in items if item.id in allowed]


def _expand_memory_branches(items: list[Memory], memories: list[Memory]) -> list[Memory]:
    by_id = {item.id: item for item in memories}
    children = _children_map(memories)
    expanded: list[Memory] = []
    seen: set[UUID] = set()
    for item in items:
        if item.id not in seen:
            seen.add(item.id)
            expanded.append(item)

        # Include lineage to root.
        current = item
        lineage: list[Memory] = []
        guard: set[UUID] = set()
        while current.parent_id and current.parent_id in by_id and current.parent_id not in guard:
            guard.add(current.parent_id)
            current = by_id[current.parent_id]
            lineage.append(current)
        for node in reversed(lineage):
            if node.id not in seen:
                seen.add(node.id)
                expanded.append(node)

        # Include direct children for quick drill-down.
        for child in children.get(item.id, []):
            if child.id in seen:
                continue
            seen.add(child.id)
            expanded.append(child)
    return expanded


async def _ensure_session_exists(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: UUID,
) -> None:
    async with session_factory() as db:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalars().first()
        if session is None:
            raise ToolValidationError("Session not found")


async def _run_python_xagent_sub_agent(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    orchestrator: Any,
    session_id: UUID,
    objective: str,
    context: str | None,
    max_steps: int,
    timeout_seconds: int,
    allowed_tools: list[str],
) -> dict[str, Any]:
    async with session_factory() as db:
        result = await db.execute(select(SubAgentTask).where(SubAgentTask.session_id == session_id))
        tasks = result.scalars().all()
        active = [item for item in tasks if item.status in {"pending", "running"}]
        if len(active) >= 3:
            raise ToolValidationError("Max 3 concurrent sub-agent tasks per session")

        task = SubAgentTask(
            session_id=session_id,
            objective=objective,
            context=context,
            constraints=[],
            allowed_tools=allowed_tools,
            max_turns=max_steps,
            timeout_seconds=timeout_seconds,
            status="pending",
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id

    started = orchestrator.start_task(task_id)
    if not started:
        async with session_factory() as db:
            result = await db.execute(select(SubAgentTask).where(SubAgentTask.id == task_id))
            existing = result.scalars().first()
            if existing is None:
                raise ToolValidationError("Failed to load sub-agent task")
            existing = await orchestrator.complete_task(db, existing)
            return _python_xagent_sub_agent_result(existing)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        async with session_factory() as db:
            result = await db.execute(select(SubAgentTask).where(SubAgentTask.id == task_id))
            current = result.scalars().first()
            if current is None:
                raise ToolValidationError("Sub-agent task disappeared")
            if current.status in {"completed", "failed", "cancelled"}:
                return _python_xagent_sub_agent_result(current)
        await asyncio.sleep(0.5)

    orchestrator.cancel_task(task_id)
    async with session_factory() as db:
        result = await db.execute(select(SubAgentTask).where(SubAgentTask.id == task_id))
        timed_out = result.scalars().first()
        if timed_out is not None:
            timed_out.status = "failed"
            timed_out.completed_at = datetime.now(UTC)
            timed_out.result = {
                "error": f"Sub-agent timed out after {timeout_seconds}s",
            }
            await db.commit()
            return _python_xagent_sub_agent_result(timed_out)

    return {
        "task_id": str(task_id),
        "status": "failed",
        "error": f"Sub-agent timed out after {timeout_seconds}s",
    }


def _python_xagent_sub_agent_result(task: SubAgentTask) -> dict[str, Any]:
    raw_result = task.result if isinstance(task.result, dict) else {}
    final_text = raw_result.get("final_text")
    if not isinstance(final_text, str):
        final_text = raw_result.get("summary") if isinstance(raw_result.get("summary"), str) else None
    return {
        "task_id": str(task.id),
        "status": task.status,
        "objective": task.objective,
        "final_text": final_text,
        "result": raw_result,
        "turns_used": int(task.turns_used or 0),
        "tokens_used": int(task.tokens_used or 0),
    }


def _run_python_xagent_code_sync(
    *,
    code: str,
    workspace_dir: Path,
    venv_dir: Path,
    call_sub_agent: Any,
) -> dict[str, Any]:
    workspace_dir.mkdir(parents=True, exist_ok=True)

    globals_map: dict[str, Any] = {
        "__name__": "__main__",
        "call_sub_agent": call_sub_agent,
    }

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    exception_text: str | None = None

    old_cwd = Path.cwd()
    old_path = os.environ.get("PATH", "")
    old_virtual_env = os.environ.get("VIRTUAL_ENV")
    old_sys_path = list(sys.path)

    venv_bin = _venv_bin_dir(venv_dir)
    venv_site_packages = _venv_site_packages_dir(venv_dir)

    try:
        os.chdir(workspace_dir)
        os.environ["PATH"] = f"{venv_bin}{os.pathsep}{old_path}" if old_path else str(venv_bin)
        os.environ["VIRTUAL_ENV"] = str(venv_dir)
        if venv_site_packages.exists():
            sys.path.insert(0, str(venv_site_packages))

        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            try:
                compiled = compile(code, "<pythonXagent>", "exec")
                exec(compiled, globals_map, globals_map)
            except Exception:  # noqa: BLE001
                exception_text = traceback.format_exc()
    finally:
        os.chdir(old_cwd)
        os.environ["PATH"] = old_path
        if old_virtual_env is None:
            os.environ.pop("VIRTUAL_ENV", None)
        else:
            os.environ["VIRTUAL_ENV"] = old_virtual_env
        sys.path[:] = old_sys_path

    result_value = globals_map.get("result", globals_map.get("_result"))
    result_json, result_repr = _to_json_or_repr(result_value)
    return {
        "ok": exception_text is None,
        "stdout": _truncate_python_xagent_text(stdout_buf.getvalue()),
        "stderr": _truncate_python_xagent_text(stderr_buf.getvalue()),
        "exception": _truncate_python_xagent_text(exception_text) if exception_text else None,
        "result": result_json,
        "result_repr": result_repr,
    }


async def _ensure_python_xagent_venv(venv_dir: Path, python_bin: Path) -> None:
    if python_bin.exists():
        return
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "venv",
        str(venv_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise ToolValidationError("Timed out while creating pythonXagent virtualenv") from exc
    if proc.returncode != 0:
        message = stderr.decode("utf-8", errors="replace") or stdout.decode("utf-8", errors="replace")
        raise ToolValidationError(f"Failed to create virtualenv: {_truncate_python_xagent_text(message)}")
    if not python_bin.exists():
        raise ToolValidationError("Virtualenv creation finished but python executable was not found")


async def _install_python_xagent_requirements(
    *,
    pip_bin: Path,
    requirements: list[str],
    timeout_seconds: int,
) -> None:
    if not pip_bin.exists():
        raise ToolValidationError("pip executable not found in virtualenv")
    proc = await asyncio.create_subprocess_exec(
        str(pip_bin),
        "install",
        "--disable-pip-version-check",
        "--no-input",
        *requirements,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise ToolValidationError("Timed out while installing pythonXagent requirements") from exc
    if proc.returncode != 0:
        message = stderr.decode("utf-8", errors="replace") or stdout.decode("utf-8", errors="replace")
        raise ToolValidationError(f"pip install failed: {_truncate_python_xagent_text(message)}")


def _venv_bin_dir(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts" if os.name == "nt" else "bin")


def _venv_python_path(venv_dir: Path) -> Path:
    bin_dir = _venv_bin_dir(venv_dir)
    return bin_dir / ("python.exe" if os.name == "nt" else "python")


def _venv_pip_path(venv_dir: Path) -> Path:
    bin_dir = _venv_bin_dir(venv_dir)
    return bin_dir / ("pip.exe" if os.name == "nt" else "pip")


def _venv_site_packages_dir(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Lib" / "site-packages"
    return venv_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"


def _to_json_or_repr(value: Any) -> tuple[Any, str | None]:
    if value is None:
        return None, None
    try:
        json.dumps(value)
        return value, None
    except TypeError:
        return None, repr(value)


def _truncate_python_xagent_text(value: str | None) -> str:
    text = value or ""
    if len(text) <= _MAX_PYTHON_XAGENT_OUTPUT_CHARS:
        return text
    return f"{text[:_MAX_PYTHON_XAGENT_OUTPUT_CHARS]}\n...[truncated]"


def _stringify_sub_agent_context(value: Any | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized if normalized else None
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return repr(value)


async def _validate_public_hostname(hostname: str) -> None:
    if os.environ.get("SSRF_ALLOW_PRIVATE", "").lower() in ("1", "true", "yes"):
        return
    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ToolValidationError(f"Cannot resolve hostname: {hostname}") from exc

    blocked: list[str] = []
    for item in addr_info:
        ip_text = item[4][0]
        ip_addr = ipaddress.ip_address(ip_text)
        if (
            ip_addr.is_private
            or ip_addr.is_loopback
            or ip_addr.is_link_local
            or ip_addr.is_reserved
            or ip_addr.is_multicast
            or ip_addr.is_unspecified
        ):
            blocked.append(ip_text)

    if blocked:
        raise ToolValidationError(
            f"SSRF blocked: {hostname} resolves to private/internal address {', '.join(sorted(set(blocked)))}"
        )


def _strip_shell_operator_tail(command: str) -> str:
    cut_at = len(command)
    for operator in ("&&", "||", ";", "|", "$(", "`"):
        idx = command.find(operator)
        if idx != -1:
            cut_at = min(cut_at, idx)
    return command[:cut_at]


def _browser_navigate_tool(manager: BrowserManager) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        url = payload.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ToolValidationError("Field 'url' must be a non-empty string")
        return await manager.navigate(url.strip())

    return ToolDefinition(
        name="browser_navigate",
        description=(
            "Navigate the browser to a URL. Returns page title and final URL. "
            "After navigating, use browser_snapshot to read page content."
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["url"],
            "properties": {"url": {"type": "string"}},
        },
        execute=_execute,
    )


def _browser_screenshot_tool(manager: BrowserManager) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        full_page = payload.get("full_page", True)
        if not isinstance(full_page, bool):
            raise ToolValidationError("Field 'full_page' must be a boolean")
        return await manager.screenshot(full_page=full_page)

    return ToolDefinition(
        name="browser_screenshot",
        description=(
            "Capture a screenshot of the current browser page as a PNG image. "
            "The image is rendered directly in the user's chat — use this proactively to show progress, "
            "verify page state after navigation, confirm form submissions, or when the user would benefit "
            "from seeing what the browser looks like. Use full_page=false to capture only the visible viewport."
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {"full_page": {"type": "boolean"}},
        },
        execute=_execute,
    )


def _browser_click_tool(manager: BrowserManager) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        selector = payload.get("selector")
        if not isinstance(selector, str) or not selector.strip():
            raise ToolValidationError("Field 'selector' must be a non-empty string")
        return await manager.click(selector.strip())

    return ToolDefinition(
        name="browser_click",
        description="Click an element by CSS selector.",
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["selector"],
            "properties": {"selector": {"type": "string"}},
        },
        execute=_execute,
    )


def _browser_type_tool(manager: BrowserManager) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        selector = payload.get("selector")
        text = payload.get("text")
        if not isinstance(selector, str) or not selector.strip():
            raise ToolValidationError("Field 'selector' must be a non-empty string")
        if not isinstance(text, str):
            raise ToolValidationError("Field 'text' must be a string")
        return await manager.type_text(selector.strip(), text)

    return ToolDefinition(
        name="browser_type",
        description="Type text into an element by CSS selector.",
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["selector", "text"],
            "properties": {
                "selector": {"type": "string"},
                "text": {"type": "string"},
            },
        },
        execute=_execute,
    )


def _browser_press_key_tool(manager: BrowserManager) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        key = payload.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ToolValidationError("Field 'key' must be a non-empty string")
        return await manager.press_key(key.strip())

    return ToolDefinition(
        name="browser_press_key",
        description="Press a keyboard key (e.g. Enter, Tab, Escape, ArrowDown). Uses Playwright key names.",
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["key"],
            "properties": {"key": {"type": "string"}},
        },
        execute=_execute,
    )


def _browser_get_text_tool(manager: BrowserManager) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        selector = payload.get("selector")
        if selector is not None and (not isinstance(selector, str) or not selector.strip()):
            raise ToolValidationError("Field 'selector' must be null or a non-empty string")
        return await manager.get_text(selector.strip() if isinstance(selector, str) else None)

    return ToolDefinition(
        name="browser_get_text",
        description=(
            "Extract visible text from the current page. "
            "Without a selector: uses Playwright's AI-optimized snapshot (clean accessibility tree, no CSS/JS noise). "
            "With a selector: extracts innerText from that specific element only — prefer this to limit output size. "
            "Output is capped at 10K chars. If truncated, use a specific selector to target the section you need. "
            "For finding interactive elements (buttons, links, inputs), use browser_snapshot with interactive_only=true instead."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector to extract text from a specific element. Omit to get full page content.",
                },
            },
        },
        execute=_execute,
    )


def _browser_snapshot_tool(manager: BrowserManager) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        interactive_only = payload.get("interactive_only", False)
        max_depth = payload.get("max_depth")
        if not isinstance(interactive_only, bool):
            raise ToolValidationError("Field 'interactive_only' must be a boolean")
        if max_depth is not None and (not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 1):
            raise ToolValidationError("Field 'max_depth' must be a positive integer")
        return await manager.get_snapshot(interactive_only=interactive_only, max_depth=max_depth)

    return ToolDefinition(
        name="browser_snapshot",
        description=(
            "Capture the accessibility tree of the current page as a structured snapshot. "
            "Returns roles, names, URLs, and values for all elements — clean, no CSS/JS noise, capped at 10K chars. "
            "Use interactive_only=true to see ONLY clickable/fillable elements (buttons, links, inputs) — "
            "this is the most token-efficient option when you just need to know what to interact with. "
            "Prefer this over browser_get_text for discovering page structure and interactive elements. "
            "If the snapshot returns empty, fall back to browser_get_text for page content."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "interactive_only": {
                    "type": "boolean",
                    "description": "If true, return only interactive elements (buttons, links, inputs, etc.). Much smaller output.",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum depth of the accessibility tree to return. Use 6 for efficient mode.",
                },
            },
        },
        execute=_execute,
    )


def _browser_reset_tool(manager: BrowserManager) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        if payload:
            raise ToolValidationError("browser_reset does not accept input fields")
        return await manager.reset()

    return ToolDefinition(
        name="browser_reset",
        description="Reset browser session to a clean about:blank state for recovery.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        execute=_execute,
    )
