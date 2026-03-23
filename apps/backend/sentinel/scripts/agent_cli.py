#!/usr/bin/env python3
"""Standalone Sentinel agent CLI (no backend session/websocket/UI).

This runs provider + tool loop directly in-process so agentic behavior can be
verified in isolation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import socket
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Make this script runnable directly without requiring PYTHONPATH exports.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services.llm.generic.types import (  # noqa: E402
    AgentMessage,
    AssistantMessage,
    ReasoningConfig,
    SystemMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    ToolSchema,
    TokenUsage,
    UserMessage,
)
from app.services.tools.browser_tool import BrowserManager  # noqa: E402
from app.services.tools.executor import ToolExecutor, ToolValidationError  # noqa: E402
from app.services.tools.registry import ToolDefinition, ToolRegistry  # noqa: E402

DEFAULT_SYSTEM_PROMPT = (
    "You are Sentinel, an autonomous operator. Be direct, execute tasks end-to-end, "
    "and use tools when needed."
)

_MAX_TOOL_RESULT_BYTES = 50_000
_MAX_HTTP_RESPONSE_BYTES = 1_048_576


@dataclass(slots=True)
class CliConfig:
    model: str
    max_iterations: int
    temperature: float
    show_thinking: bool
    verbose_tools: bool
    tools_enabled: bool
    system_prompt: str


class CliToolAdapter:
    """Minimal tool adapter for standalone CLI mode (no DB/estop dependency)."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._executor = ToolExecutor(registry)

    def get_tool_schemas(self) -> list[ToolSchema]:
        schemas: list[ToolSchema] = []
        for tool in self._registry.list_all():
            if not tool.enabled:
                continue
            schemas.append(
                ToolSchema(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.parameters_schema,
                )
            )
        return schemas

    async def execute_tool_calls(self, calls: list[ToolCallContent]) -> list[ToolResultMessage]:
        tasks = [self._execute_one(call) for call in calls]
        return await asyncio.gather(*tasks)

    async def _execute_one(self, call: ToolCallContent) -> ToolResultMessage:
        try:
            payload = call.arguments if isinstance(call.arguments, dict) else {}
            result, _duration_ms = await self._executor.execute(
                call.name,
                dict(payload),
            )
            content = self._truncate_content(json.dumps(result, default=str))
            return ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=content,
                is_error=False,
            )
        except KeyError:
            return ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=self._truncate_content(f"Tool '{call.name}' is not registered"),
                is_error=True,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=self._truncate_content(str(exc)),
                is_error=True,
            )

    @staticmethod
    def _truncate_content(content: str) -> str:
        encoded = content.encode("utf-8", errors="replace")
        total_bytes = len(encoded)
        if total_bytes <= _MAX_TOOL_RESULT_BYTES:
            return content
        head = encoded[:_MAX_TOOL_RESULT_BYTES].decode("utf-8", errors="replace")
        return f"{head}\n...[TRUNCATED - {total_bytes} bytes total]"


def _env_str(key: str, default: str = "") -> str:
    value = os.getenv(key)
    if value is None:
        return default
    trimmed = value.strip()
    return trimmed if trimmed else default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except Exception:
        return default


def _build_cli_provider() -> Any:
    """Build tiered provider routing from env vars only."""
    try:
        from app.services.llm.generic.tier import TierConfig, TierModelConfig, TierProvider
        from app.services.llm.providers.anthropic import AnthropicProvider
        from app.services.llm.providers.codex import CodexProvider
        from app.services.llm.providers.gemini import GeminiProvider
        from app.services.llm.providers.openai import OpenAIProvider
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        missing = exc.name or "unknown"
        raise RuntimeError(
            f"Missing Python dependency '{missing}'. Install backend dependencies in the active venv."
        ) from exc

    anthropic = None
    openai = None
    gemini = None
    openai_is_codex = False

    anthropic_token = _env_str("ANTHROPIC_OAUTH_TOKEN") or _env_str("ANTHROPIC_API_KEY")
    if anthropic_token:
        anthropic = AnthropicProvider(anthropic_token)

    openai_oauth = _env_str("OPENAI_OAUTH_TOKEN")
    openai_api_key = _env_str("OPENAI_API_KEY")
    openai_base_url = _env_str("OPENAI_BASE_URL", "https://api.openai.com/v1")
    if openai_oauth:
        openai = CodexProvider(openai_oauth)
        openai_is_codex = True
    elif openai_api_key:
        openai = OpenAIProvider(openai_api_key, base_url=openai_base_url)

    gemini_key = _env_str("GEMINI_API_KEY")
    if gemini_key:
        gemini = GeminiProvider(gemini_key)

    if not anthropic and not openai and not gemini:
        return None

    primary_provider = _env_str("PRIMARY_PROVIDER", "anthropic")
    llm_max_retries = _env_int("LLM_MAX_RETRIES", 3)

    tier_defs = [
        (
            "fast",
            _env_str("TIER_FAST_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
            _env_str("TIER_FAST_OPENAI_MODEL", "gpt-4o-mini"),
            _env_str("TIER_FAST_CODEX_MODEL", "gpt-5.3-codex-spark"),
            _env_str("TIER_FAST_GEMINI_MODEL", "gemini-3-flash-preview"),
            _env_int("TIER_FAST_MAX_TOKENS", 4096),
            _env_float("TIER_FAST_TEMPERATURE", 0.3),
            _env_int("TIER_FAST_ANTHROPIC_THINKING_BUDGET", 0),
            _env_str("TIER_FAST_OPENAI_REASONING_EFFORT", ""),
            _env_int("TIER_FAST_GEMINI_THINKING_BUDGET", 0),
        ),
        (
            "normal",
            _env_str("TIER_NORMAL_ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            _env_str("TIER_NORMAL_OPENAI_MODEL", "gpt-4o"),
            _env_str("TIER_NORMAL_CODEX_MODEL", "gpt-5.3-codex"),
            _env_str("TIER_NORMAL_GEMINI_MODEL", "gemini-3-flash-preview"),
            _env_int("TIER_NORMAL_MAX_TOKENS", 8192),
            _env_float("TIER_NORMAL_TEMPERATURE", 0.7),
            _env_int("TIER_NORMAL_ANTHROPIC_THINKING_BUDGET", 5000),
            _env_str("TIER_NORMAL_OPENAI_REASONING_EFFORT", ""),
            _env_int("TIER_NORMAL_GEMINI_THINKING_BUDGET", 0),
        ),
        (
            "hard",
            _env_str("TIER_HARD_ANTHROPIC_MODEL", "claude-opus-4-6"),
            _env_str("TIER_HARD_OPENAI_MODEL", "o3"),
            _env_str("TIER_HARD_CODEX_MODEL", "gpt-5.3-codex"),
            _env_str("TIER_HARD_GEMINI_MODEL", "gemini-3.1-pro-preview"),
            _env_int("TIER_HARD_MAX_TOKENS", 40000),
            _env_float("TIER_HARD_TEMPERATURE", 0.7),
            _env_int("TIER_HARD_ANTHROPIC_THINKING_BUDGET", 32000),
            _env_str("TIER_HARD_OPENAI_REASONING_EFFORT", "high"),
            _env_int("TIER_HARD_GEMINI_THINKING_BUDGET", 32000),
        ),
    ]

    tiers: dict[str, TierConfig] = {}
    for (
        tier_name,
        anth_model,
        oai_model,
        codex_model,
        gem_model,
        max_tok,
        temp,
        anth_budget,
        oai_effort,
        gem_budget,
    ) in tier_defs:
        all_cfgs: dict[str, TierModelConfig] = {}
        if anthropic:
            all_cfgs["anthropic"] = TierModelConfig(
                provider=anthropic,
                model=anth_model,
                reasoning_config=ReasoningConfig(
                    max_tokens=max_tok,
                    thinking_budget=anth_budget if anth_budget > 0 else None,
                ),
                temperature=temp,
            )
        if openai:
            all_cfgs["openai"] = TierModelConfig(
                provider=openai,
                model=(codex_model if openai_is_codex else oai_model),
                reasoning_config=ReasoningConfig(
                    max_tokens=max_tok,
                    reasoning_effort=oai_effort or None,
                ),
                temperature=temp,
            )
        if gemini:
            all_cfgs["gemini"] = TierModelConfig(
                provider=gemini,
                model=gem_model,
                reasoning_config=ReasoningConfig(
                    max_tokens=max_tok,
                    thinking_budget=gem_budget if gem_budget > 0 else None,
                ),
                temperature=temp,
            )

        if not all_cfgs:
            continue
        if primary_provider in all_cfgs:
            primary = all_cfgs[primary_provider]
            fallbacks = [cfg for name, cfg in all_cfgs.items() if name != primary_provider]
        else:
            ordered = list(all_cfgs.values())
            primary = ordered[0]
            fallbacks = ordered[1:]
        tiers[tier_name] = TierConfig(primary=primary, fallbacks=fallbacks)

    if not tiers:
        return None
    return TierProvider(tiers=tiers, default_tier="normal", max_retries=llm_max_retries)


def _resolve_file_read_base() -> Path:
    raw = _env_str("TOOL_FILE_READ_BASE_DIR", str(Path.cwd()))
    return Path(raw).expanduser().resolve()


async def _validate_hostname(hostname: str) -> None:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ToolValidationError(f"Unable to resolve host: {hostname}") from exc
    if not infos:
        raise ToolValidationError(f"Unable to resolve host: {hostname}")


async def _file_read(payload: dict[str, Any]) -> dict[str, Any]:
    path_raw = payload.get("path")
    if not isinstance(path_raw, str) or not path_raw.strip():
        raise ToolValidationError("Field 'path' must be a non-empty string")
    max_bytes = payload.get("max_bytes", 4096)
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ToolValidationError("Field 'max_bytes' must be a positive integer")

    allowed_base = _resolve_file_read_base()
    path = Path(path_raw).expanduser().resolve()
    if path != allowed_base and allowed_base not in path.parents:
        raise ToolValidationError(f"Path outside allowed directory: {allowed_base}")
    if not path.exists() or not path.is_file():
        raise ToolValidationError("File not found")

    data = path.read_bytes()
    chunk = data[:max_bytes]
    return {
        "path": str(path),
        "content": chunk.decode("utf-8", errors="replace"),
        "bytes_read": len(chunk),
        "truncated": len(data) > max_bytes,
    }


async def _http_request(payload: dict[str, Any]) -> dict[str, Any]:
    import httpx

    url = payload.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ToolValidationError("Field 'url' must be a non-empty string")

    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ToolValidationError("Field 'url' must be a valid http/https URL")
    await _validate_hostname(parsed.hostname)

    method = payload.get("method", "GET")
    if not isinstance(method, str):
        raise ToolValidationError("Field 'method' must be a string")
    method = method.upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ToolValidationError("Unsupported HTTP method")

    timeout_seconds = payload.get("timeout_seconds", 20)
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise ToolValidationError("Field 'timeout_seconds' must be a positive integer")

    headers_payload = payload.get("headers", {})
    if headers_payload is None:
        headers_payload = {}
    if not isinstance(headers_payload, dict):
        raise ToolValidationError("Field 'headers' must be an object")
    headers = {str(k): str(v) for k, v in headers_payload.items()}

    request_kwargs: dict[str, Any] = {"headers": headers}
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


def _build_registry(*, browser_manager: BrowserManager, no_tools: bool) -> ToolRegistry:
    registry = ToolRegistry()
    if no_tools:
        return registry

    registry.register(
        ToolDefinition(
            name="file_read",
            description="Read text content from a local file path with byte limit.",
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["path"],
                "properties": {
                    "path": {"type": "string"},
                    "max_bytes": {"type": "integer"},
                },
            },
            execute=_file_read,
        )
    )
    registry.register(
        ToolDefinition(
            name="http_request",
            description="Make outbound HTTP requests.",
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
            execute=_http_request,
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_navigate",
            description="Navigate browser to a URL.",
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["url"],
                "properties": {
                    "url": {"type": "string"},
                    "timeout_ms": {"type": "integer"},
                },
            },
            execute=lambda payload: browser_manager.navigate(
                str(payload["url"]),
                timeout_ms=(int(payload["timeout_ms"]) if payload.get("timeout_ms") is not None else None),
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_screenshot",
            description="Take a browser screenshot.",
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"full_page": {"type": "boolean"}},
            },
            execute=lambda payload: browser_manager.screenshot(full_page=bool(payload.get("full_page", True))),
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_click",
            description="Click an element by selector.",
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["selector"],
                "properties": {
                    "selector": {"type": "string"},
                    "timeout_ms": {"type": "integer"},
                },
            },
            execute=lambda payload: browser_manager.click(
                str(payload["selector"]),
                timeout_ms=(int(payload["timeout_ms"]) if payload.get("timeout_ms") is not None else None),
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_type",
            description="Type text into an element by selector.",
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["selector", "text"],
                "properties": {
                    "selector": {"type": "string"},
                    "text": {"type": "string"},
                    "timeout_ms": {"type": "integer"},
                },
            },
            execute=lambda payload: browser_manager.type_text(
                str(payload["selector"]),
                str(payload["text"]),
                timeout_ms=(int(payload["timeout_ms"]) if payload.get("timeout_ms") is not None else None),
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_select",
            description="Select an option in a <select> field.",
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["selector"],
                "properties": {
                    "selector": {"type": "string"},
                    "value": {"type": "string"},
                    "label": {"type": "string"},
                    "index": {"type": "integer"},
                    "timeout_ms": {"type": "integer"},
                },
            },
            execute=lambda payload: browser_manager.select_option(
                str(payload["selector"]),
                value=(str(payload["value"]) if payload.get("value") is not None else None),
                label=(str(payload["label"]) if payload.get("label") is not None else None),
                index=(int(payload["index"]) if payload.get("index") is not None else None),
                timeout_ms=(int(payload["timeout_ms"]) if payload.get("timeout_ms") is not None else None),
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_wait_for",
            description="Wait for an element state.",
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["selector"],
                "properties": {
                    "selector": {"type": "string"},
                    "condition": {
                        "type": "string",
                        "enum": ["visible", "hidden", "attached", "detached", "enabled", "disabled"],
                    },
                    "timeout_ms": {"type": "integer"},
                },
            },
            execute=lambda payload: browser_manager.wait_for(
                str(payload["selector"]),
                condition=str(payload.get("condition", "visible")),
                timeout_ms=(int(payload["timeout_ms"]) if payload.get("timeout_ms") is not None else None),
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_get_value",
            description="Get current value/state for an element.",
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["selector"],
                "properties": {"selector": {"type": "string"}},
            },
            execute=lambda payload: browser_manager.get_value(str(payload["selector"])),
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_fill_form",
            description="Execute a sequence of form steps.",
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["steps"],
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "selector": {"type": "string"},
                                "action": {"type": "string"},
                                "text": {"type": "string"},
                                "value": {"type": "string"},
                                "label": {"type": "string"},
                                "index": {"type": "integer"},
                                "condition": {"type": "string"},
                                "timeout_ms": {"type": "integer"},
                                "click": {"type": "boolean"},
                            },
                        },
                    },
                    "continue_on_error": {"type": "boolean"},
                    "verify": {"type": "boolean"},
                },
            },
            execute=lambda payload: browser_manager.fill_form(
                payload["steps"],
                continue_on_error=bool(payload.get("continue_on_error", False)),
                verify=bool(payload.get("verify", False)),
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_press_key",
            description="Press a keyboard key in the current page.",
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["key"],
                "properties": {"key": {"type": "string"}},
            },
            execute=lambda payload: browser_manager.press_key(str(payload["key"])),
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_get_text",
            description="Extract visible text from page or selector.",
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"selector": {"type": "string"}},
            },
            execute=lambda payload: browser_manager.get_text(
                str(payload["selector"]) if payload.get("selector") is not None else None
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_snapshot",
            description="Get accessibility snapshot for page understanding.",
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "interactive_only": {"type": "boolean"},
                    "max_depth": {"type": "integer"},
                },
            },
            execute=lambda payload: browser_manager.get_snapshot(
                interactive_only=bool(payload.get("interactive_only", False)),
                max_depth=(int(payload["max_depth"]) if payload.get("max_depth") is not None else None),
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_reset",
            description="Close and relaunch browser context.",
            parameters_schema={"type": "object", "additionalProperties": False, "properties": {}},
            execute=lambda payload: browser_manager.reset(),
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_tabs",
            description="List current browser tabs.",
            parameters_schema={"type": "object", "additionalProperties": False, "properties": {}},
            execute=lambda payload: browser_manager.list_tabs(),
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_tab_open",
            description="Open a new browser tab.",
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"url": {"type": "string"}},
            },
            execute=lambda payload: browser_manager.open_tab(str(payload.get("url") or "about:blank")),
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_tab_focus",
            description="Focus an existing browser tab.",
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["tab_id"],
                "properties": {"tab_id": {"type": "string"}},
            },
            execute=lambda payload: browser_manager.focus_tab(str(payload["tab_id"])),
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_tab_close",
            description="Close an existing browser tab.",
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["tab_id"],
                "properties": {"tab_id": {"type": "string"}},
            },
            execute=lambda payload: browser_manager.close_tab(str(payload["tab_id"])),
        )
    )

    return registry


class StandaloneAgentCli:
    """Minimal in-memory think/act loop for provider + tool behavior."""

    def __init__(
        self,
        *,
        provider: Any,
        tool_registry: ToolRegistry,
        config: CliConfig,
    ) -> None:
        self._provider = provider
        self._config = config
        self._tool_adapter = CliToolAdapter(tool_registry)
        self._messages: list[AgentMessage] = [
            SystemMessage(
                content=(
                    f"{config.system_prompt.strip()}\n\n"
                    "Runtime mode: standalone CLI (no persisted backend session).\n"
                    f"Current UTC time: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}."
                ),
            )
        ]

    @property
    def tools(self) -> list[str]:
        return [schema.name for schema in self._tool_adapter.get_tool_schemas()]

    def reset(self) -> None:
        self._messages = [self._messages[0]]

    async def ask(self, prompt: str) -> str:
        self._messages.append(UserMessage(content=prompt))
        final_text = ""

        for iteration in range(1, self._config.max_iterations + 1):
            print(f"\n[iter {iteration}/{self._config.max_iterations}]")
            assistant = await self._stream_and_assemble()
            self._messages.append(assistant)
            final_text = self._assistant_text(assistant)

            tool_calls = [
                block for block in assistant.content if isinstance(block, ToolCallContent)
            ]
            if assistant.stop_reason != "tool_use" or not tool_calls:
                break

            tool_results = await self._tool_adapter.execute_tool_calls(tool_calls)
            for result in tool_results:
                self._print_tool_result(result)
                self._messages.append(result)
        else:
            print(
                "\n[warn] max iterations reached before terminal response. "
                "Increase --max-iterations if needed.")

        return final_text

    async def _stream_and_assemble(self) -> AssistantMessage:
        text_blocks: dict[int, list[str]] = {}
        thinking_blocks: dict[int, list[str]] = {}
        tool_blocks: dict[int, dict[str, Any]] = {}
        block_sequence: list[tuple[str, int]] = []
        seen_blocks: set[tuple[str, int]] = set()
        stop_reason = "stop"
        printed_any_text = False

        def remember(kind: str, idx: int) -> None:
            key = (kind, idx)
            if key in seen_blocks:
                return
            seen_blocks.add(key)
            block_sequence.append(key)

        async for event in self._provider.stream(
            self._messages,
            model=self._config.model,
            tools=self._tool_adapter.get_tool_schemas() if self._config.tools_enabled else [],
            temperature=self._config.temperature,
            reasoning_config=None,
            tool_choice=None,
        ):
            if event.type == "text_start":
                idx = event.content_index or 0
                remember("text", idx)
                text_blocks.setdefault(idx, [])
            elif event.type == "text_delta":
                idx = event.content_index or 0
                remember("text", idx)
                delta = event.delta or ""
                text_blocks.setdefault(idx, []).append(delta)
                if delta:
                    print(delta, end="", flush=True)
                    printed_any_text = True
            elif event.type == "thinking_delta":
                idx = event.content_index or 0
                remember("thinking", idx)
                delta = event.delta or ""
                thinking_blocks.setdefault(idx, []).append(delta)
                if self._config.show_thinking and delta:
                    print(delta, end="", flush=True)
            elif event.type == "toolcall_start":
                idx = event.content_index or 0
                remember("tool_call", idx)
                call = event.tool_call
                tool_blocks[idx] = {
                    "id": (call.id if call else ""),
                    "name": (call.name if call else ""),
                    "args_chunks": [],
                    "thought_signature": (call.thought_signature if call else None),
                }
                print(
                    f"\n[tool_call] {tool_blocks[idx]['name']} id={tool_blocks[idx]['id']}",
                    flush=True,
                )
            elif event.type == "toolcall_delta":
                idx = event.content_index or 0
                remember("tool_call", idx)
                if idx in tool_blocks and event.delta:
                    tool_blocks[idx]["args_chunks"].append(event.delta)
            elif event.type == "done":
                stop_reason = event.stop_reason or "stop"
            elif event.type == "error":
                raise RuntimeError(event.error or "Provider stream failed")

        if printed_any_text:
            print("")

        content: list[TextContent | ThinkingContent | ToolCallContent] = []
        for kind, idx in block_sequence:
            if kind == "text":
                text = "".join(text_blocks.get(idx, [])).strip()
                if text:
                    content.append(TextContent(text=text))
            elif kind == "thinking":
                thinking = "".join(thinking_blocks.get(idx, [])).strip()
                if thinking:
                    content.append(ThinkingContent(thinking=thinking))
            elif kind == "tool_call":
                tb = tool_blocks.get(idx, {})
                raw = "".join(tb.get("args_chunks", []))
                parsed_args: dict[str, Any]
                if raw:
                    try:
                        loaded = json.loads(raw)
                        parsed_args = loaded if isinstance(loaded, dict) else {"value": loaded}
                    except json.JSONDecodeError:
                        parsed_args = {"raw": raw}
                else:
                    parsed_args = {}
                content.append(
                    ToolCallContent(
                        id=str(tb.get("id") or ""),
                        name=str(tb.get("name") or ""),
                        arguments=parsed_args,
                        thought_signature=(
                            str(tb.get("thought_signature")).strip()
                            if isinstance(tb.get("thought_signature"), str)
                            and str(tb.get("thought_signature")).strip()
                            else None
                        ),
                    )
                )

        return AssistantMessage(
            content=content,
            model=self._config.model,
            provider=getattr(self._provider, "name", "unknown"),
            usage=TokenUsage(),
            stop_reason=stop_reason,  # type: ignore[arg-type]
        )

    @staticmethod
    def _assistant_text(message: AssistantMessage) -> str:
        return "\n".join(
            block.text for block in message.content if isinstance(block, TextContent) and block.text
        ).strip()

    def _print_tool_result(self, result: ToolResultMessage) -> None:
        status = "error" if result.is_error else "ok"
        print(f"[tool_result:{status}] {result.tool_name}")
        if self._config.verbose_tools:
            try:
                parsed = json.loads(result.content)
                print(json.dumps(parsed, indent=2, ensure_ascii=False))
            except json.JSONDecodeError:
                preview = result.content
                if len(preview) > 1500:
                    preview = preview[:1500] + "\n...[truncated]"
                print(preview)


def _resolve_system_prompt(args: argparse.Namespace) -> str:
    if args.system_prompt:
        return str(args.system_prompt)
    if args.system_prompt_file:
        return Path(args.system_prompt_file).read_text(encoding="utf-8")
    return _env_str("DEFAULT_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Standalone Sentinel agent CLI (no UI/session APIs).",
    )
    parser.add_argument("--model", default="hint:normal", help="Model id or tier hint.")
    parser.add_argument("--max-iterations", type=int, default=25)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--show-thinking", action="store_true")
    parser.add_argument("--verbose-tools", action="store_true")
    parser.add_argument("--no-tools", action="store_true", help="Disable all tool usage.")
    parser.add_argument("--list-tools", action="store_true", help="List tool names and exit.")
    parser.add_argument("--prompt", help="Run one-shot prompt and exit.")
    parser.add_argument("--system-prompt", help="Override system prompt text.")
    parser.add_argument("--system-prompt-file", help="Read system prompt from file.")
    return parser.parse_args()


def _print_help_commands() -> None:
    print(
        "\nCommands:\n"
        "  /help                 Show CLI command help\n"
        "  /reset                Clear conversation history\n"
        "  /model <id>           Change model for next turns\n"
        "  /max <n>              Change max iterations\n"
        "  /tools                List available tools\n"
        "  /quit                 Exit\n"
    )


async def _run() -> int:
    args = _parse_args()

    browser_manager = BrowserManager()
    try:
        registry = _build_registry(browser_manager=browser_manager, no_tools=bool(args.no_tools))

        if args.list_tools:
            for tool in registry.list_all():
                print(tool.name)
            return 0

        try:
            provider = _build_cli_provider()
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if provider is None:
            print(
                "No LLM provider configured. Set at least one provider credential in env "
                "(ANTHROPIC_API_KEY/ANTHROPIC_OAUTH_TOKEN, OPENAI_API_KEY/OPENAI_OAUTH_TOKEN, GEMINI_API_KEY).",
                file=sys.stderr,
            )
            return 1

        cli = StandaloneAgentCli(
            provider=provider,
            tool_registry=registry,
            config=CliConfig(
                model=str(args.model),
                max_iterations=max(1, int(args.max_iterations)),
                temperature=float(args.temperature),
                show_thinking=bool(args.show_thinking),
                verbose_tools=bool(args.verbose_tools),
                tools_enabled=not bool(args.no_tools),
                system_prompt=_resolve_system_prompt(args),
            ),
        )

        if args.prompt:
            await cli.ask(str(args.prompt))
            return 0

        print("Sentinel standalone agent CLI")
        print(f"Model: {cli._config.model}")
        print(f"Tools: {len(cli.tools)} loaded")
        _print_help_commands()

        while True:
            try:
                line = input("\nYou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                break
            if not line:
                continue
            if line.startswith("/"):
                parts = shlex.split(line)
                command = parts[0].lower()
                if command in {"/quit", "/exit"}:
                    break
                if command == "/help":
                    _print_help_commands()
                    continue
                if command == "/reset":
                    cli.reset()
                    print("Conversation reset.")
                    continue
                if command == "/tools":
                    for name in cli.tools:
                        print(name)
                    continue
                if command == "/model":
                    if len(parts) < 2:
                        print("Usage: /model <model-id>")
                        continue
                    cli._config.model = parts[1]
                    print(f"Model set to: {cli._config.model}")
                    continue
                if command == "/max":
                    if len(parts) < 2:
                        print("Usage: /max <iterations>")
                        continue
                    try:
                        cli._config.max_iterations = max(1, int(parts[1]))
                    except ValueError:
                        print("Invalid integer.")
                        continue
                    print(f"Max iterations set to: {cli._config.max_iterations}")
                    continue
                print("Unknown command. Use /help.")
                continue

            try:
                await cli.ask(line)
            except Exception as exc:  # noqa: BLE001
                print(f"[error] {exc}", file=sys.stderr)
        return 0
    finally:
        await browser_manager.close()


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
