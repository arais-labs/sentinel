from __future__ import annotations

import io
from pathlib import Path

import pytest

from app.services.llm.generic.types import AssistantMessage, ReasoningConfig, SystemMessage, TextContent, TokenUsage
from app.sentral import ConversationItem, TextBlock, ToolResultBlock
from app.sentral import AgentEvent
from scripts.agent_cli import (
    CliProviderAdapter,
    LocalWorkspaceToolRegistry,
    _CliTierConfig,
    _CliTierModelConfig,
    _CliTierProvider,
    _strip_runtime_system_items,
    _compose_turn_system_prompt,
    _collect_boot_provider_overrides,
    _format_runtime_trace,
    _format_tool_arguments,
    _preview_tool_result_content,
    _runtime_item_to_sentinel_message,
    _read_prompt_line,
)


@pytest.mark.asyncio
async def test_local_workspace_tool_registry_lists_only_minimal_tools(tmp_path: Path) -> None:
    registry = LocalWorkspaceToolRegistry(root=tmp_path)

    assert [tool.name for tool in registry.list_tools()] == [
        "cd",
        "read_file",
        "write_file",
        "run_command",
    ]


@pytest.mark.asyncio
async def test_local_workspace_tools_can_write_read_cd_and_run(tmp_path: Path) -> None:
    registry = LocalWorkspaceToolRegistry(root=tmp_path)
    mkdir = tmp_path / "notes"
    mkdir.mkdir()

    cd_tool = registry.get_tool("cd")
    write_tool = registry.get_tool("write_file")
    read_tool = registry.get_tool("read_file")
    run_tool = registry.get_tool("run_command")

    assert cd_tool is not None
    assert write_tool is not None
    assert read_tool is not None
    assert run_tool is not None

    cd_result = await cd_tool.execute({"path": "notes"})
    assert cd_result.status == "ok"
    assert registry.cwd == mkdir

    write_result = await write_tool.execute({"path": "todo.txt", "content": "ship sentral\n"})
    assert write_result.status == "ok"
    assert (mkdir / "todo.txt").read_text(encoding="utf-8") == "ship sentral\n"

    read_result = await read_tool.execute({"path": "todo.txt"})
    assert read_result.status == "ok"
    assert read_result.content["content"] == "ship sentral\n"

    run_result = await run_tool.execute(
        {
            "command": "python3 -c 'import os; print(os.getcwd())'",
            "timeout_seconds": 30,
        }
    )
    assert run_result.status == "ok"
    assert str(mkdir) in run_result.content["stdout"]


@pytest.mark.asyncio
async def test_local_workspace_tools_allow_absolute_paths_outside_initial_root(tmp_path: Path) -> None:
    registry = LocalWorkspaceToolRegistry(root=tmp_path)
    read_tool = registry.get_tool("read_file")
    cd_tool = registry.get_tool("cd")
    assert read_tool is not None
    assert cd_tool is not None

    external_dir = tmp_path.parent / "external-cli-fixture"
    external_dir.mkdir(exist_ok=True)
    external_file = external_dir / "outside.txt"
    external_file.write_text("outside\n", encoding="utf-8")

    read_result = await read_tool.execute({"path": str(external_file)})
    cd_result = await cd_tool.execute({"path": str(external_dir)})

    assert read_result.status == "ok"
    assert read_result.content["content"] == "outside\n"
    assert cd_result.status == "ok"
    assert registry.cwd == external_dir


def test_collect_boot_provider_overrides_from_pasted_secret() -> None:
    prompts = iter(["3", "1", "\x1b[200~oauth-secret\x1b[201~"])

    overrides = _collect_boot_provider_overrides(
        env={},
        input_fn=lambda _prompt: next(prompts),
        interactive=True,
    )

    assert overrides == {
        "PRIMARY_PROVIDER": "openai",
        "OPENAI_OAUTH_TOKEN": "oauth-secret",
    }


def test_collect_boot_provider_overrides_from_env_var_name() -> None:
    prompts = iter(["5", "2", "MY_GEMINI_TOKEN"])

    overrides = _collect_boot_provider_overrides(
        env={"MY_GEMINI_TOKEN": "gemini-secret"},
        input_fn=lambda _prompt: next(prompts),
        interactive=True,
    )

    assert overrides == {
        "PRIMARY_PROVIDER": "gemini",
        "GEMINI_API_KEY": "gemini-secret",
    }


def test_read_prompt_line_accepts_long_pasted_tokens() -> None:
    token = "\x1b[200~" + ("tok_" * 100) + "\x1b[201~\n"
    stdout = io.StringIO()

    value = _read_prompt_line(
        "OpenAI Codex OAuth token: ",
        stdin=io.StringIO(token),
        stdout=stdout,
    )

    assert value == "tok_" * 100
    assert stdout.getvalue() == "OpenAI Codex OAuth token: "


def test_runtime_tool_item_converts_to_tool_result_message() -> None:
    message = _runtime_item_to_sentinel_message(
        ConversationItem(
            id="tool-1",
            role="tool",
            content=[
                ToolResultBlock(
                    tool_call_id="call_123",
                    tool_name="run_command",
                    content='{"stdout":"ok"}',
                )
            ],
        )
    )

    assert message.tool_call_id == "call_123"
    assert message.tool_name == "run_command"
    assert message.content == '{"stdout":"ok"}'


def test_runtime_system_item_converts_to_system_message() -> None:
    message = _runtime_item_to_sentinel_message(
        ConversationItem(
            id="system-1",
            role="system",
            content=[TextBlock(text="system rules")],
        )
    )

    assert isinstance(message, SystemMessage)
    assert message.content == "system rules"


def test_format_tool_arguments_renders_useful_preview() -> None:
    preview = _format_tool_arguments(
        {
            "command": "echo hi",
            "path": "README.md",
        }
    )

    assert "command=echo hi" in preview
    assert "path=README.md" in preview


def test_preview_tool_result_content_prefers_stdout_then_content() -> None:
    assert _preview_tool_result_content({"stdout": "first line\nsecond line"}) == "first line"
    assert _preview_tool_result_content({"content": "hello\nworld"}) == "hello"


def test_compose_turn_system_prompt_includes_execution_and_workspace_context(tmp_path: Path) -> None:
    prompt = _compose_turn_system_prompt(
        base_prompt="Base prompt",
        workspace_root=tmp_path,
        cwd=tmp_path / "src",
    )

    assert "Do not end a turn with text like 'I'll do X next'" in prompt
    assert "Only finish with a text-only assistant turn when the task is actually complete" in prompt
    assert "include that progress update in the same assistant turn as the next tool call" in prompt
    assert "Do not say a file was written, generated, or updated unless a tool result confirmed it." in prompt
    assert f"Initial CLI directory: {tmp_path}" in prompt
    assert f"Current working directory: {tmp_path / 'src'}" in prompt


def test_format_runtime_trace_for_progress_and_done() -> None:
    assert _format_runtime_trace(
        AgentEvent(type="agent_progress", iteration=2, max_iterations=50)
    ) == "[iter 2/50]"
    assert _format_runtime_trace(
        AgentEvent(type="done", stop_reason="tool_use")
    ) == "[turn-stop] tool_use -> continuing"
    assert _format_runtime_trace(
        AgentEvent(type="done", stop_reason="stop")
    ) == "[turn-stop] stop -> ending"


def test_strip_runtime_system_items_removes_ephemeral_prompt_items() -> None:
    history = [
        ConversationItem(id="system-1", role="system", content=[]),
        ConversationItem(id="user-1", role="user", content=[]),
        ConversationItem(id="assistant-1", role="assistant", content=[]),
    ]

    stripped = _strip_runtime_system_items(history)

    assert [item.id for item in stripped] == ["user-1", "assistant-1"]


@pytest.mark.asyncio
async def test_cli_tier_provider_resolves_tier_name_without_backend_dependencies() -> None:
    provider = _FakeCliProvider("fake")
    tier_provider = _CliTierProvider(
        tiers={
            "normal": _CliTierConfig(
                primary=_CliTierModelConfig(
                    provider=provider,
                    model="tier-model",
                    reasoning_config=ReasoningConfig(),
                    temperature=0.4,
                ),
                fallbacks=(),
            )
        },
        default_tier="normal",
        max_retries=1,
    )

    response = await tier_provider.chat([], model="normal")

    assert response.model == "tier-model"
    assert provider.seen_models == ["tier-model"]


@pytest.mark.asyncio
async def test_cli_provider_adapter_forwards_tool_choice() -> None:
    provider = _FakeCliProvider("fake")
    adapter = CliProviderAdapter(provider)

    await adapter.chat(
        messages=[],
        tools=[],
        config=type(
            "Cfg",
            (),
            {"model": "normal", "temperature": 0.4, "tool_choice": "required"},
        )(),
    )

    assert provider.seen_tool_choices == ["required"]


class _FakeCliProvider:
    def __init__(self, name: str) -> None:
        self._name = name
        self.seen_models: list[str] = []
        self.seen_tool_choices: list[str | None] = []

    @property
    def name(self) -> str:
        return self._name

    async def chat(
        self,
        messages,
        model: str,
        tools=None,
        temperature: float = 0.7,
        reasoning_config=None,
        tool_choice=None,
    ) -> AssistantMessage:
        self.seen_models.append(model)
        self.seen_tool_choices.append(tool_choice)
        return AssistantMessage(
            content=[TextContent(text="ok")],
            model=model,
            provider=self._name,
            usage=TokenUsage(),
            stop_reason="stop",
        )

    async def stream(self, *args, **kwargs):
        raise NotImplementedError
