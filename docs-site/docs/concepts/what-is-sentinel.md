---
sidebar_position: 1
title: What is Sentinel?
---

# What is Sentinel?

Sentinel is an open-source autonomous agent platform built for real operational use. It gives teams a complete, self-hosted stack to run AI agents with strong runtime controls, persistent memory, browser automation, and a modern operator interface — all running locally via Docker.

---

## What agents can do

### Execute multi-step tasks
Agents run end-to-end with tool use, branching logic, and error recovery — not just single-turn responses. They can chain dozens of steps, recover from failures, and hand off to sub-agents when needed.

### Maintain memory across sessions
Sentinel uses a hierarchical persistent memory model. Agents stay coherent across long-running sessions, picks up where they left off, and accumulate domain knowledge over time.

### Operate a real browser
Playwright is built in. Agents browse, click, fill forms, extract data, and handle dynamic pages. Operators can watch execution live via a VNC view in the UI.

### Delegate to sub-agents
Agents can spawn bounded sub-agents for parallel or isolated subtasks, then consolidate results — without needing a separate orchestration framework.

### Respond to triggers
Cron schedules, webhooks, and scripts can all fire agents. Agents can also create and update their own trigger definitions when permitted.

---

## The operator interface

Sentinel ships with a modern web UI designed for people running and supervising agent work.

| Feature | Description |
|---|---|
| Session view | Active turn, tool calls, and reasoning visible in real time |
| Live browser monitor | Watch the agent's browser via VNC |
| Memory inspector | Browse and edit the agent's memory tree |
| Trigger manager | View, create, enable/disable scheduled triggers |
| araiOS workspace | Manage tools, permissions, and approval queues |

---

## The Python runtime

Agents run on a custom Python runtime — not a thin wrapper around a framework. This gives you:

- Direct control over execution behavior and task logic
- No hidden prompt scaffolding you can't inspect
- Embeddable in Python-native pipelines

---

## Architecture

```
User / Telegram
      ↓
  Sentinel UI  ←→  araiOS Workspace
      ↓
  Agent Runtime (Python)
      ↓
  araiOS Control Plane
  ├── Custom Tools (sandboxed Python)
  ├── Memory (hierarchical, persistent)
  ├── Permissions + Approvals
  └── Triggers (cron / webhooks)
      ↓
  Browser (Playwright) + External APIs
```

---

## Open source

Sentinel is licensed under **AGPL-3.0** and built by [ARAIS](https://arais.us). Fully self-hosted — no telemetry, no cloud dependency, no usage fees.
