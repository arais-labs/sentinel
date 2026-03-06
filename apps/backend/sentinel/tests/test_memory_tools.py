from __future__ import annotations

import asyncio

import pytest

from app.models import Memory, Message, Session
from app.services.agent import AgentLoop, ContextBuilder, ToolAdapter
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import AgentEvent, AssistantMessage, TextContent, ToolCallContent, TokenUsage
from app.services.memory.search import MemorySearchResult, MemorySearchService
from app.services.tools import ToolExecutor
from app.services.tools.executor import ToolValidationError
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


class _TrackingMemoryService:
    def __init__(self, db: FakeDB):
        self._db = db
        self.calls = 0

    async def list_all_memories(self, db):
        _ = db
        self.calls += 1
        return list(self._db.storage[Memory])


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


def test_memory_tree_tool_returns_nested_structure():
    memory_db = FakeDB()
    session_factory = _SessionFactory(memory_db)
    registry = build_default_registry(
        memory_search_service=_StaticMemorySearch(memory_db),
        embedding_service=_FakeEmbedding(),
        session_factory=session_factory,
    )
    executor = ToolExecutor(registry)

    root_a = Memory(
        title="Root A",
        summary="A root",
        content="Root A content",
        category="project",
        metadata_json={},
        pinned=True,
        importance=90,
    )
    root_b = Memory(
        title="Root B",
        summary="B root",
        content="Root B content",
        category="project",
        metadata_json={},
        pinned=False,
        importance=20,
    )
    memory_db.add(root_a)
    memory_db.add(root_b)
    child_a = Memory(
        title="Child A",
        summary="A child",
        content="Child A content",
        category="project",
        parent_id=root_a.id,
        metadata_json={},
    )
    memory_db.add(child_a)
    grandchild_a = Memory(
        title="Grandchild A",
        summary="A grandchild",
        content="Grandchild A content",
        category="project",
        parent_id=child_a.id,
        metadata_json={},
    )
    memory_db.add(grandchild_a)

    tree, _ = _run(
        executor.execute(
            "memory_tree",
            {"max_depth": 5},
            allow_high_risk=True,
        )
    )

    assert tree["total_roots"] == 2
    assert tree["truncated"] is False
    assert tree["roots"][0]["id"] == str(root_a.id)
    assert "content" not in tree["roots"][0]
    assert tree["roots"][0]["children"][0]["id"] == str(child_a.id)
    assert tree["roots"][0]["children"][0]["children"][0]["id"] == str(grandchild_a.id)


def test_memory_tree_tool_respects_depth_limit_and_include_content():
    memory_db = FakeDB()
    session_factory = _SessionFactory(memory_db)
    registry = build_default_registry(
        memory_search_service=_StaticMemorySearch(memory_db),
        embedding_service=_FakeEmbedding(),
        session_factory=session_factory,
    )
    executor = ToolExecutor(registry)

    root = Memory(
        title="Root",
        summary="Root summary",
        content="Root content",
        category="project",
        metadata_json={},
    )
    memory_db.add(root)
    child = Memory(
        title="Child",
        summary="Child summary",
        content="Child content",
        category="project",
        parent_id=root.id,
        metadata_json={},
    )
    memory_db.add(child)
    grandchild = Memory(
        title="Grandchild",
        summary="Grandchild summary",
        content="Grandchild content",
        category="project",
        parent_id=child.id,
        metadata_json={},
    )
    memory_db.add(grandchild)

    tree, _ = _run(
        executor.execute(
            "memory_tree",
            {"root_id": str(root.id), "max_depth": 1, "include_content": True},
            allow_high_risk=True,
        )
    )

    assert tree["total_roots"] == 1
    assert tree["truncated"] is True
    root_node = tree["roots"][0]
    assert root_node["content"] == "Root content"
    child_node = root_node["children"][0]
    assert child_node["has_more_children"] is True
    assert child_node["children"] == []


def test_memory_delete_tool_deletes_subtree():
    memory_db = FakeDB()
    session_factory = _SessionFactory(memory_db)
    registry = build_default_registry(
        memory_search_service=_StaticMemorySearch(memory_db),
        embedding_service=_FakeEmbedding(),
        session_factory=session_factory,
    )
    executor = ToolExecutor(registry)

    root = Memory(
        title="Delete root",
        content="Root",
        category="project",
        metadata_json={},
    )
    memory_db.add(root)
    child = Memory(
        title="Delete child",
        content="Child",
        category="project",
        parent_id=root.id,
        metadata_json={},
    )
    memory_db.add(child)
    grandchild = Memory(
        title="Delete grandchild",
        content="Grandchild",
        category="project",
        parent_id=child.id,
        metadata_json={},
    )
    memory_db.add(grandchild)
    survivor = Memory(
        title="Keep me",
        content="Survivor",
        category="project",
        metadata_json={},
    )
    memory_db.add(survivor)

    deleted, _ = _run(
        executor.execute(
            "memory_delete",
            {"id": str(root.id)},
            allow_high_risk=True,
        )
    )
    assert deleted["deleted"] is True
    assert deleted["id"] == str(root.id)

    remaining_ids = {str(item.id) for item in memory_db.storage[Memory]}
    assert str(root.id) not in remaining_ids
    assert str(child.id) not in remaining_ids
    assert str(grandchild.id) not in remaining_ids
    assert str(survivor.id) in remaining_ids


def test_memory_delete_tool_rejects_invalid_or_unknown_id():
    memory_db = FakeDB()
    session_factory = _SessionFactory(memory_db)
    registry = build_default_registry(
        memory_search_service=_StaticMemorySearch(memory_db),
        embedding_service=_FakeEmbedding(),
        session_factory=session_factory,
    )
    executor = ToolExecutor(registry)

    with pytest.raises(ToolValidationError, match="Field 'id' must be a valid UUID string"):
        _run(
            executor.execute(
                "memory_delete",
                {"id": "not-a-uuid"},
                allow_high_risk=True,
            )
        )

    with pytest.raises(ToolValidationError, match="Memory node not found"):
        _run(
            executor.execute(
                "memory_delete",
                {"id": "7f07395b-9e02-41cd-9952-65792509f7e4"},
                allow_high_risk=True,
            )
        )


def test_memory_move_tool_moves_subtree_to_another_root():
    memory_db = FakeDB()
    session_factory = _SessionFactory(memory_db)
    registry = build_default_registry(
        memory_search_service=_StaticMemorySearch(memory_db),
        embedding_service=_FakeEmbedding(),
        session_factory=session_factory,
    )
    executor = ToolExecutor(registry)

    root_a = Memory(title="Root A", content="A", category="project", metadata_json={})
    root_b = Memory(title="Root B", content="B", category="project", metadata_json={})
    memory_db.add(root_a)
    memory_db.add(root_b)

    child = Memory(
        title="Child",
        content="child",
        category="project",
        parent_id=root_a.id,
        metadata_json={},
    )
    memory_db.add(child)
    grandchild = Memory(
        title="Grandchild",
        content="grandchild",
        category="project",
        parent_id=child.id,
        metadata_json={},
    )
    memory_db.add(grandchild)

    moved, _ = _run(
        executor.execute(
            "memory_move",
            {"node_ids": [str(child.id)], "target_parent_id": str(root_b.id)},
            allow_high_risk=True,
        )
    )

    assert moved["moved_count"] == 1
    assert moved["target_parent_id"] == str(root_b.id)
    assert child.parent_id == root_b.id
    assert grandchild.parent_id == child.id


def test_memory_move_tool_rejects_cycle_or_conflicting_nodes():
    memory_db = FakeDB()
    session_factory = _SessionFactory(memory_db)
    registry = build_default_registry(
        memory_search_service=_StaticMemorySearch(memory_db),
        embedding_service=_FakeEmbedding(),
        session_factory=session_factory,
    )
    executor = ToolExecutor(registry)

    root = Memory(title="Root", content="root", category="project", metadata_json={})
    memory_db.add(root)
    child = Memory(
        title="Child",
        content="child",
        category="project",
        parent_id=root.id,
        metadata_json={},
    )
    memory_db.add(child)

    with pytest.raises(ToolValidationError, match="own descendant"):
        _run(
            executor.execute(
                "memory_move",
                {"node_ids": [str(root.id)], "target_parent_id": str(child.id)},
                allow_high_risk=True,
            )
        )

    with pytest.raises(ToolValidationError, match="ancestor and its descendant"):
        _run(
            executor.execute(
                "memory_move",
                {"node_ids": [str(root.id), str(child.id)], "to_root": True},
                allow_high_risk=True,
            )
        )


def test_memory_move_tool_rejects_system_memory_nodes():
    memory_db = FakeDB()
    session_factory = _SessionFactory(memory_db)
    registry = build_default_registry(
        memory_search_service=_StaticMemorySearch(memory_db),
        embedding_service=_FakeEmbedding(),
        session_factory=session_factory,
    )
    executor = ToolExecutor(registry)

    system_root = Memory(
        title="Agent Identity",
        content="You are Sentinel.",
        category="core",
        pinned=True,
        is_system=True,
        system_key="agent_identity",
        metadata_json={},
    )
    other_root = Memory(
        title="Workspace",
        content="General workspace notes",
        category="project",
        metadata_json={},
    )
    memory_db.add(system_root)
    memory_db.add(other_root)

    with pytest.raises(ToolValidationError, match="protected system memory"):
        _run(
            executor.execute(
                "memory_move",
                {
                    "node_ids": [str(system_root.id)],
                    "target_parent_id": str(other_root.id),
                },
                allow_high_risk=True,
            )
        )


def test_memory_move_tool_rejects_target_parent_that_is_system_memory():
    memory_db = FakeDB()
    session_factory = _SessionFactory(memory_db)
    registry = build_default_registry(
        memory_search_service=_StaticMemorySearch(memory_db),
        embedding_service=_FakeEmbedding(),
        session_factory=session_factory,
    )
    executor = ToolExecutor(registry)

    system_root = Memory(
        title="Agent Identity",
        content="You are Sentinel.",
        category="core",
        pinned=True,
        is_system=True,
        system_key="agent_identity",
        metadata_json={},
    )
    other_root = Memory(
        title="Workspace",
        content="General workspace notes",
        category="project",
        metadata_json={},
    )
    memory_db.add(system_root)
    memory_db.add(other_root)

    with pytest.raises(ToolValidationError, match="under a protected system memory"):
        _run(
            executor.execute(
                "memory_move",
                {
                    "node_ids": [str(other_root.id)],
                    "target_parent_id": str(system_root.id),
                },
                allow_high_risk=True,
            )
        )


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

    memory_policy_block = next(msg for msg in system_messages if "## Hierarchical Memory Policy" in msg)
    assert "Pinned memories are high-priority anchors" in memory_policy_block
    assert "ask whether the user wants memory reorganization" in memory_policy_block

    relevant_block = next(msg for msg in system_messages if "Potentially Relevant Memory Branches" in msg)
    assert str(child.id) in relevant_block or str(root_a.id) in relevant_block


def test_context_builder_memory_layer_uses_injected_memory_service():
    db = FakeDB()
    root = Memory(
        title="Project Alpha",
        summary="Top-level alpha summary",
        content="Alpha root content",
        category="project",
        metadata_json={},
        pinned=True,
        importance=90,
    )
    db.add(root)
    session = Session(user_id="dev-admin", status="active", title="ctx-service")
    db.add(session)
    tracking_service = _TrackingMemoryService(db)

    builder = ContextBuilder(
        default_system_prompt="sys",
        memory_service=tracking_service,
    )
    messages = _run(builder.build(db, session.id))

    assert tracking_service.calls == 1
    system_messages = [m.content for m in messages if getattr(m, "role", "") == "system"]
    assert any("## Memory (pinned): Project Alpha" in msg for msg in system_messages)


def test_context_builder_skips_runtime_context_system_history_rows():
    db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="ctx-runtime")
    db.add(session)
    db.add(
        Message(
            session_id=session.id,
            role="system",
            content="[Runtime Context Snapshot] model=hint:normal tools=5 system_blocks=12",
            metadata_json={"source": "runtime_context", "run_context": {"model": "hint:normal"}},
        )
    )
    db.add(
        Message(
            session_id=session.id,
            role="user",
            content="hello",
            metadata_json={},
        )
    )
    db.add(
        Message(
            session_id=session.id,
            role="assistant",
            content="world",
            metadata_json={"stop_reason": "stop"},
        )
    )

    builder = ContextBuilder(default_system_prompt="sys")
    messages = _run(builder.build(db, session.id, pending_user_message="next"))

    system_contents = [m.content for m in messages if getattr(m, "role", "") == "system"]
    assert all("Runtime Context Snapshot" not in content for content in system_contents)

    history_user = [m for m in messages if getattr(m, "role", "") == "user"]
    history_assistant = [m for m in messages if getattr(m, "role", "") == "assistant"]
    assert any(
        any(getattr(block, "text", "") == "hello" for block in getattr(msg, "content", []))
        for msg in history_user
    )
    assert any(
        any(getattr(block, "text", "") == "world" for block in getattr(msg, "content", []))
        for msg in history_assistant
    )


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


def test_context_builder_keeps_only_matching_tool_results_for_tool_use_turn():
    db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="matching")
    db.add(session)
    db.add(
        Message(
            session_id=session.id,
            role="assistant",
            content="Using tool",
            metadata_json={
                "tool_calls": [
                    {"id": "toolu_a", "name": "memory_search", "arguments": {"query": "alpha"}},
                    {"id": "toolu_b", "name": "memory_search", "arguments": {"query": "beta"}},
                ]
            },
        )
    )
    db.add(
        Message(
            session_id=session.id,
            role="tool_result",
            content='{"ok": true, "id": "a"}',
            tool_call_id="toolu_a",
            tool_name="memory_search",
            metadata_json={},
        )
    )
    db.add(
        Message(
            session_id=session.id,
            role="tool_result",
            content='{"ok": true, "id": "z"}',
            tool_call_id="toolu_z",
            tool_name="memory_search",
            metadata_json={},
        )
    )
    db.add(
        Message(
            session_id=session.id,
            role="tool_result",
            content='{"ok": true, "id": "b"}',
            tool_call_id="toolu_b",
            tool_name="memory_search",
            metadata_json={},
        )
    )

    builder = ContextBuilder(default_system_prompt="sys")
    context = _run(builder.build(db, session.id))
    tool_result_ids = [
        getattr(item, "tool_call_id", "")
        for item in context
        if getattr(item, "role", "") == "tool_result"
    ]
    assert tool_result_ids == ["toolu_a", "toolu_b"]


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
