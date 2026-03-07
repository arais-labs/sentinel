---
sidebar_position: 8
title: Emergency Stop
---

# Emergency Stop (Estop)

The emergency stop system lets operators halt agent execution at different depths. It is not just an on/off switch — there are four levels, each with a different scope.

---

## The four levels

| Level | Name | What it blocks |
|---|---|---|
| `0` | **NONE** | Nothing — normal operation |
| `1` | **TOOL_FREEZE** | All tool execution. The agent loop continues but no tools can run. |
| `2` | **NETWORK_KILL** | HTTP requests and all browser tools (`http_request`, all `browser_*`). Other tools still run. |
| `3` | **KILL_ALL** | Everything, including starting the agent loop. New turns will not start. |

---

## Important: KILL_ALL vs TOOL_FREEZE

`TOOL_FREEZE` (level 1) blocks tool execution mid-loop. The loop itself is still running — the LLM is called, but any tool calls are rejected.

`KILL_ALL` (level 3) prevents the agent loop from **starting at all**. If a turn is already running when KILL_ALL is set, it will finish its current LLM call but the loop will not proceed to the next iteration.

Use `TOOL_FREEZE` to stop tool use while keeping the agent able to respond to messages.

Use `KILL_ALL` when you need to completely freeze all agent activity immediately.

---

## Legacy estop setting

Earlier versions used a boolean `estop_active` setting. This is still supported for backward compatibility and maps to `TOOL_FREEZE` (level 1). If you are using the legacy boolean, upgrading to the level-based system gives you finer control.

---

## When to use each level

| Situation | Recommended level |
|---|---|
| Agent is doing something unexpected with tools | `TOOL_FREEZE` |
| Agent is making external network calls you want to stop | `NETWORK_KILL` |
| Something is seriously wrong and all activity must stop | `KILL_ALL` |
| Investigation complete, resuming normal operation | `NONE` |

---

## Estop and sub-agents

Estop applies to all agent execution within the instance, including sub-agents spawned by the main agent. Setting `KILL_ALL` blocks all loops — parent and child.

---

## Setting estop

Estop is controlled from the Sentinel operator UI under **Settings**. It takes effect immediately on the next iteration check.
