---
sidebar_position: 2
title: Quick Start
---

# Quick Start

Get Sentinel running locally in under 5 minutes.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- macOS, Linux, or Windows (WSL2)
- A bash terminal (interactive — not CI)
- An API key for Anthropic or OpenAI

---

## 1. Clone the repo

```bash
git clone https://github.com/arais-labs/sentinel.git
cd sentinel
```

---

## 2. Run the CLI

```bash
bash ./sentinel-cli.sh
```

This opens an interactive menu. On first run:

1. Select **Create config**
2. Enter your LLM provider API key (Anthropic or OpenAI)
3. Configure optional settings if needed (Telegram token, instance name)
4. Select **Start stack**

The CLI pulls Docker images and starts all services.

:::note
The CLI requires an interactive terminal with a TTY. It will not work in CI, non-interactive shells, or piped execution.
:::

---

## 3. Open the UI

Once the stack is up, open your browser:

| Service | URL |
|---|---|
| Login gateway | `http://localhost:4747/` |
| Sentinel agent UI | `http://localhost:4747/sentinel/` |
| araiOS workspace | `http://localhost:4747/araios/` |
| Live browser view (VNC) | `http://localhost:4747/vnc/` |

---

## 4. Start your first session

1. Go to `http://localhost:4747/` and log in with your API key
2. You land in the Sentinel session interface
3. Type a message — the agent has memory, browser access, and tools available immediately

The agent can browse the web, remember context across sessions, and run scheduled tasks out of the box.

---

## Next steps

- [Full installation guide](/guides/installation) — config options, multi-instance, updates
- [Agent loop](/concepts/agent-loop) — how the agent executes, what limits apply, and how to interpret stops
- [Memory model](/concepts/memory) — how the agent retains and retrieves context
- [Triggers](/concepts/triggers) — schedule agent tasks to run automatically
