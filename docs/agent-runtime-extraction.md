# Standalone Agent Runtime Extraction

## Goal

Extract Sentinel's agent execution stack into a standalone reusable runtime package that can be used by:

- Sentinel WebSocket/UI
- Telegram bridge
- Trigger scheduler
- other applications outside Sentinel

The extracted runtime must preserve current Sentinel behavior while removing Sentinel-specific concerns from the core loop.

## Non-Goals

The standalone runtime package must **not** own:

- database persistence
- Sentinel sessions
- websocket transport
- Telegram transport
- auth
- sub-agent orchestration
- auto-rename
- app routing

Those remain application concerns.

## Package Boundary

The extracted package owns:

- provider interface
- message/content model
- streaming event model
- tool registry interface
- approval-aware tool execution
- conversation history handling
- compaction API
- iterative agent loop

The application owns:

- persistence and history storage implementation
- transport adapters
- approval UI / storage
- session naming
- app-specific concurrency policy

## Core Principles

1. The runtime is transport-agnostic.
2. The runtime is persistence-agnostic.
3. Streaming events are the canonical API.
4. Tool approvals are first-class runtime outcomes.
5. Compaction is a runtime concept, but it is explicit, not implicit.
6. Sentinel behavior must remain unchanged behind adapters during migration.

## Runtime Model

### Canonical Entry Point

The primary API is a single-turn execution primitive:

```python
result = await runtime.run_turn(request, sink=sink)
```

Where:

- `request` contains history, new input, tool registry, provider config, and runtime policies
- `sink` receives streaming events
- `result` contains final outcome metadata and the updated conversation state

### Convenience Wrapper

The package may also provide:

```python
session = AgentSession(runtime=runtime, store=store, sink=sink)
result = await session.run_turn(input)
```

But `AgentSession` is convenience only. `run_turn(...)` is canonical.

## Conversation Ownership

The runtime must understand conversation history, but it must not require Sentinel persistence.

So the package defines an async conversation store interface and also supports direct history injection.

### Required Capability

The runtime must be able to work in both modes:

1. Caller supplies prior history directly.
2. Caller supplies a `ConversationStore` implementation.

### Store Interface

```python
class ConversationStore(Protocol):
    async def load_history(self, conversation_id: str) -> list[ConversationItem]: ...
    async def append_items(self, conversation_id: str, items: list[ConversationItem]) -> None: ...
    async def replace_history(self, conversation_id: str, items: list[ConversationItem]) -> None: ...
```

The package should also ship an in-memory store implementation.

## Core Interfaces

### Provider

The provider interface belongs to the extracted package.

```python
class Provider(Protocol):
    async def chat(
        self,
        *,
        messages: list[ConversationItem],
        tools: list[ToolSchema],
        config: GenerationConfig,
    ) -> AssistantTurn: ...

    async def stream(
        self,
        *,
        messages: list[ConversationItem],
        tools: list[ToolSchema],
        config: GenerationConfig,
    ) -> AsyncIterator[ProviderEvent]: ...
```

Provider selection is application-owned. Provider protocol is package-owned.

### Tool Registry

```python
class ToolRegistry(Protocol):
    def list_tools(self) -> list[ToolDefinition]: ...
    def get_tool(self, name: str) -> ToolDefinition | None: ...
```

### Tool Definition

```python
class ToolDefinition(TypedDict):
    name: str
    description: str
    parameters_schema: dict[str, Any]
    approval_mode: Literal["allow", "approval", "deny"]
    execute: ToolExecutor
```

### Tool Executor Outcome

Tool execution must support approval as a first-class result.

```python
class ToolExecutionResult(TypedDict, total=False):
    status: Literal["ok", "error", "pending_approval"]
    content: Any
    error: str
    approval_request: ApprovalRequest
```

## Approval Contract

Approvals are part of the runtime contract.

Decision:

- the loop does **not** suspend internally waiting for approval
- the loop stops with a terminal `pending_approval` outcome
- the caller resumes later with updated history or an explicit approval resolution message

This keeps the runtime transport-agnostic and restartable.

### Approval Types

```python
class ApprovalRequest(TypedDict):
    id: str
    tool_name: str
    action: str
    description: str
    payload: dict[str, Any]
```

## Compaction Contract

Compaction remains a runtime concept, but it is **explicit**.

Decision:

- no automatic hidden compaction inside `run_turn(...)`
- the runtime exposes compaction as a separate API
- the runtime may emit a signal that compaction is recommended or required
- the caller decides when to compact and whether to persist compacted history

### Compactor Interface

```python
class Compactor(Protocol):
    async def compact(
        self,
        *,
        history: list[ConversationItem],
        config: CompactionConfig,
    ) -> CompactionResult: ...
```

### Compaction API

```python
result = await runtime.compact(history=history, config=config)
```

### Compaction Result

```python
class CompactionResult(TypedDict):
    history: list[ConversationItem]
    raw_token_count: int
    compacted_token_count: int
    summary_preview: str
```

## Canonical Message Model

The package must not depend on Sentinel ORM models.

### Conversation Items

```python
class ConversationItem(TypedDict):
    id: str
    role: Literal["system", "user", "assistant", "tool"]
    content: list[ContentBlock]
    metadata: dict[str, Any]
```

### Content Blocks

```python
TextBlock
ImageBlock
ThinkingBlock
ToolCallBlock
ToolResultBlock
```

The package owns these types.

## Streaming Event Model

Streaming events are the canonical transport-neutral surface.

### Event Types

```python
class RuntimeEvent(TypedDict, total=False):
    type: Literal[
        "turn_started",
        "thinking_start",
        "thinking_delta",
        "thinking_end",
        "toolcall_start",
        "toolcall_delta",
        "toolcall_end",
        "tool_result",
        "approval_requested",
        "agent_progress",
        "text_start",
        "text_delta",
        "text_end",
        "compaction_required",
        "done",
        "error",
    ]
    turn_id: str
    message_id: str
    tool_name: str
    approval_request: ApprovalRequest
    delta: str
    content: Any
    stop_reason: str
    error: str
```

### Sink Interface

```python
class EventSink(Protocol):
    async def emit(self, event: RuntimeEvent) -> None: ...
```

WebSocket, Telegram, triggers, and tests should all be adapters around this event stream.

## Canonical Turn API

```python
class RunTurnRequest(TypedDict, total=False):
    conversation_id: str
    history: list[ConversationItem]
    new_input: list[ContentBlock]
    system_prompt: str | None
    tools: ToolRegistry
    provider: Provider
    generation: GenerationConfig
    store: ConversationStore | None
    compactor: Compactor | None
```

Rules:

- caller must provide either `history` or `store + conversation_id`
- runtime may load history from store if history is omitted
- runtime may return updated history regardless of store usage

### Turn Result

```python
class TurnResult(TypedDict, total=False):
    status: Literal["completed", "pending_approval", "error", "cancelled"]
    final_text: str
    history: list[ConversationItem]
    usage: TokenUsage
    approval_request: ApprovalRequest
    error: str
    stop_reason: str
```

## What Moves Out of Sentinel

These should eventually move behind adapters:

- [loop.py](/Users/alexandresfez/PycharmProjects/ARAIS/sentinel/apps/backend/sentinel/app/services/agent/loop.py)
- [tool_adapter.py](/Users/alexandresfez/PycharmProjects/ARAIS/sentinel/apps/backend/sentinel/app/services/agent/tool_adapter.py)
- [context_builder.py](/Users/alexandresfez/PycharmProjects/ARAIS/sentinel/apps/backend/sentinel/app/services/agent/context_builder.py)
- generic provider interfaces under [llm/generic](/Users/alexandresfez/PycharmProjects/ARAIS/sentinel/apps/backend/sentinel/app/services/llm/generic)

These stay in Sentinel and become consumers/adapters:

- [ws.py](/Users/alexandresfez/PycharmProjects/ARAIS/sentinel/apps/backend/sentinel/app/routers/ws.py)
- [ws_stream_service.py](/Users/alexandresfez/PycharmProjects/ARAIS/sentinel/apps/backend/sentinel/app/services/ws/ws_stream_service.py)
- [bridge.py](/Users/alexandresfez/PycharmProjects/ARAIS/sentinel/apps/backend/sentinel/app/services/telegram/bridge.py)
- [trigger_scheduler.py](/Users/alexandresfez/PycharmProjects/ARAIS/sentinel/apps/backend/sentinel/app/services/triggers/trigger_scheduler.py)
- [service.py](/Users/alexandresfez/PycharmProjects/ARAIS/sentinel/apps/backend/sentinel/app/services/sessions/service.py)

## Biggest Current Extraction Blockers

1. `ws_stream_service` mixes:
   - persistence
   - event broadcasting
   - run registry
   - loop execution
   - compaction follow-up

2. Telegram bridge owns its own run orchestration around the loop.

3. Trigger scheduler owns a third execution flow.

4. `AgentLoop` still mixes:
   - core reasoning/tool iteration
   - persistence
   - some Sentinel-specific message shaping

## Migration Plan

### Phase 1

Define the standalone contracts inside Sentinel first.

Deliverables:

- runtime-neutral message model
- runtime-neutral event model
- provider/tool/store/compactor protocols

### Phase 2

Adapt current `AgentLoop` to those interfaces without changing Sentinel behavior.

Goal:

- Sentinel still works exactly the same
- internals now conform to package contracts

### Phase 3

Extract a shared runner/orchestrator used by:

- WS
- Telegram
- Triggers

This runner should consume:

- runtime contracts
- event sink
- app-level persistence hooks

### Phase 4

Physically move the reusable runtime pieces into a standalone package.

### Phase 5

Make Sentinel consumers thin adapters around the extracted package.

## Sentinel Invariants

During extraction, Sentinel behavior must stay unchanged:

- same approval behavior
- same streaming behavior
- same persistence semantics
- same auto-rename behavior outside the runtime package
- same trigger and Telegram behavior from the user point of view

If a choice threatens these invariants, keep the compatibility adapter in Sentinel and move the complexity out later.
