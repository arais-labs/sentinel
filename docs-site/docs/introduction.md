---
slug: /
sidebar_position: 1
title: Introduction
---

# Sentinel

**Run AI agents that execute, remember, browse, and operate — on your hardware, under your control.**

Sentinel is an open-source autonomous agent platform built by [ARAIS](https://arais.us). It combines a custom Python agent runtime, structured persistent memory, browser automation, a permissions and approval system, and a clean operator UI into one self-hosted stack you spin up with a single command.

![Sentinel UI](/img/sentinel.png)

---

## What Sentinel is

Sentinel is not an agent framework. It is a complete runtime — something you run, not something you build on top of.

You get a real agent that executes multi-step tasks, browses the web, stores memory across sessions, fires on schedules, and operates under explicit operator controls. Everything is self-hosted, open source, and built to run in production.

| Component | What it does |
|---|---|
| **Agent runtime** | Custom Python loop — iterative, tool-using, recoverable |
| **Memory** | Hierarchical tree, persisted across sessions, hybrid search |
| **Browser** | Playwright built in, live VNC view for operators |
| **Triggers** | Cron and heartbeat schedules, webhook-ready |
| **Approvals** | Three-provider gate system — pause agent until you approve |
| **araiOS** | Control plane for tools, permissions, modules, and coordination |

---

## Two systems, one stack

### Sentinel
The agent runtime and operator interface. Handles the loop, memory, sessions, triggers, and browser. This is the thing the user talks to.

### araiOS
The control plane underneath. Handles custom tools, permissions, approval gates, module data stores, and agent coordination. Agents interact with araiOS through a REST API.

> **Sentinel is the agent. araiOS is what the agent is allowed to do and how.**

---

## Get started

- [Quick Start](/quickstart) — running in under 5 minutes
- [What is Sentinel?](/concepts/what-is-sentinel) — architecture and the agent loop
- [What is araiOS?](/concepts/what-is-araios) — tools, permissions, and the module system
