---
slug: /
sidebar_position: 1
title: Introduction
---

# Sentinel

**Run AI agents that execute, remember, browse, and adapt — on your hardware, under your control.**

Sentinel is an open-source autonomous agent platform built by [ARAIS](https://arais.us). It combines a custom Python agent runtime, structured persistent memory, browser automation, and a clean operator UI into one self-hosted stack you spin up with a single command.

![Sentinel UI](/img/sentinel.png)

---

## What makes Sentinel different

Most agent frameworks are built for demos. Sentinel is built for the turn after that — when you need agents to handle real tasks, stay coherent across sessions, and operate under clear operator controls.

| | Sentinel |
|---|---|
| **Runtime** | Custom Python agent — full control over execution |
| **Memory** | Hierarchical, persistent — coherent across sessions |
| **Browser** | Playwright built-in with live VNC view |
| **Triggers** | Cron, webhooks, scripts — all in one place |
| **Tools** | Sandboxed Python via araiOS, gated by permissions |
| **Hosting** | Self-hosted Docker — no cloud dependency |

---

## Core components

- **Sentinel** — the agent runtime and operator interface
- **araiOS** — the control plane: tools, permissions, approvals, coordination
- **Browser runtime** — Playwright + VNC for live browser automation
- **Memory store** — hierarchical persistent memory across sessions
- **Triggers** — schedule and automate agent execution

---

## Get started

- [Quick Start](/quickstart) — up and running in minutes
- [What is Sentinel?](/concepts/what-is-sentinel) — architecture and capabilities
- [What is araiOS?](/concepts/what-is-araios) — the control plane explained
