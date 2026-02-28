from __future__ import annotations

import asyncio

from app.models import Memory, Message, Session
from app.services.agent import AgentLoop, ContextBuilder, ToolAdapter
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import AgentEvent, AssistantMessage, TextContent, ToolCallContent, TokenUsage
from app.services.memory.search import MemorySearchResult, MemorySearchService
from app.services.tools import ToolExecutor
from app.services.tools.builtin import build_default_registry
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


class _FakeEmbedding:
    async def embed(self, text: str) -> list[float]:
        return [0.3, 0.7]


class _SessionCtx:
    def __init__(self, db: FakeDB):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SessionFactory:
    def __init__(self, db: FakeDB):
        self._db = db

    def __call__(self):
        return _SessionCtx(self._db)


class _StaticMemorySearch(MemorySearchService):
    def __init__(self, db: FakeDB):
        super().__init__(embedding_service=None)
        self._db = db

    async def search(self, db, query: str, *, category: str | None = None, limit: int = 10):
        _ = db
        rows = self._db.storage[Memory]
        return [MemorySearchResult(memory=item, score=1.0) for item in rows[:limit]]


class _SequenceProvider(LLMProvider):
    def __init__(self, responses: list[AssistantMessage]) -> None:
        self._responses = responses
        self.calls = 0

    @property
    def name(self) -> str:
        return "seq"

    async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None):
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]

    async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None):
        if False:
            yield AgentEvent(type="done", stop_reason="stop")
        return


def test_memory_store_and_search_tools_execute():
    memory_db = FakeDB()
    session_factory = _SessionFactory(memory_db)
    registry = build_default_registry(
        memory_search_service=_StaticMemorySearch(memory_db),
        embedding_service=_FakeEmbedding(),
        session_factory=session_factory,
    )
    executor = ToolExecutor(registry)

    stored, _ = _run(
        executor.execute(
            "memory_store",
            {"content": "Store this memory", "category": "project", "metadata": {}},
            allow_high_risk=True,
        )
    )
    assert stored["embedded"] is True
    assert len(memory_db.storage[Memory]) == 1

    searched, _ = _run(
        executor.execute(
            "memory_search",
            {"query": "store", "limit": 5},
            allow_high_risk=True,
        )
    )
    assert searched["total"] == 1
    assert searched["items"][0]["content"] == "Store this memory"


def test_agent_loop_can_call_memory_search_tool():
    memory_db = FakeDB()
    memory_db.add(Memory(content="Remember alpha", category="project", metadata_json={}))

    registry = build_default_registry(
        memory_search_service=_StaticMemorySearch(memory_db),
        embedding_service=_FakeEmbedding(),
        session_factory=_SessionFactory(memory_db),
    )
    provider = _SequenceProvider(
        [
            AssistantMessage(
                content=[ToolCallContent(id="call_mem", name="memory_search", arguments={"query": "alpha"})],
                model="m",
                provider="p",
                usage=TokenUsage(),
                stop_reason="tool_use",
            ),
            AssistantMessage(
                content=[TextContent(text="Used memory")],
                model="m",
                provider="p",
                usage=TokenUsage(),
                stop_reason="stop",
            ),
        ]
    )

    loop_db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="loop")
    loop_db.add(session)

    loop = AgentLoop(provider, ContextBuilder(default_system_prompt="sys"), ToolAdapter(registry, ToolExecutor(registry)))
    result = _run(loop.run(loop_db, session.id, "find alpha", stream=False))

    assert result.final_text == "Used memory"
    rows = [m for m in loop_db.storage[Message] if m.session_id == session.id]
    tool_row = next(row for row in rows if row.role == "tool_result")
    assert tool_row.tool_name == "memory_search"
    assert "Remember alpha" in tool_row.content


def test_context_builder_injects_all_root_memories_and_auto_branches():
    db = FakeDB()
    root_a = Memory(
        title="Project Alpha",
        summary="Top-level alpha summary",
        content="Alpha root content",
        category="project",
        metadata_json={},
        pinned=True,
        importance=90,
    )
    root_b = Memory(
        title="Project Beta",
        summary="Top-level beta summary",
        content="Beta root content",
        category="project",
        metadata_json={},
        pinned=False,
        importance=40,
    )
    db.add(root_a)
    db.add(root_b)
    child = Memory(
        title="Alpha detail",
        summary="Specific alpha detail",
        content="This child has the alpha implementation notes",
        category="project",
        parent_id=root_a.id,
        metadata_json={},
    )
    db.add(child)
    session = Session(user_id="dev-admin", status="active", title="ctx")
    db.add(session)

    builder = ContextBuilder(
        default_system_prompt="sys",
        memory_search_service=_StaticMemorySearch(db),
    )
    messages = _run(builder.build(db, session.id, pending_user_message="alpha notes"))
    system_messages = [m.content for m in messages if getattr(m, "role", "") == "system"]

    pinned_block = next(msg for msg in system_messages if "## Memory (pinned):" in msg)
    assert "Project Alpha" in pinned_block
    assert "Alpha root content" in pinned_block

    roots_block = next(msg for msg in system_messages if "## Non-Pinned Root Memories" in msg)
    assert str(root_b.id) in roots_block
    assert str(root_a.id) not in roots_block
    assert "Pinned memories are already fully injected above" in roots_block

    relevant_block = next(msg for msg in system_messages if "Potentially Relevant Memory Branches" in msg)
    assert str(child.id) in relevant_block or str(root_a.id) in relevant_block


def test_context_builder_strips_orphan_tool_use_for_anthropic_compat():
    db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="orphan")
    db.add(session)
    db.add(
        Message(
            session_id=session.id,
            role="assistant",
            content="Using tool",
            metadata_json={
                "tool_calls": [
                    {"id": "toolu_orphan", "name": "memory_search", "arguments": {"query": "alpha"}}
                ]
            },
        )
    )
    # Intentionally no following tool_result to emulate truncated history.
    db.add(
        Message(
            session_id=session.id,
            role="assistant",
            content="Next plain assistant message",
            metadata_json={},
        )
    )

    builder = ContextBuilder(default_system_prompt="sys")
    context = _run(builder.build(db, session.id))
    assistant_messages = [m for m in context if getattr(m, "role", "") == "assistant"]
    assert assistant_messages, "expected assistant messages in context"
    first = assistant_messages[0]
    tool_blocks = [block for block in first.content if isinstance(block, ToolCallContent)]
    assert tool_blocks == []


def test_context_builder_adds_delegation_policy_when_tools_available():
    db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="delegation")
    db.add(session)

    builder = ContextBuilder(
        default_system_prompt="sys",
        available_tools={"spawn_sub_agent", "check_sub_agent"},
    )
    context = _run(builder.build(db, session.id))
    system_messages = [m.content for m in context if getattr(m, "role", "") == "system"]

    delegation = next((msg for msg in system_messages if "## Delegation Policy" in msg), None)
    assert delegation is not None
    assert "bounded one-off tasks" in delegation
    assert "check_sub_agent" in delegation
