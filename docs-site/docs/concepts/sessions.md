---
sidebar_position: 9
title: Sessions
---

# Sessions

A session is a persistent conversation container for a running agent. It holds the message history, active turn state, and routing identity for one channel of communication.

Sessions are **per-instance**. Sentinel runs one deployment that hosts multiple logical [instances](../guides/multi-instance.md), and every session lives in the database of the instance it belongs to. A session created under `instance-a` is never visible to `instance-b`; there is no cross-instance session sharing.

---

## Session types

| Type | Description |
|---|---|
| **Main session** | The canonical root session for a user. Created automatically, one active per user. Default routing target for triggers and the primary workspace in the Sentinel UI. |
| **Named sessions** | User-created root sessions for isolated workstreams or projects. |
| **Sub-agent sessions** | Child sessions spawned by the [delegate tool](../concepts/agent-loop.md). They reference their creator via `parent_session_id` and run a single delegated task. |
| **Telegram DM / group sessions** | Sessions bound to a Telegram chat. See [Telegram routing](#telegram-session-routing). |

A **root** session is one with no `parent_session_id`. Main, named, and Telegram-bound sessions are all roots. Sub-agent sessions are the only non-root sessions.

---

## Main session

Each user has exactly one active main session per instance. It is resolved (or created) on demand: Sentinel looks for an active `main` binding (`binding_type="main"`, `binding_key="owner"`), then falls back to the oldest existing root session, and finally creates a fresh `Main` session if the user has none.

A main session must be a root session owned by the user. A Telegram channel session (group **or** non-owner DM) cannot be promoted to main — attempting it raises `SessionBindingTargetInvalidError` ("Telegram channel sessions cannot be set as main").

Triggers that route to `main` resolve through the same `main` binding, so they always target the user's current main session.

---

## Session bindings

Routing identity is managed by **bindings**, not by a field on the session itself. A binding maps `(user_id, binding_type, binding_key)` to a session, with one active binding per key:

| Binding type | Key | Purpose |
|---|---|---|
| `main` | `owner` | The user's active main session. Setting a new main deactivates the previous one. |
| `telegram_dm` | chat identifier | A Telegram direct-message channel session. |
| `telegram_group` | chat identifier | A Telegram group/supergroup channel session. |

Only root sessions can be bound. Re-binding reuses an existing inactive binding row when possible rather than creating duplicates.

---

## Sub-agent sessions

When the agent delegates work via the `delegate` tool, the orchestrator creates an isolated **child session** with `parent_session_id` set to the spawning session and a title like `sub-agent:<objective>`. The child runs the delegated task under its own message history.

The `delegate` tool is excluded from the sub-agent's own tool registry, so **sub-agents cannot recursively delegate** further sub-agents. If no per-instance agent runtime support is available, the task is marked failed with `Agent runtime support unavailable`.

For task lifecycle, scope, and tool limits, see [Agent Loop](../concepts/agent-loop.md).

---

## Session isolation

Each session has its own message history and active turn state. Tool state and browser state are not shared between sessions.

Sessions do **not** have isolated memory: all sessions within an instance read from and write to the same shared [memory](../concepts/memory.md) tree. Memory isolation is at the instance boundary, not the session boundary.

---

## Telegram session routing

Telegram is configured per instance — each instance runs its own bot and routes
incoming messages to that instance's agent. See the [Telegram guide](../guides/telegram.md).

Routing is deterministic by chat type:

| Chat type | Routing |
|---|---|
| Owner private DM | Owner's main session |
| Non-owner private DM | Dedicated `telegram_dm` channel session for that user |
| Group / supergroup | Dedicated `telegram_group` channel session for that chat |

Group and non-owner sessions are designed to be treated as untrusted: the agent would not reveal secrets or take privileged actions without explicit owner approval.

---

## Session deletion and trigger routing

Deleting a session cascades to its messages, summaries, and sub-agent tasks.

If a session referenced by a trigger is deleted, the trigger falls back silently to the main session on its next fire. The `action_config` will contain `route_fallback_reason` and `last_invalid_target_session_id` to help diagnose this.

If you delete a session that [triggers](../concepts/triggers.md) point to, update or redirect those triggers to avoid silent fallback behavior.

---

## Context compaction

When a session's message history grows large enough to exceed the token budget, the agent runs **compaction**:

1. The 10 most recent turns are kept as active context (counted as turn pairs, not raw message rows)
2. All older messages are **deleted from the database** and replaced with a single summary message
3. The summary is generated by the LLM if a provider is available, or by a fallback text concatenation if no LLM provider is reachable

:::warning Compaction is destructive
Compacted messages cannot be recovered. The session history is permanently shorter after compaction. The UI emits compaction status events and a completion toast when auto-compaction runs. If you need to preserve full history for a session, export it before the token budget is exhausted.
:::

The quality of the summary depends on whether an LLM provider is configured for the instance. Without one, the fallback produces a lower-quality concatenation with timestamps.
