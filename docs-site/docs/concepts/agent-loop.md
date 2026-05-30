---
sidebar_position: 3
title: Agent Loop
---

# Agent Loop

The agent loop is the core execution engine. Understanding how it runs — and how it stops — is important for diagnosing unexpected behavior and setting correct expectations.

Sentinel hosts **multiple isolated instances per deployment**. The loop is not a single global engine: each instance has its own runtime support (`agent_runtime_support`), tool registry, and tool executor, built from that instance's database settings (including its own LLM provider credentials). When this page says "the agent loop," it means the loop running inside one instance's runtime context. An instance with no LLM provider configured has no runtime support, so it cannot run a turn at all.

---

## How one turn works

Each agent turn runs an iterative cycle:

```
Build context (memory + history + system prompt)
       ↓
Call LLM with available tools
       ↓
If LLM returns tool calls → execute tools → feed results back → repeat
If LLM returns final text → persist message → emit done event → stop
```

The loop continues until the LLM stops using tools, a limit is hit, an approval gate pauses it, or an explicit stop is triggered.

For details on how the context is assembled (system prompt, policies, agent mode, memory, history), see [Memory](./memory.md) and [Sessions](./sessions.md).

---

## Iteration budget

Each turn has a maximum iteration count. One iteration is one LLM call plus all the tool calls it returns.

- **Default budget:** 25 iterations (`chat_default_iterations`).
- **Configurable ceiling:** up to 100 (`chat_max_iterations`). Callers can request fewer; requests are clamped to this ceiling.
- Telegram-driven and trigger-fired turns run with a fixed budget of 25.

### Grace extension

When the base budget is exhausted, the loop does **not** stop immediately. It computes a grace extension of `max(10, base // 4)` iterations — that is, 25% of the base budget, but never fewer than 10 extra steps.

Before spending the grace budget, the loop runs a quick **grace analysis**: it sends a trimmed tail of the recent conversation to a fast-tier model and asks it to return `{"continue": true}` or `{"continue": false}`. If the model declines (or the analysis call errors or times out after 15s), the loop skips straight to finalization. If it approves, the loop continues into the grace iterations.

### Finalization round

Once the budget (including any granted grace) is exhausted, the loop makes one final LLM call **with no tools available** (`tool_choice="none"`). It instructs the model to stop calling tools and write a natural, user-facing wrap-up: what was completed, what is blocked or uncertain, and the single best next step. This produces the final response the user sees.

:::note
Response quality near the iteration limit depends on how much work remained when finalization kicked in. If a task is consistently hitting the limit, break it into smaller sub-tasks or delegate to sub-agents (see [Sessions](./sessions.md)).
:::

---

## How the loop stops

Each turn ends with a `done` event carrying a `stop_reason`. The frontend uses this to render the correct UI state. The distinct outcomes:

| `stop_reason` | Cause | What you see |
|---|---|---|
| `stop` | LLM returned final text naturally, or the finalization round completed | Agent's full response (or the finalization wrap-up) |
| `pending_approval` | A tool call hit an approval gate | The turn pauses; an approval card appears. The loop resumes when the approval is resolved |
| `timeout` | Total turn time exceeded the configured limit | A timeout notice; the run can be retried |
| `aborted` | User stopped the run, or tool execution was cancelled | A "generation stopped" notice |
| `error` | The provider or run failed | The translated error message (see [Error handling](#error-handling)) |

The `pending_approval` path is **not a failure** — it is a deliberate pause. When a tool action requires approval, the underlying tool result is surfaced as an HTTP 202 (Accepted, pending), not a 403 (denied). The loop stops cleanly and waits; resolving the approval starts a fresh turn that continues the work. See [Approvals](./approvals.md) for the full contract.

:::tip
If you see a timeout, the task likely involves a long chain of slow tool calls. Try splitting the task or reducing the scope.
:::

---

## Streaming vs non-streaming

Turns can run in two modes:

- **Streaming** (`stream=True`) — the agent emits text deltas in real time as the LLM generates them. This is what the **WebSocket session stream**, **Telegram-driven** turns, and **trigger-fired** turns use.
- **Non-streaming** (`stream=False`) — the full response is generated before any output is returned. This is used by the synchronous `POST .../sessions/{id}/chat` REST endpoint.

In streaming mode, the terminal `done` event is **deliberately deferred** until after the database commit. This prevents the frontend from reloading messages before they are fully saved. If you see a brief pause at the end of a response before the UI settles, that is expected.

---

## Operator inject queue

Operators (and the orchestrator) can inject messages into a running turn via the inject queue. At each iteration boundary the loop drains any queued items and appends them to the working history. An injected operator message appears to the LLM prefixed as `[Operator interjection]: <text>`, letting you redirect a running agent without cancelling the whole turn.

This mechanism backs sub-agent message injection: the orchestrator can steer a delegated child run mid-execution by enqueuing interjections on that child's session.

---

## Tool image handling

Browser and other image-producing tools can return screenshots. These are converted into image blocks and reinjected into the next LLM call so the agent can reason about what it sees. Reinjection is bounded:

- **Max images per turn** — default 2; additional images beyond this are skipped.
- **Max bytes per image** — default ~2 MB; oversized images are skipped.
- **Max total bytes per turn** — default ~4 MB; images that would exceed the budget are skipped.
- **Deduplication** — images already seen (by content hash) are skipped.

Skipping is silent. If the agent seems to ignore recent browser state, it may have hit an image limit — use an explicit text-extraction browser call (for example `get_text`) as a fallback when visual context is critical.

---

## Incremental persistence

The loop can persist messages in two modes:

| Mode | When items are saved | Used by |
|---|---|---|
| **Full** (default) | Once the turn completes | Synchronous `POST /chat`, trigger runs |
| **Incremental** | After each item (assistant turn / tool result) via a checkpoint callback | WebSocket session streaming, sub-agent runs |

Incremental mode means partial results are durable if the server crashes mid-turn. The trade-off: a reconnecting user may see a tool-call row whose result row had not yet been written if a crash landed between the call and its result.

---

## Error handling

Common LLM/provider errors are translated into readable messages before being shown:

| Error | Message shown |
|---|---|
| Rate limit (429) | "API rate limit reached. Please wait a moment and try again." |
| Auth failure (401) | "API authentication failed. Please check your API key in Settings." |
| Billing (402) | "API billing issue. Please check your account balance and payment method." |
| Server overload (503) | "The AI provider is currently overloaded. Please try again in a few moments." |
| Timeout | "Request timed out. The server took too long to respond." |
| All providers failed | "All AI providers failed. Please check your API keys in Settings." |

LLM provider credentials are configured **per instance** (in Settings / the API), not via environment variables. An auth or "all providers failed" message means the API key for *that instance* is missing or invalid — check that instance's settings, not the deployment's `.env`.

Most raw error strings are capped at 300 characters before display.

---

## Current limitations

Limits to be aware of:

- **No mid-run compaction.** Auto-compaction runs *between* WebSocket-driven runs, not mid-iteration. A single long autonomous run can fill its context window before the loop has a chance to compact.
- **Finalization is best-effort.** The wrap-up round is a normal LLM call; if the provider is degraded at that moment, the final message quality can suffer.
- **Image skips are silent.** There is no in-band signal to the agent that an image was dropped for size or count reasons.
