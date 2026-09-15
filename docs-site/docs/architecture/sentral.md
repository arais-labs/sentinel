---
title: Sentral runtime
---

# Sentral runtime

Sentral is the reusable agent engine shared by Sentinel and the standalone TUI.
Its source lives in `packages/sentral/src/sentral`. The backend and TUI depend on
this Python package, which owns the engine, provider adapters, and shared tools.
It has no dependency on Sentinel's database, settings, or application startup.

## Boundaries

| Sentral | Sentinel application | Standalone TUI |
| --- | --- | --- |
| Turn loop and streaming events | Instances, sessions, database history | Terminal layout and keyboard controls |
| Provider and tool contracts | Module/tool adapters and permissions | Local approval decisions |
| Conversation store interface | Persisted context and compaction integration | In-memory conversation |
| Approval outcomes and cancellation | Workspace, Telegram, HTTP/WebSocket routing | App-owned local processes |

`AgentRuntimeEngine` accepts a turn request and emits typed events through an event
sink. Providers produce text, reasoning, tool calls, and usage; tools return
results through the runtime's tool contract. Applications supply persistence,
configuration, and permission decisions.

Sentinel's integration lives in `app/services/agent_runtime_adapters`. The TUI
imports the shared engine and existing `host_runtime`/`http_request` implementations;
backend code does not import the TUI. Neither a terminal chat nor a provider
adapter should start the web application just to use the agent engine.

See [Agent loop](../concepts/agent-loop.md) for behavior and
[Standalone TUI](../guides/standalone-tui.md) for usage.
