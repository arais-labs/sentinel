---
sidebar_position: 3
title: Agent Loop
---

# Agent Loop

The agent loop is the core execution engine. Understanding how it runs — and how it stops — is important for diagnosing unexpected behavior and setting correct expectations.

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

The loop continues until the LLM stops using tools, a limit is hit, or an explicit stop is triggered.

---

## Iteration budget

Each turn has a maximum iteration count (default: 50). One iteration is one LLM call plus all tool calls it returns.

### Grace extension
When the base budget is exhausted, the loop does **not** stop immediately. It enters a grace period of up to 25% more iterations (minimum 10 extra). During grace, the LLM is asked to evaluate whether it needs more steps. If it says no, or if the grace budget also runs out, finalization begins.

### Finalization round
After the budget is exhausted, one final no-tools LLM call is made with an instruction to summarize and wrap up the current state. This produces the final response the user sees.

If the finalization round itself fails, a hardcoded fallback message is used.

:::note
Users may see slightly different response quality near the iteration limit depending on whether finalization succeeded cleanly. If a task is consistently hitting the limit, break it into smaller sub-tasks or use sub-agents.
:::

---

## How the loop stops

There are four distinct termination paths. Each produces a different persisted message:

| Path | Cause | What you see |
|---|---|---|
| **Clean stop** | LLM returned final text naturally | Agent's full response |
| **Budget + finalization** | Max iterations hit, finalization round completed | Summarized wrap-up from finalization |
| **Timeout** | Total turn time exceeded the configured limit | `⚠️ Agent timed out after Xs. You can retry.` |
| **User cancellation** | User clicked stop / sent CancelledError | `⚠️ Generation stopped by user.` |

Each path emits a different WebSocket `done` event with a distinct `stop_reason`. The frontend uses this to show the correct UI state.

:::tip
If you see a timeout message, the task likely involves a very long chain of slow tool calls. Try splitting the task or reducing the scope.
:::

---

## Streaming vs non-streaming

By default, turns run in streaming mode. The agent emits text deltas in real time as the LLM generates them.

In streaming mode, the `done` event is **deliberately deferred** until after the database commit. This prevents the frontend from loading messages before they are fully saved. If you see a brief pause at the end of a response before the UI updates, this is expected.

Non-streaming mode is used for Telegram and trigger-fired turns. The full response is generated before any output is sent.

---

## Operator inject queue

Operators can inject messages into a running turn via the inject queue. An injected message appears to the LLM as `[Operator interjection]: <text>`. This lets you redirect a running agent without cancelling the full turn.

This is primarily used by the Telegram bridge to inject follow-up instructions mid-execution.

---

## Tool image handling

Browser tools can return screenshots. These are automatically reinjected into the next LLM call so the agent can reason about what it sees.

There are two limits:
- **Max image count per context** — beyond this, older images are dropped silently
- **Max total image bytes per context** — images are dropped to stay within the byte limit

If images are being silently dropped, you may notice the agent making decisions that seem to ignore recent browser state. Use explicit `browser_get_text` calls as a fallback when visual context is critical.

---

## Incremental persistence

The loop can run in two persistence modes:

| Mode | When messages are saved |
|---|---|
| **Full** (default) | After the entire turn completes |
| **Incremental** | After each individual tool call |

Incremental mode is used in Telegram sessions. It means partial results are visible if the server crashes mid-turn, but it also means reconnecting users may see tool call rows without their results if a crash happened between a call and its result.

---

## Error handling

Common LLM errors are translated into readable messages before being shown:

| Error | Message shown |
|---|---|
| Rate limit (429) | "API rate limit reached. Please wait a moment and try again." |
| Auth failure (401) | "API authentication failed. Please check your API key in Settings." |
| Billing (402) | "API billing issue. Please check your account balance and payment method." |
| Server overload (503) | "The AI provider is currently overloaded. Please try again in a few moments." |
| Timeout | "Request timed out. The server took too long to respond." |
| All providers failed | "All AI providers failed. Please check your API keys in Settings." |

Raw error strings are capped at 300 characters before display.
