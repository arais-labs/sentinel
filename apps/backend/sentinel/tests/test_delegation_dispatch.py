import asyncio

from app.models import Session
from sentral import ConversationItem, GenerationConfig, RunTurnRequest, TextBlock
from app.services.agent_runtime_adapters.runtime import SentinelLoopRuntimeAdapter
from sentral.llm.generic.types import AssistantMessage, TextContent, ToolCallContent
from app.services.tools import ToolDefinition, ToolRegistry
from tests.fake_db import FakeDB
from tests.test_sub_agent_orchestrator import _SequenceProvider, _build_base_runtime_support


def test_delegation_executes_without_hidden_planner_or_stale_workspace_veto():
    async def check():
        db = FakeDB()
        session = Session(user_id="local", title="delegation", status="active")
        db.add(session)
        calls = []
        registry = ToolRegistry()

        async def spawn(payload, runtime):
            calls.append(payload)
            return {"task_id": "child", "status": "pending"}

        registry.register(
            ToolDefinition(
                name="delegate",
                description="Delegate work",
                parameters_schema={"type": "object", "additionalProperties": True},
                execute=spawn,
            )
        )

        class Provider(_SequenceProvider):
            planner_calls = 0

            async def chat(
                self,
                messages,
                model,
                tools=None,
                temperature=0.7,
                reasoning_config=None,
                tool_choice=None,
            ):
                if tool_choice == "none":
                    self.planner_calls += 1
                    return AssistantMessage(
                        content=[
                            TextContent(
                                text='{"spawn_decisions":[{"tool_call_id":"spawn","allow":false,"reason":"Earlier workspace check was unattached"}]}'
                            )
                        ]
                    )
                return await super().chat(
                    messages, model, tools, temperature, reasoning_config, tool_choice
                )

        provider = Provider(
            [
                AssistantMessage(
                    content=[
                        ToolCallContent(
                            id="spawn",
                            name="delegate",
                            arguments={
                                "action": "spawn",
                                "objective": "Create and verify a temporary file",
                            },
                        )
                    ],
                    stop_reason="tool_use",
                ),
                AssistantMessage(content=[TextContent(text="Delegated")]),
            ]
        )
        adapter = SentinelLoopRuntimeAdapter(
            loop=_build_base_runtime_support(provider, registry),
            db=db,
            session_id=session.id,
            persist_incremental=True,
        )
        result = await adapter.run_turn(
            RunTurnRequest(
                conversation_id=str(session.id),
                new_items=[
                    ConversationItem(
                        id="user",
                        role="user",
                        content=[
                            TextBlock(
                                text="I have attached the workspace. Delegate the file task now."
                            )
                        ],
                    )
                ],
                config=GenerationConfig(model="hard", max_iterations=0, stream=False),
            )
        )
        assert result.status == "completed"
        assert provider.planner_calls == 0
        assert provider.calls == 2
        assert len(calls) == 1
        assert calls[0]["action"] == "spawn"

    asyncio.run(check())
