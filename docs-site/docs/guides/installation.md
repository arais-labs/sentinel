---
sidebar_position: 1
title: Installation
---

# Installation

Full installation guide for Sentinel.

## Requirements

| Requirement | Notes |
|---|---|
| Docker Desktop | Must be running before you start |
| bash | macOS, Linux, or Windows WSL2 |
| ~4GB disk space | For Docker images |

---

## Step 1 — Clone the repo

```bash
git clone https://github.com/arais-labs/sentinel.git
cd sentinel
```

---

## Step 2 — Run the CLI

```bash
bash ./sentinel-cli.sh
```

Interactive menu options:

| Option | Description |
|---|---|
| Create config | Generate `.env` for a new instance |
| Edit config | Modify existing config |
| Start stack | Pull images and start all services |
| Stop stack | Gracefully stop all services |
| View logs | Tail container logs |
| Destroy | Remove containers and volumes |

On first run: **Create config → Start stack**.

:::note
Must be run in an interactive terminal with a TTY. Won't work in CI or non-interactive shells.
:::

---

## Step 3 — Access

| Service | URL |
|---|---|
| Login gateway | `http://localhost:4747/` |
| Sentinel UI | `http://localhost:4747/sentinel/` |
| araiOS workspace | `http://localhost:4747/araios/` |
| Live browser | `http://localhost:4747/vnc/` |

---

## Updating

```bash
bash ./sentinel-cli.sh
# Select: Pull latest images → Restart stack
```

---

## Config reference

Key `.env` fields:

| Field | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key (if using Claude) |
| `OPENAI_API_KEY` | OpenAI API key (if using GPT) |
| `TELEGRAM_BOT_TOKEN` | Optional Telegram integration |
| `INSTANCE_NAME` | Name for this instance (multi-instance setups) |
