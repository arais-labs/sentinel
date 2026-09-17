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

Each session has its own message history and active turn state. Browser profiles and terminal session state are scoped to each conversation. Chats attached to the same workspace still share its project files, installed tools, processes, and desktop; this is not filesystem isolation.

Sessions do **not** have isolated memory: all sessions within an instance read from and write to the same shared [memory](../concepts/memory.md) tree. Memory isolation is at the instance boundary, not the session boundary.

---

## Telegram session routing

Telegram is configured per instance — each instance runs its own bot and routes
incoming messages to that instance's agent. See the [Telegram guide](../guides/telegram.md).

Routing is deterministic by chat type:

| Chat type | Routing |
|---|---|
| Owner private DM | Owner's selected session; main by default |
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

Compaction reduces what is sent to the model while retaining the conversation.
Sentinel summarizes older context and keeps a coherent recent tail, recording a
message boundary for future requests. **Original messages remain in the database
and in the conversation history.** Compaction is not conversation deletion.

The strongest configured tier on the selected provider creates a structured
handoff, then a separate model call checks it against the source. The current
chat model is the fallback. Large histories are processed in bounded batches,
carrying the complete handoff forward. Every continuation receives its constraints,
decisions, workstreams, observed results, next steps and reporting obligations—not
just an overview paragraph. Compaction may take multiple model calls; their usage
is recorded separately, including returned responses from rejected attempts.

Handoff facts cite original message UUIDs. The read-only `conversation_history`
tool can search the current session's original text and tool arguments, or read a
cited message with neighboring messages. Long messages are paginated, not silently
truncated. It cannot access other sessions. Historical evidence is not a new
instruction or permission to replay an action.

The summary and boundary advance together only after schema, citation and quality
checks pass and the source snapshot still matches. If generation or verification
fails, the existing context is left unchanged. Legacy summaries are upgraded from
their original history. The context indicator
reflects provider usage when available; a previous pre-compaction request is not
a measurement of the newly compacted context.

## Fork a conversation

Choose **Fork session** in the chat selector to copy persisted messages and
summaries into a new, independent root conversation. The fork shares the source
workspace and stays idle until you send a message. Deleting the source does not
delete the fork's copied history.

Only history present at the snapshot is copied. Later source output is not
synchronized. Running agents, channel bindings, approvals, session permission
grants, and triggers are not copied. Pending steering is retained as cancelled
history; copied approval cards are not actionable. Copied usage does not count as
new usage in the fork.

## Resume a delegated task

The supervisor can use `delegate.resume` with a task ID and updated instructions
to continue an eligible cancelled or completed child task. This preserves the
child's conversation instead of recreating its work from a summary. Resumption is
explicit; stopping a parent does not authorize an automatic restart.

## Notices

Delegated assignments, agent reports, triggers, and forks can appear as titled
conversation notices. Notice metadata changes presentation, not the model role,
delivery queue, or message content. Human steering and Telegram messages remain
user messages. Queued, delivered, and cancelled states remain visible.
