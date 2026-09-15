---
title: Agent loop
---

# Agent loop

Sentinel uses Sentral to run a conversation's turn. Each instance supplies its
provider settings, tools, permissions, persistence, and routing; the shared engine
does not own those application concerns.

## One turn

```text
Build context → call provider → execute permitted tools → add results → repeat
                                  ↓
                         approval or user intervention
```

The turn finishes when the model answers without more tool calls, a configured
limit is reached, execution fails, or the operator stops it. Results and streaming
events let the UI display text, tool progress, approvals, and usage as work proceeds.

## Step settings

The default is **Auto steps** (`max_iterations = 0`). A turn can instead use an
explicit iteration budget from Run settings. One iteration is a provider call
and its tool calls, not a fixed amount of wall-clock time.

For an explicit budget, the engine can grant a bounded grace extension after an
additional model check. Once that budget is exhausted, it requests a final answer
without tools. This wrap-up still depends on the provider responding successfully.
Auto steps does not remove cancellation, provider errors, or configured timeouts.

## Steering and stopping

Send another message during a turn to steer it. The runtime consumes queued
instructions at execution boundaries; it cannot instantly interrupt every running
tool. **Stop** cancels the turn. A command already launched in a workspace may have
its own lifetime, so inspect the terminal/process state before retrying work.

Delegation creates a child conversation with its own history. The supervisor can
inspect, message, or explicitly resume eligible tasks. Child agents share the
selected workspace, not the parent's session-scoped permission grants.

## Approvals

Tool-level approval gates wait for the operator before executing. Module HTTP
actions can return **202 Pending** with an approval record. A policy denial is
**403 Denied** and cannot be bypassed by a session grant. The exact continuation
path depends on the tool; a pending approval is not a failed action to retry.

See [Approvals](./approvals.md) for decisions and session scope.

## Context and persistence

Sentinel builds model context from policies, memory, conversation history, and any
compaction summary. Compaction advances the context boundary while retaining the
full transcript. See [Sessions](./sessions.md).

Streaming paths checkpoint messages and tool results as they are produced.
Interrupted work may therefore leave a partial tool exchange in history; a final
response alone is not proof that all prior operations succeeded. Screenshot tools
supply image attachments subject to the configured image count and byte limits.
Use browser text or accessibility snapshots when visual input is unavailable.

## Diagnosing failures

Check the error attached to the failed provider or tool call. Authentication,
rate limits, context limits, unavailable workspaces, and provider service errors
require different remedies. “All providers failed” does not by itself mean the
API key is wrong. Provider connections belong to the selected instance.

The runtime architecture and application boundary are described in
[Sentral runtime](../architecture/sentral.md).
