#!/usr/bin/env python3
"""Minimal standalone agent CLI built on sentral.

This CLI does not use Sentinel sessions, websockets, or routers. It runs the
shared sentral runtime directly with a tiny local workspace toolset:

- cd
- read_file
- write_file
- run_command
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.sentral import (  # noqa: E402
    AssistantTurn,
    AgentEvent,
    AgentRuntimeEngine,
    ConversationItem,
    GenerationConfig,
    ImageBlock,
    RunTurnRequest,
    TextBlock,
    ThinkingBlock,
    ToolDefinition,
    ToolExecutionResult,
    ToolSchema as RuntimeToolSchema,
    ToolRegistry,
    TokenUsage as RuntimeTokenUsage,
    ToolCallBlock,
)
from app.services.onboarding.onboarding_defaults import (  # noqa: E402
    DEFAULT_SYSTEM_PROMPT as SENTINEL_DEFAULT_SYSTEM_PROMPT,
)
from app.services.llm.generic.base import LLMProvider  # noqa: E402
from app.services.llm.generic.errors import error_tag, is_retryable  # noqa: E402
from app.services.llm.generic.types import (  # noqa: E402
    AgentEvent as SentinelAgentEvent,
    AssistantMessage,
    ImageContent,
    ReasoningConfig,
    SystemMessage,
    TextContent,
    ThinkingContent,
    TokenUsage,
    ToolCallContent,
    ToolResultMessage,
    ToolSchema,
    UserMessage,
)
from app.services.llm.ids import parse_tier_name  # noqa: E402

CLI_EXECUTION_POLICY = (
    "## Execution Policy\n"
    "When the user asks you to execute a multi-step task, keep acting until the task is complete or a true blocker appears.\n"
    "Do not end a turn with text like 'I'll do X next' or ask for confirmation to continue when no new user permission is actually required.\n"
    "Only finish with a text-only assistant turn when the task is actually complete, you are blocked on a real external dependency, or you need required user input.\n"
    "If you want to give an intermediate progress update during an unfinished task, include that progress update in the same assistant turn as the next tool call instead of stopping after the text.\n"
    "Do not emit commentary-only completion text for unfinished work. Continue by calling the next appropriate tool in the same turn.\n"
    "If a tool fails, immediately try a different valid approach before asking the user for help.\n"
    "Only ask the user for input when required by external verification, permissions, or unavailable credentials."
)
CLI_FILE_POLICY = (
    "## File And Command Policy\n"
    "You are running locally with the user's filesystem permissions.\n"
    "The CLI start directory only sets your initial cwd; it is not a sandbox boundary.\n"
    "Use read_file/write_file for direct file operations and cd to change cwd when helpful.\n"
    "If you claim a file was created or modified, first perform the write and then verify it with read_file or run_command.\n"
    "Do not say a file was written, generated, or updated unless a tool result confirmed it."
)
DEFAULT_SYSTEM_PROMPT = (
    f"{SENTINEL_DEFAULT_SYSTEM_PROMPT}\n\n{CLI_EXECUTION_POLICY}\n\n{CLI_FILE_POLICY}"
)
DEFAULT_TIMEOUT_SECONDS = 300
MAX_FILE_BYTES = 128_000
MAX_COMMAND_OUTPUT_CHARS = 64_000


@dataclass(slots=True)
class CliConfig:
    model: str
    max_iterations: int
    temperature: float
    stream: bool
    show_thinking: bool
    system_prompt: str
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class ProviderCredentialOption:
    key: str
    label: str
    env_keys: tuple[str, ...]
    primary_provider: str


_PROVIDER_CREDENTIAL_OPTIONS = (
    ProviderCredentialOption(
        key="anthropic_oauth",
        label="Anthropic OAuth token",
        env_keys=("ANTHROPIC_OAUTH_TOKEN",),
        primary_provider="anthropic",
    ),
    ProviderCredentialOption(
        key="anthropic_api",
        label="Anthropic API key",
        env_keys=("ANTHROPIC_API_KEY",),
        primary_provider="anthropic",
    ),
    ProviderCredentialOption(
        key="openai_oauth",
        label="OpenAI Codex OAuth token",
        env_keys=("OPENAI_OAUTH_TOKEN",),
        primary_provider="openai",
    ),
    ProviderCredentialOption(
        key="openai_api",
        label="OpenAI API key",
        env_keys=("OPENAI_API_KEY",),
        primary_provider="openai",
    ),
    ProviderCredentialOption(
        key="gemini_api",
        label="Gemini API key",
        env_keys=("GEMINI_API_KEY",),
        primary_provider="gemini",
    ),
)


@dataclass(frozen=True, slots=True)
class _CliTierModelConfig:
    provider: LLMProvider
    model: str
    reasoning_config: ReasoningConfig
    temperature: float


@dataclass(frozen=True, slots=True)
class _CliTierConfig:
    primary: _CliTierModelConfig
    fallbacks: tuple[_CliTierModelConfig, ...]


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


def _env_lookup(key: str, env: Mapping[str, str] | None = None) -> str:
    raw = (env or os.environ).get(key)
    if raw is None:
        return ""
    trimmed = str(raw).strip()
    return trimmed if trimmed else ""


def _has_any_provider_credentials(env: Mapping[str, str] | None = None) -> bool:
    return any(
        _env_lookup(key, env)
        for option in _PROVIDER_CREDENTIAL_OPTIONS
        for key in option.env_keys
    )


def _prompt_choice(
    *,
    prompt: str,
    options: list[str],
    input_fn: Callable[[str], str],
) -> int:
    while True:
        print(prompt)
        for index, option in enumerate(options, start=1):
            print(f"  {index}. {option}")
        raw = _normalize_cli_input(input_fn("> ")).strip()
        if raw.isdigit():
            selected = int(raw)
            if 1 <= selected <= len(options):
                return selected - 1
        print("Invalid selection.\n")


def _normalize_cli_input(raw: str) -> str:
    return raw.replace("\x1b[200~", "").replace("\x1b[201~", "").replace("\r", "")


def _read_prompt_line(
    prompt: str,
    *,
    stdin: io.TextIOBase | None = None,
    stdout: io.TextIOBase | None = None,
) -> str:
    in_stream = stdin or sys.stdin
    out_stream = stdout or sys.stdout
    out_stream.write(prompt)
    out_stream.flush()
    raw = in_stream.readline()
    if raw == "":
        raise EOFError
    return _normalize_cli_input(raw).rstrip("\n")


def _boot_tui_select(prompt: str, options: list[tuple[str, str]]) -> str | None:
    from prompt_toolkit.shortcuts import radiolist_dialog

    return radiolist_dialog(
        title="Sentral CLI Setup",
        text=prompt,
        values=options,
        ok_text="Continue",
        cancel_text="Cancel",
    ).run()


def _boot_tui_input(prompt: str, *, default: str = "") -> str | None:
    from prompt_toolkit.shortcuts import input_dialog

    return input_dialog(
        title="Sentral CLI Setup",
        text=prompt,
        default=default,
        ok_text="Continue",
        cancel_text="Cancel",
    ).run()


def _collect_boot_provider_overrides_tui(
    *,
    env: Mapping[str, str] | None = None,
    select_fn: Callable[[str, list[tuple[str, str]]], str | None] = _boot_tui_select,
    input_value_fn: Callable[[str], str | None] | None = None,
    input_with_default_fn: Callable[[str, str], str | None] | None = None,
) -> dict[str, str]:
    if _has_any_provider_credentials(env):
        return {}

    input_value = input_value_fn or (lambda prompt: _boot_tui_input(prompt))
    input_with_default = input_with_default_fn or (
        lambda prompt, default: _boot_tui_input(prompt, default=default)
    )
    env_map = env or os.environ

    provider_key = select_fn(
        "Select a provider credential for this CLI run:",
        [(option.key, option.label) for option in _PROVIDER_CREDENTIAL_OPTIONS],
    )
    if provider_key is None:
        raise RuntimeError("Credential setup cancelled.")

    selected = next(
        option for option in _PROVIDER_CREDENTIAL_OPTIONS if option.key == provider_key
    )
    source = select_fn(
        f"How do you want to provide {selected.label}?",
        [
            ("paste", "Paste token now"),
            ("env", "Load from an environment variable name"),
        ],
    )
    if source is None:
        raise RuntimeError("Credential setup cancelled.")

    overrides: dict[str, str] = {"PRIMARY_PROVIDER": selected.primary_provider}
    if source == "paste":
        while True:
            secret = _normalize_cli_input(
                input_value(f"Paste {selected.label}:") or ""
            ).strip()
            if secret:
                overrides[selected.env_keys[0]] = secret
                print(f"Using {selected.label} for this run.\n")
                return overrides
            print("Secret cannot be empty.\n")

    while True:
        default_hint = selected.env_keys[0]
        var_name = _normalize_cli_input(
            input_with_default(
                f"Environment variable name for {selected.label}:",
                default_hint,
            )
            or ""
        ).strip() or default_hint
        value = _env_lookup(var_name, env_map)
        if value:
            overrides[selected.env_keys[0]] = value
            print(f"Using {selected.label} from {var_name}.\n")
            return overrides
        print(f"Environment variable '{var_name}' is not set.\n")


def _collect_boot_provider_overrides(
    *,
    env: Mapping[str, str] | None = None,
    input_fn: Callable[[str], str] | None = None,
    interactive: bool | None = None,
) -> dict[str, str]:
    if _has_any_provider_credentials(env):
        return {}
    is_interactive = interactive if interactive is not None else sys.stdin.isatty()
    if not is_interactive:
        return {}
    if input_fn is None:
        try:
            return _collect_boot_provider_overrides_tui(env=env)
        except ModuleNotFoundError:
            pass
    prompt_input = input_fn or _read_prompt_line

    print("No AI provider credentials found.")
    provider_idx = _prompt_choice(
        prompt="Select a provider credential for this CLI run:",
        options=[option.label for option in _PROVIDER_CREDENTIAL_OPTIONS],
        input_fn=prompt_input,
    )
    selected = _PROVIDER_CREDENTIAL_OPTIONS[provider_idx]

    source_idx = _prompt_choice(
        prompt=f"How do you want to provide {selected.label}?",
        options=[
            "Paste token now (visible)",
            "Load from an environment variable name",
        ],
        input_fn=prompt_input,
    )

    overrides: dict[str, str] = {"PRIMARY_PROVIDER": selected.primary_provider}
    env_map = env or os.environ

    if source_idx == 0:
        while True:
            secret = _normalize_cli_input(prompt_input(f"{selected.label}: ")).strip()
            if secret:
                overrides[selected.env_keys[0]] = secret
                print(f"Using {selected.label} for this run.\n")
                return overrides
            print("Secret cannot be empty.\n")

    while True:
        default_hint = selected.env_keys[0]
        var_name = _normalize_cli_input(
            prompt_input(f"Environment variable name [{default_hint}]: ")
        ).strip() or default_hint
        value = _env_lookup(var_name, env_map)
        if value:
            overrides[selected.env_keys[0]] = value
            print(f"Using {selected.label} from {var_name}.\n")
            return overrides
        print(f"Environment variable '{var_name}' is not set.\n")


def _build_provider() -> LLMProvider:
    try:
        from app.services.llm.providers.anthropic import AnthropicProvider
        from app.services.llm.providers.codex import CodexProvider
        from app.services.llm.providers.gemini import GeminiProvider
        from app.services.llm.providers.openai import OpenAIProvider
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        missing = exc.name or "unknown"
        raise RuntimeError(
            f"Missing Python dependency '{missing}'. Install backend dependencies in the active venv."
        ) from exc

    boot_overrides = _collect_boot_provider_overrides()
    env = {**os.environ, **boot_overrides}

    anthropic = None
    openai = None
    gemini = None
    openai_is_codex = False

    anthropic_token = _env_lookup("ANTHROPIC_OAUTH_TOKEN", env) or _env_lookup("ANTHROPIC_API_KEY", env)
    if anthropic_token:
        anthropic = AnthropicProvider(anthropic_token)

    openai_oauth = _env_lookup("OPENAI_OAUTH_TOKEN", env)
    openai_api_key = _env_lookup("OPENAI_API_KEY", env)
    openai_base_url = _env_lookup("OPENAI_BASE_URL", env) or "https://api.openai.com/v1"
    if openai_oauth:
        openai = CodexProvider(openai_oauth)
        openai_is_codex = True
    elif openai_api_key:
        openai = OpenAIProvider(openai_api_key, base_url=openai_base_url)

    gemini_key = _env_lookup("GEMINI_API_KEY", env)
    if gemini_key:
        gemini = GeminiProvider(gemini_key)

    if not anthropic and not openai and not gemini:
        raise RuntimeError(
            "No provider credentials found. Set one of ANTHROPIC_OAUTH_TOKEN, ANTHROPIC_API_KEY, "
            "OPENAI_OAUTH_TOKEN, OPENAI_API_KEY, or GEMINI_API_KEY."
        )

    primary_provider = _env_lookup("PRIMARY_PROVIDER", env) or "anthropic"
    llm_max_retries = int(_env_lookup("LLM_MAX_RETRIES", env) or 3)
    tier_defs = [
        (
            "fast",
            _env_lookup("TIER_FAST_ANTHROPIC_MODEL", env) or "claude-haiku-4-5-20251001",
            _env_lookup("TIER_FAST_OPENAI_MODEL", env) or "gpt-4o-mini",
            _env_lookup("TIER_FAST_CODEX_MODEL", env) or "gpt-5.3-codex-spark",
            _env_lookup("TIER_FAST_GEMINI_MODEL", env) or "gemini-3-flash-preview",
            int(_env_lookup("TIER_FAST_MAX_TOKENS", env) or 4096),
            float(_env_lookup("TIER_FAST_TEMPERATURE", env) or 0.3),
            int(_env_lookup("TIER_FAST_ANTHROPIC_THINKING_BUDGET", env) or 0),
            _env_lookup("TIER_FAST_OPENAI_REASONING_EFFORT", env),
            int(_env_lookup("TIER_FAST_GEMINI_THINKING_BUDGET", env) or 0),
        ),
        (
            "normal",
            _env_lookup("TIER_NORMAL_ANTHROPIC_MODEL", env) or "claude-sonnet-4-6",
            _env_lookup("TIER_NORMAL_OPENAI_MODEL", env) or "gpt-4o",
            _env_lookup("TIER_NORMAL_CODEX_MODEL", env) or "gpt-5.3-codex",
            _env_lookup("TIER_NORMAL_GEMINI_MODEL", env) or "gemini-3-flash-preview",
            int(_env_lookup("TIER_NORMAL_MAX_TOKENS", env) or 8192),
            float(_env_lookup("TIER_NORMAL_TEMPERATURE", env) or 0.7),
            int(_env_lookup("TIER_NORMAL_ANTHROPIC_THINKING_BUDGET", env) or 5000),
            _env_lookup("TIER_NORMAL_OPENAI_REASONING_EFFORT", env),
            int(_env_lookup("TIER_NORMAL_GEMINI_THINKING_BUDGET", env) or 0),
        ),
        (
            "hard",
            _env_lookup("TIER_HARD_ANTHROPIC_MODEL", env) or "claude-opus-4-6",
            _env_lookup("TIER_HARD_OPENAI_MODEL", env) or "o3",
            _env_lookup("TIER_HARD_CODEX_MODEL", env) or "gpt-5.3-codex",
            _env_lookup("TIER_HARD_GEMINI_MODEL", env) or "gemini-3.1-pro-preview",
            int(_env_lookup("TIER_HARD_MAX_TOKENS", env) or 40000),
            float(_env_lookup("TIER_HARD_TEMPERATURE", env) or 0.7),
            int(_env_lookup("TIER_HARD_ANTHROPIC_THINKING_BUDGET", env) or 32000),
            _env_lookup("TIER_HARD_OPENAI_REASONING_EFFORT", env) or "high",
            int(_env_lookup("TIER_HARD_GEMINI_THINKING_BUDGET", env) or 32000),
        ),
    ]

    tiers: dict[str, _CliTierConfig] = {}
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
        all_cfgs: dict[str, _CliTierModelConfig] = {}
        if anthropic:
            all_cfgs["anthropic"] = _CliTierModelConfig(
                provider=anthropic,
                model=anth_model,
                reasoning_config=ReasoningConfig(
                    max_tokens=max_tok,
                    thinking_budget=anth_budget if anth_budget > 0 else None,
                ),
                temperature=temp,
            )
        if openai:
            all_cfgs["openai"] = _CliTierModelConfig(
                provider=openai,
                model=(codex_model if openai_is_codex else oai_model),
                reasoning_config=ReasoningConfig(
                    max_tokens=max_tok,
                    reasoning_effort=oai_effort or None,
                ),
                temperature=temp,
            )
        if gemini:
            all_cfgs["gemini"] = _CliTierModelConfig(
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
            fallbacks = tuple(cfg for name, cfg in all_cfgs.items() if name != primary_provider)
        else:
            ordered = list(all_cfgs.values())
            primary = ordered[0]
            fallbacks = tuple(ordered[1:])
        tiers[tier_name] = _CliTierConfig(primary=primary, fallbacks=fallbacks)

    if not tiers:
        raise RuntimeError("No provider tiers could be built from current environment.")
    return _CliTierProvider(tiers=tiers, default_tier="normal", max_retries=llm_max_retries)


class _CliTierProvider(LLMProvider):
    """Minimal CLI-only tier router without backend schema/config dependencies."""

    def __init__(
        self,
        *,
        tiers: dict[str, _CliTierConfig],
        default_tier: str,
        max_retries: int,
        base_backoff_ms: int = 500,
    ) -> None:
        self._tiers = tiers
        self._default_tier = default_tier
        self._max_retries = max_retries
        self._base_backoff_ms = base_backoff_ms

    @property
    def name(self) -> str:
        return "cli-tier"

    async def chat(
        self,
        messages,
        model: str,
        tools=None,
        temperature: float = 0.7,
        reasoning_config=None,
        tool_choice: str | None = None,
    ) -> AssistantMessage:
        config = self._resolve_model_config(model)
        return await self._call_chat_with_fallback(
            configs=self._ordered_configs(config, model),
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )

    async def stream(
        self,
        messages,
        model: str,
        tools=None,
        temperature: float = 0.7,
        reasoning_config=None,
        tool_choice: str | None = None,
    ):
        configs = self._ordered_configs(self._resolve_model_config(model), model)
        diagnostics: list[str] = []
        for config in configs:
            for attempt in range(1, self._max_retries + 1):
                try:
                    async for event in config.provider.stream(
                        messages,
                        model=config.model,
                        tools=tools,
                        temperature=config.temperature,
                        reasoning_config=config.reasoning_config,
                        tool_choice=tool_choice,
                    ):
                        yield event
                    return
                except Exception as exc:  # noqa: BLE001
                    diagnostics.append(
                        f"provider={config.provider.name} model={config.model} attempt {attempt}/{self._max_retries}: {error_tag(exc)}"
                    )
                    if is_retryable(exc) and attempt < self._max_retries:
                        await asyncio.sleep((self._base_backoff_ms * (2 ** (attempt - 1))) / 1000)
                        continue
                    break
        raise RuntimeError("All providers failed. " + " | ".join(diagnostics))

    def _resolve_model_config(self, requested_model: str) -> _CliTierConfig:
        tier_name = parse_tier_name(requested_model)
        if tier_name is not None:
            resolved = self._tiers.get(tier_name.value)
            if resolved is not None:
                return resolved
        default_tier = self._tiers.get(self._default_tier)
        if default_tier is None:
            raise RuntimeError(f"Default tier '{self._default_tier}' is not configured.")
        return _CliTierConfig(
            primary=_CliTierModelConfig(
                provider=default_tier.primary.provider,
                model=requested_model,
                reasoning_config=default_tier.primary.reasoning_config,
                temperature=default_tier.primary.temperature,
            ),
            fallbacks=(),
        )

    def _ordered_configs(
        self,
        tier: _CliTierConfig,
        requested_model: str,
    ) -> tuple[_CliTierModelConfig, ...]:
        tier_name = parse_tier_name(requested_model)
        if tier_name is None:
            return (tier.primary,)
        return (tier.primary, *tier.fallbacks)

    async def _call_chat_with_fallback(
        self,
        *,
        configs: tuple[_CliTierModelConfig, ...],
        messages,
        tools,
        tool_choice: str | None,
    ) -> AssistantMessage:
        diagnostics: list[str] = []
        for config in configs:
            for attempt in range(1, self._max_retries + 1):
                try:
                    return await config.provider.chat(
                        messages,
                        model=config.model,
                        tools=tools,
                        temperature=config.temperature,
                        reasoning_config=config.reasoning_config,
                        tool_choice=tool_choice,
                    )
                except Exception as exc:  # noqa: BLE001
                    diagnostics.append(
                        f"provider={config.provider.name} model={config.model} attempt {attempt}/{self._max_retries}: {error_tag(exc)}"
                    )
                    if is_retryable(exc) and attempt < self._max_retries:
                        await asyncio.sleep((self._base_backoff_ms * (2 ** (attempt - 1))) / 1000)
                        continue
                    break
        raise RuntimeError("All providers failed. " + " | ".join(diagnostics))


def _runtime_item_to_sentinel_message(item: ConversationItem) -> Any:
    if item.role == "system":
        return SystemMessage(
            content="\n".join(
                block.text for block in item.content if isinstance(block, TextBlock) and block.text
            ),
            metadata=dict(item.metadata),
            timestamp=item.timestamp,
        )

    if item.role == "user":
        blocks: list[TextContent | ImageContent] = []
        for block in item.content:
            if isinstance(block, TextBlock):
                blocks.append(TextContent(text=block.text))
            elif isinstance(block, ImageBlock):
                blocks.append(ImageContent(media_type=block.media_type, data=block.data))
        if len(blocks) == 1 and isinstance(blocks[0], TextContent):
            return UserMessage(content=blocks[0].text, metadata=dict(item.metadata), timestamp=item.timestamp)
        return UserMessage(content=blocks, metadata=dict(item.metadata), timestamp=item.timestamp)

    if item.role == "assistant":
        content: list[TextContent | ThinkingContent | ToolCallContent] = []
        for block in item.content:
            if isinstance(block, TextBlock):
                content.append(TextContent(text=block.text))
            elif isinstance(block, ThinkingBlock):
                content.append(ThinkingContent(thinking=block.thinking, signature=block.signature))
            elif isinstance(block, ToolCallBlock):
                content.append(
                    ToolCallContent(
                        id=block.id,
                        name=block.name,
                        arguments=dict(block.arguments),
                        thought_signature=block.thought_signature,
                    )
                )
        metadata = dict(item.metadata)
        usage_payload = metadata.get("usage") if isinstance(metadata.get("usage"), dict) else {}
        return AssistantMessage(
            content=content,
            model=str(metadata.get("model") or ""),
            provider=str(metadata.get("provider") or ""),
            usage=TokenUsage(
                input_tokens=int(usage_payload.get("input_tokens") or 0),
                output_tokens=int(usage_payload.get("output_tokens") or 0),
            ),
            stop_reason=str(metadata.get("stop_reason") or "stop"),
        )

    tool_block = next(
        (block for block in item.content if block.type == "tool_result"),
        None,
    )
    if tool_block is None:
        return UserMessage(
            content="\n".join(block.text for block in item.content if isinstance(block, TextBlock)),
            metadata=dict(item.metadata),
            timestamp=item.timestamp,
        )
    return ToolResultMessage(
        tool_call_id=tool_block.tool_call_id,
        tool_name=tool_block.tool_name,
        content=tool_block.content,
        is_error=tool_block.is_error,
        metadata=dict(tool_block.metadata),
    )


def _sentinel_assistant_turn_to_runtime(message: AssistantMessage, *, item_id: str) -> AssistantTurn:
    metadata = {
        "model": message.model,
        "provider": message.provider,
        "stop_reason": message.stop_reason,
        "usage": {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
        },
    }
    content = []
    for block in message.content:
        if isinstance(block, TextContent):
            content.append(TextBlock(text=block.text))
        elif isinstance(block, ThinkingContent):
            content.append(ThinkingBlock(thinking=block.thinking, signature=block.signature))
        elif isinstance(block, ToolCallContent):
            content.append(
                ToolCallBlock(
                    id=block.id,
                    name=block.name,
                    arguments=dict(block.arguments),
                    thought_signature=block.thought_signature,
                )
            )
    return AssistantTurn(
        item=ConversationItem(id=item_id, role="assistant", content=content, metadata=metadata),
        stop_reason=message.stop_reason,
        usage=RuntimeTokenUsage(
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        ),
    )


def _sentinel_event_to_runtime_event(event: SentinelAgentEvent) -> AgentEvent:
    metadata: dict[str, Any] = {}
    if event.signature is not None:
        metadata["signature"] = event.signature
    if event.content_index is not None:
        metadata["content_index"] = event.content_index
    runtime_event = AgentEvent(
        type=event.type,
        delta=event.delta,
        stop_reason=event.stop_reason,
        error=event.error,
        iteration=event.iteration,
        max_iterations=event.max_iterations,
        metadata=metadata,
    )
    if event.tool_call is not None:
        runtime_event.tool_call = ToolCallBlock(
            id=event.tool_call.id,
            name=event.tool_call.name,
            arguments=dict(event.tool_call.arguments),
            thought_signature=event.tool_call.thought_signature,
        )
    return runtime_event


class CliProviderAdapter:
    """Tiny local bridge from Sentinel LLMProvider to sentral Provider."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    @property
    def name(self) -> str:
        return self._provider.name

    async def chat(
        self,
        *,
        messages: list[ConversationItem],
        tools: list[RuntimeToolSchema],
        config: GenerationConfig,
    ) -> AssistantTurn:
        response = await self._provider.chat(
            [_runtime_item_to_sentinel_message(message) for message in messages],
            model=config.model,
            tools=[
                ToolSchema(
                    name=tool.name,
                    description=tool.description,
                    parameters=dict(tool.parameters),
                )
                for tool in tools
            ],
            temperature=config.temperature,
            tool_choice=config.tool_choice,
        )
        return _sentinel_assistant_turn_to_runtime(response, item_id="assistant")

    async def stream(
        self,
        *,
        messages: list[ConversationItem],
        tools: list[RuntimeToolSchema],
        config: GenerationConfig,
    ):
        async for event in self._provider.stream(
            [_runtime_item_to_sentinel_message(message) for message in messages],
            model=config.model,
            tools=[
                ToolSchema(
                    name=tool.name,
                    description=tool.description,
                    parameters=dict(tool.parameters),
                )
                for tool in tools
            ],
            temperature=config.temperature,
            tool_choice=config.tool_choice,
        ):
            yield _sentinel_event_to_runtime_event(event)


class LocalWorkspaceToolRegistry(ToolRegistry):
    """Minimal sentral-native tools over one local filesystem context."""

    def __init__(self, *, root: Path) -> None:
        self._root = root.expanduser().resolve()
        self._cwd = self._root
        self._tools = {
            "cd": ToolDefinition(
                name="cd",
                description="Change the current working directory.",
                parameters_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path"],
                    "properties": {"path": {"type": "string"}},
                },
                execute=self._cd,
            ),
            "read_file": ToolDefinition(
                name="read_file",
                description="Read a text file relative to the current working directory or by absolute path.",
                parameters_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path"],
                    "properties": {
                        "path": {"type": "string"},
                        "max_bytes": {"type": "integer"},
                    },
                },
                execute=self._read_file,
            ),
            "write_file": ToolDefinition(
                name="write_file",
                description="Write text to a file relative to the current working directory or by absolute path.",
                parameters_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path", "content"],
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "append": {"type": "boolean"},
                        "make_parents": {"type": "boolean"},
                    },
                },
                execute=self._write_file,
            ),
            "run_command": ToolDefinition(
                name="run_command",
                description="Run a shell command in the current working directory.",
                parameters_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["command"],
                    "properties": {
                        "command": {"type": "string"},
                        "timeout_seconds": {"type": "integer"},
                    },
                },
                execute=self._run_command,
            ),
        }

    @property
    def cwd(self) -> Path:
        return self._cwd

    @property
    def root(self) -> Path:
        return self._root

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def get_tool(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def _resolve_path(self, raw: Any) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("Field 'path' must be a non-empty string")
        candidate = Path(raw.strip()).expanduser()
        if not candidate.is_absolute():
            candidate = (self._cwd / candidate).resolve()
        else:
            candidate = candidate.resolve()
        return candidate

    async def _cd(self, payload: dict[str, Any]) -> ToolExecutionResult:
        try:
            target = self._resolve_path(payload.get("path"))
        except ValueError as exc:
            return ToolExecutionResult(status="error", error=str(exc))
        if not target.exists():
            return ToolExecutionResult(status="error", error=f"Directory not found: {target}")
        if not target.is_dir():
            return ToolExecutionResult(status="error", error=f"Not a directory: {target}")
        self._cwd = target
        return ToolExecutionResult(
            status="ok",
            content={"cwd": str(self._cwd)},
        )

    async def _read_file(self, payload: dict[str, Any]) -> ToolExecutionResult:
        try:
            path = self._resolve_path(payload.get("path"))
        except ValueError as exc:
            return ToolExecutionResult(status="error", error=str(exc))
        max_bytes = payload.get("max_bytes", MAX_FILE_BYTES)
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
            return ToolExecutionResult(status="error", error="Field 'max_bytes' must be a positive integer")
        if not path.exists() or not path.is_file():
            return ToolExecutionResult(status="error", error=f"File not found: {path}")
        data = path.read_bytes()
        visible = data[:max_bytes]
        return ToolExecutionResult(
            status="ok",
            content={
                "path": str(path),
                "cwd": str(self._cwd),
                "content": visible.decode("utf-8", errors="replace"),
                "bytes_read": len(visible),
                "truncated": len(data) > max_bytes,
            },
        )

    async def _write_file(self, payload: dict[str, Any]) -> ToolExecutionResult:
        try:
            path = self._resolve_path(payload.get("path"))
        except ValueError as exc:
            return ToolExecutionResult(status="error", error=str(exc))
        content = payload.get("content")
        append = bool(payload.get("append", False))
        make_parents = bool(payload.get("make_parents", True))
        if not isinstance(content, str):
            return ToolExecutionResult(status="error", error="Field 'content' must be a string")
        if make_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with path.open(mode, encoding="utf-8") as handle:
            handle.write(content)
        return ToolExecutionResult(
            status="ok",
            content={
                "path": str(path),
                "cwd": str(self._cwd),
                "bytes_written": len(content.encode("utf-8")),
                "append": append,
            },
        )

    async def _run_command(self, payload: dict[str, Any]) -> ToolExecutionResult:
        command = payload.get("command")
        timeout_seconds = payload.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if not isinstance(command, str) or not command.strip():
            return ToolExecutionResult(status="error", error="Field 'command' must be a non-empty string")
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            return ToolExecutionResult(status="error", error="Field 'timeout_seconds' must be a positive integer")
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(self._cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=float(timeout_seconds))
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return ToolExecutionResult(
                status="error",
                error=f"Command timed out after {timeout_seconds}s",
            )
        return ToolExecutionResult(
            status="ok",
            content={
                "command": command,
                "cwd": str(self._cwd),
                "exit_code": proc.returncode,
                "stdout": _truncate_text(stdout.decode("utf-8", errors="replace")),
                "stderr": _truncate_text(stderr.decode("utf-8", errors="replace")),
            },
        )


def _truncate_text(value: str) -> str:
    if len(value) <= MAX_COMMAND_OUTPUT_CHARS:
        return value
    return value[:MAX_COMMAND_OUTPUT_CHARS] + "\n...[TRUNCATED]"


def _format_tool_arguments(arguments: dict[str, Any] | None) -> str:
    if not arguments:
        return ""
    parts: list[str] = []
    for key, value in arguments.items():
        rendered = str(value).replace("\n", "\\n")
        if len(rendered) > 120:
            rendered = rendered[:120] + "..."
        parts.append(f"{key}={rendered}")
    return " ".join(parts)


def _preview_tool_result_content(content: Any) -> str:
    if not isinstance(content, dict):
        return ""
    if isinstance(content.get("stdout"), str) and content["stdout"].strip():
        return _truncate_text(content["stdout"].strip()).splitlines()[0]
    if isinstance(content.get("content"), str) and content["content"].strip():
        return _truncate_text(content["content"].strip()).splitlines()[0]
    if "cwd" in content:
        return f"cwd={content['cwd']}"
    if "path" in content:
        return f"path={content['path']}"
    return ""


def _format_runtime_trace(event: AgentEvent) -> str | None:
    if event.type == "agent_progress" and event.iteration is not None and event.max_iterations is not None:
        return f"[iter {event.iteration}/{event.max_iterations}]"
    if event.type == "done":
        reason = event.stop_reason or "stop"
        if reason == "tool_use":
            return "[turn-stop] tool_use -> continuing"
        if reason == "stop":
            return "[turn-stop] stop -> ending"
        return f"[turn-stop] {reason}"
    return None


def _build_config(args: argparse.Namespace) -> CliConfig:
    return CliConfig(
        model=args.model,
        max_iterations=args.max_iterations,
        temperature=args.temperature,
        stream=not args.no_stream,
        show_thinking=args.show_thinking,
        system_prompt=args.system_prompt,
        timeout_seconds=args.timeout_seconds,
    )


def _compose_turn_system_prompt(
    *,
    base_prompt: str,
    workspace_root: Path,
    cwd: Path,
) -> str:
    normalized = base_prompt.strip()
    if CLI_EXECUTION_POLICY not in normalized:
        normalized = f"{normalized}\n\n{CLI_EXECUTION_POLICY}" if normalized else CLI_EXECUTION_POLICY
    if CLI_FILE_POLICY not in normalized:
        normalized = f"{normalized}\n\n{CLI_FILE_POLICY}" if normalized else CLI_FILE_POLICY
    return (
        f"{normalized}\n\n"
        "## Local Workspace Context\n"
        f"Initial CLI directory: {workspace_root}\n"
        f"Current working directory: {cwd}\n"
        "Use absolute or cwd-relative paths consistently. Prefer completing the task over asking for confirmation."
    )


def _make_runtime_system_item(
    *,
    system_prompt: str,
) -> ConversationItem:
    return ConversationItem(
        id=f"system-{uuid4().hex}",
        role="system",
        content=[TextBlock(text=system_prompt)],
    )


def _strip_runtime_system_items(history: list[ConversationItem]) -> list[ConversationItem]:
    return [item for item in history if item.role != "system"]


async def _run_turn(
    *,
    engine: AgentRuntimeEngine,
    workspace_tools: LocalWorkspaceToolRegistry,
    history: list[ConversationItem],
    prompt: str,
    config: CliConfig,
) -> list[ConversationItem]:
    printed_text = False
    last_progress: tuple[int, int] | None = None
    turn_system_prompt = _compose_turn_system_prompt(
        base_prompt=config.system_prompt,
        workspace_root=workspace_tools.root,
        cwd=workspace_tools.cwd,
    )

    async def _sink(event: AgentEvent) -> None:
        nonlocal printed_text, last_progress
        trace = _format_runtime_trace(event)
        if trace is not None:
            if event.type == "agent_progress":
                progress_key = (int(event.iteration or 0), int(event.max_iterations or 0))
                if progress_key != last_progress:
                    print(f"\n{trace}")
                    last_progress = progress_key
            else:
                print(f"\n{trace}")
            return
        if event.type == "text_delta" and event.delta:
            print(event.delta, end="", flush=True)
            printed_text = True
            return
        if event.type == "thinking_delta" and event.delta and config.show_thinking:
            print(f"\n[thinking] {event.delta}", end="", flush=True)
            return
        if event.type == "toolcall_start" and event.tool_call is not None:
            print(f"\n[tool] {event.tool_call.name}")
            return
        if event.type == "tool_result" and event.tool_result is not None:
            status = "error" if event.tool_result.is_error else "ok"
            args_preview = _format_tool_arguments(event.tool_result.tool_arguments)
            result_preview = _preview_tool_result_content(event.tool_result.content)
            details = " ".join(part for part in (args_preview, result_preview) if part)
            suffix = f" {details}" if details else ""
            print(f"[tool-result:{status}] {event.tool_result.tool_name}{suffix}")
            return
        if event.type == "error" and event.error:
            print(f"\n[error] {event.error}")

    result = await engine.run_turn(
        RunTurnRequest(
            history=[
                _make_runtime_system_item(system_prompt=turn_system_prompt),
                *history,
            ],
            new_items=[
                ConversationItem(
                    id=f"user-{uuid4().hex}",
                    role="user",
                    content=[TextBlock(text=prompt)],
                )
            ],
            config=GenerationConfig(
                model=config.model,
                temperature=config.temperature,
                max_iterations=config.max_iterations,
                stream=config.stream,
                system_prompt=turn_system_prompt,
                provider_metadata={"timeout_seconds": config.timeout_seconds},
            ),
        ),
        sink=_sink,
    )
    if printed_text:
        print()
    final_text = ""
    if result.final_item is not None:
        final_text = "\n".join(
            block.text for block in result.final_item.content if isinstance(block, TextBlock) and block.text
        ).strip()
    if not printed_text and final_text:
        print(final_text)
    if result.status != "completed":
        print(f"[status] {result.status}")
        if result.error:
            print(f"[error] {result.error}")
    print(f"[cwd] {workspace_tools.cwd}")
    return _strip_runtime_system_items(result.history)


async def _interactive_loop(
    *,
    engine: AgentRuntimeEngine,
    workspace_tools: LocalWorkspaceToolRegistry,
    config: CliConfig,
) -> None:
    history: list[ConversationItem] = []
    print("Sentral CLI")
    print("Commands: /exit, /tools, /cwd")
    while True:
        try:
            prompt = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not prompt:
            continue
        if prompt in {"/exit", "/quit"}:
            break
        if prompt == "/tools":
            tool_names = [tool.name for tool in workspace_tools.list_tools()]
            print(", ".join(tool_names))
            continue
        if prompt == "/cwd":
            print(workspace_tools.cwd)
            continue
        history = await _run_turn(
            engine=engine,
            workspace_tools=workspace_tools,
            history=history,
            prompt=prompt,
            config=config,
        )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal standalone sentral CLI.")
    parser.add_argument("prompt", nargs="*", help="One-shot prompt. Omit for interactive mode.")
    parser.add_argument("--model", default=_env_str("AGENT_MODEL", "normal"))
    parser.add_argument("--workspace", default=str(Path.cwd()))
    parser.add_argument("--max-iterations", type=int, default=_env_int("AGENT_MAX_ITERATIONS", 50))
    parser.add_argument("--temperature", type=float, default=_env_float("AGENT_TEMPERATURE", 0.7))
    parser.add_argument("--timeout-seconds", type=int, default=_env_int("AGENT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    parser.add_argument("--system-prompt", default=_env_str("AGENT_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT))
    parser.add_argument("--no-stream", action="store_true")
    parser.add_argument("--show-thinking", action="store_true")
    return parser.parse_args(argv)


async def _main(argv: list[str]) -> int:
    args = _parse_args(argv)
    provider = _build_provider()
    workspace_tools = LocalWorkspaceToolRegistry(root=Path(args.workspace))
    engine = AgentRuntimeEngine(
        provider=CliProviderAdapter(provider),
        tool_registry=workspace_tools,
    )
    config = _build_config(args)
    history: list[ConversationItem] = []

    prompt = " ".join(args.prompt).strip()
    if prompt:
        await _run_turn(
            engine=engine,
            workspace_tools=workspace_tools,
            history=history,
            prompt=prompt,
            config=config,
        )
        return 0

    await _interactive_loop(
        engine=engine,
        workspace_tools=workspace_tools,
        config=config,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(_main(list(argv if argv is not None else sys.argv[1:])))
    except KeyboardInterrupt:
        return 130
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except subprocess.SubprocessError as exc:
        print(f"subprocess error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
