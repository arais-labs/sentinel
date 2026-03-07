---
sidebar_position: 1
title: What is Sentinel?
---

# What is Sentinel?

Sentinel is a self-hosted autonomous agent platform. It runs a real agent loop — iterative, tool-using, memory-backed — and gives operators full visibility and control over what the agent does.

---

## The agent

The agent runs a think-act-observe cycle. Each turn:

1. Builds context from memory, system prompts, and conversation history
2. Calls the LLM with available tools
3. Executes any tool calls the LLM returns
4. Observes the results and loops until the task is complete or a stop condition is hit

The runtime is Python, not a thin wrapper around a framework. You can inspect the full execution path.

---

## What agents can do

### Execute multi-step tasks
Agents chain steps, use tools, recover from failures, and delegate to sub-agents for bounded parallel work. A single turn can involve dozens of tool calls.

### Maintain memory across sessions
Memory is hierarchical and persistent. Agents retain domain knowledge, user preferences, and project state across sessions — without re-briefing every turn.

### Operate a real browser
Playwright is built in. Agents navigate, click, fill forms, extract data, and screenshot pages. Operators watch execution live via VNC at `/vnc/`.

### Run on a schedule
Cron and heartbeat triggers fire agents automatically. Agents can create and manage their own triggers when permitted.

### Gate actions behind human approval
High-risk operations pause the agent and surface a request for operator review. The agent resumes only after approval.

---

## The operator UI

| Feature | Description |
|---|---|
| Session view | Active turn, tool calls, and live streaming output |
| Live browser monitor | VNC view of the agent's browser |
| Memory inspector | Browse and edit the full memory tree |
| Trigger manager | View, create, enable, and disable scheduled triggers |
| araiOS workspace | Tools, permissions, approvals, and module management |

---

## Architecture

```
User / Telegram / Trigger
        ↓
  Sentinel UI  ↔  araiOS Workspace
        ↓
  Agent Runtime (Python)
  ├── Context builder (memory + history)
  ├── LLM provider (Anthropic / OpenAI / failover)
  ├── Tool adapter (araiOS + browser + runtime + git)
  ├── Approval gate (pause/resume on sensitive actions)
  └── Estop service (freeze or kill execution at any depth)
        ↓
  araiOS Control Plane
  ├── Custom tool modules (sandboxed Python)
  ├── Data modules (persistent record stores)
  ├── Permissions (allow / approval / deny per action)
  └── Approval queue (async human review)
        ↓
  Browser (Playwright) + External APIs + Git
```

---

## Open source

Sentinel is licensed under **AGPL-3.0** and built by [ARAIS](https://arais.us). Fully self-hosted — no telemetry, no cloud dependency, no usage fees.
