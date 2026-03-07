---
sidebar_position: 1
title: Installation
---

# Installation

Full installation guide for Sentinel.

---

## Requirements

| Requirement | Notes |
|---|---|
| Docker Desktop | Must be running before you start |
| bash | macOS, Linux, or Windows WSL2 |
| ~4 GB disk space | For Docker images |
| An interactive terminal | The CLI requires a real TTY |

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

The CLI opens an interactive menu:

| Option | Description |
|---|---|
| Create config | Generate `.env` for a new instance |
| Edit config | Modify an existing config |
| Start stack | Pull images and start all services |
| Stop stack | Gracefully stop all services |
| View logs | Tail container logs |
| Pull latest images | Update to the latest builds |
| Destroy | Remove containers and volumes |

**On first run:** Create config → Start stack.

:::note
The CLI must run in an interactive terminal with a TTY. It will not work in CI, non-interactive shells, or piped execution.
:::

---

## Step 3 — Access the UI

| Service | URL |
|---|---|
| Login gateway | `http://localhost:4747/` |
| Sentinel UI | `http://localhost:4747/sentinel/` |
| araiOS workspace | `http://localhost:4747/araios/` |
| Live browser (VNC) | `http://localhost:4747/vnc/` |

---

## Config reference

Key `.env` fields:

| Field | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key (for Claude models) |
| `OPENAI_API_KEY` | OpenAI API key (for GPT models) |
| `TELEGRAM_BOT_TOKEN` | Optional — enables Telegram integration |
| `INSTANCE_NAME` | Identifier for this instance (used in multi-instance setups) |

You need at least one LLM provider key. Both can be set simultaneously — the agent uses them with automatic failover.

---

## LLM provider failover

Sentinel uses a `ReliableProvider` wrapper that tries providers in order with exponential backoff:

- 3 retries per provider, starting at 500ms and doubling each attempt
- On non-retryable errors (auth failures, billing issues), the provider is skipped immediately
- If all providers fail, the agent surfaces a clear error message

To benefit from failover, set both `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`. Provider order is determined by config.

---

## Updating

```bash
bash ./sentinel-cli.sh
# Select: Pull latest images → Restart stack
```

---

## Troubleshooting

**Docker is not running**
Start Docker Desktop before running the CLI.

**Port 4747 is already in use**
Another process is using the port. Stop that process or change the port in your `.env`.

**CLI exits immediately without a menu**
You are running it in a non-interactive shell. Run it directly in a terminal.

**Agent returns "API authentication failed"**
Your LLM provider key is invalid or expired. Check it in Settings → API Keys.
