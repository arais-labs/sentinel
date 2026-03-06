---
sidebar_position: 2
title: Quick Start
---

# Quick Start

Get Sentinel running locally in under 5 minutes.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- macOS, Linux, or Windows (WSL2)
- A bash terminal

---

## 1. Clone the repo

```bash
git clone https://github.com/arais-labs/sentinel.git
cd sentinel
```

---

## 2. Run the Sentinel CLI

```bash
bash ./sentinel-cli.sh
```

This launches an interactive menu. On first run:

1. Select **Create config**
2. Enter your LLM provider API key (Anthropic, OpenAI, etc.)
3. Configure optional settings (Telegram token, instance name)
4. Select **Start stack**

:::note
Run this in an interactive terminal with a TTY. It won't work in CI environments or non-interactive shells.
:::

---

## 3. Open the UI

| Service | URL |
|---|---|
| Login gateway | `http://localhost:4747/` |
| Sentinel UI | `http://localhost:4747/sentinel/` |
| araiOS workspace | `http://localhost:4747/araios/` |
| Live browser view | `http://localhost:4747/vnc/` |

---

## 4. Start your first session

1. Log in at `http://localhost:4747/`
2. Open **Sentinel** → you'll see the main session interface
3. Type a message to the agent — it has memory, browser access, and tools available immediately

---

## Next steps

- [Full installation guide](/guides/installation) — config options, multi-instance, updates
- [What is araiOS?](/concepts/what-is-araios) — set up custom tools and permissions
- [Memory model](/concepts/memory) — understand how agents retain context
