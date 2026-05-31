---
slug: /
sidebar_position: 1
title: Introduction
---

# Sentinel

**Sentinel is a self-hosted platform for running autonomous AI operators: browser automation, scheduling, coding workflows, and operations tasks, all with strong controls and a clean UI.**

Sentinel is an open source agent platform built by [ARAIS](https://arais.us). It combines a custom Python agent runtime, structured persistent memory, browser automation, a permissions and approval system, and a React control plane into one stack you can boot quickly.

A single Sentinel deployment hosts **multiple isolated instances**. Each instance is its own operator with its own database, its own LLM provider credentials, and its own runtime workspace — all served from one shared stack.

---

## Why teams use Sentinel

| Capability | What you get |
|---|---|
| **Multiple instances, one stack** | One deployment runs many isolated agent instances, each with its own database, LLM provider, and runtime |
| **Built in browser automation** | Playwright tools with 25+ actions (navigate, click, type, snapshot, network capture) and an optional stealth mode |
| **Built in scheduling** | Cron and heartbeat triggers for recurring runs, scheduled per instance |
| **Built in memory** | Hierarchical persistent memory with hybrid (vector + keyword) retrieval across sessions |
| **Built in controls** | Three-level permission policies, approval gates, audit trail, and per-session stop controls |
| **Built in UI** | Sessions workbench, modules, approvals, logs, and operational controls — all instance-aware |

---

## One stack, many instances

Sentinel runs as a single shared stack:

- A **manager database** (`sentinel_manager`) holds instance metadata, runtime targets, revoked tokens, and audit logs.
- Each **instance** gets its own application database (created on demand), its own LLM provider credentials stored encrypted in that instance's settings, and its own runtime context (tool registry, executor, trigger scheduler, sub-agent orchestrator).

The whole stack is reached through one origin. The instance is selected in the URL path — API routes are scoped as `/api/v1/instances/{instance_name}/...`, and the UI parametrizes routes on the instance name. There are no separate ports or compose files per instance.

LLM provider credentials (Anthropic, OpenAI, Gemini, and others) are **configured per instance through the UI or API** and stored encrypted in that instance's settings. They are no longer read from environment variables.

Inside an instance you get the agent runtime, sessions, hierarchical memory, triggers, browser execution, system and user-defined modules, permission policies, approvals, sub-agent delegation, and coordination APIs.

See [Multi-Instance](/guides/multi-instance) for the full instance model.

---

## How you run it

Sentinel ships two ways to run the same stack:

- **Docker Compose** — Postgres (pgvector), the FastAPI backend, and the React frontend, served on a single port. Requires `SENTINEL_DATA_ENCRYPTION_KEY` and `SENTINEL_JWT_SECRET_KEY`. See [Installation](/guides/installation).
- **macOS desktop app** — an Electron app (Apple Silicon first) shipped as an unsigned DMG via GitHub releases. It bundles Postgres + pgvector and runs the FastAPI backend as a managed child process, with no Docker dependency.

The current build is version **0.1.0**.

---

## Current limitations

Things to know about the current build:

- The macOS desktop app is **Apple Silicon only** and ships **unsigned** (no x86_64, Linux, or Windows builds; no code signing or notarization yet).
- Browser automation runs Playwright (headless/headed Chrome) only — there is **no VNC or display-server integration**.
- The embedding service is process-global, not per-instance.
- There is no automatic cleanup of an instance's database when the instance is deleted.

---

## Start here

- [Quick Start](/quickstart)
- [Installation](/guides/installation)
- [What is Sentinel?](/concepts/what-is-sentinel)
- [Multi-Instance](/guides/multi-instance)
- [Modules and permissions](/concepts/modules-and-permissions)
