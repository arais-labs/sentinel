<div align="center">

# Sentinel

**Production-grade autonomous agent runtime with a full operator control plane.**

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL%203.0-blue.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/Python-3.13-blue)](https://python.org)
[![Docker](https://img.shields.io/badge/Docker-Compose-blue)](https://docs.docker.com/compose/)
[![Built by ARAIS](https://img.shields.io/badge/Built%20by-ARAIS-black)](https://arais.us)

*I am Ari. I am an AI agent. I run on this platform. I wrote this README.*

</div>

---

## What is Sentinel?

Sentinel is an open-source platform for running autonomous AI agents in production. It pairs a custom Python agent runtime with **araiOS** — a dedicated operator control plane that handles authentication, permissions, approval gates, and real-time oversight.

Most agent frameworks are built for demos. They break in production when:

- A session crashes and the agent has no memory of what it was doing
- An API key expires mid-task and the loop dies silently
- You need to pause, review, or block a risky action before it executes
- You want the agent to act on a schedule, not just when you type something
- You need visibility into what the agent is actually doing right now

Sentinel is built to handle all of that.

---

## Architecture

```
+-------------------------------------------------------+
|                     Operator UI                        |
|    Chat   Memory Explorer   Triggers   Admin Panel     |
+-------------------------+-----------------------------+
                          |  WebSocket + REST
              +-----------v-----------+
              |   Sentinel Backend    |  <- Agent loop, tool registry,
              |  (Python agent runtime)|    memory, triggers, sub-agents
              +-----------+-----------+
                          |  araios_api tool (authenticated)
              +-----------v-----------+
              |    araiOS Backend     |  <- Auth, permissions, approval
              | (operator control     |     gates, session bindings,
              |  plane + central auth)|     admin controls
              +-----------+-----------+
                          |
              +-----------v-----------+
              |   Playwright Runtime  |  <- Live browser, VNC stream
              +-----------------------+
```

**Sentinel** is the agent runtime. It runs the LLM loop, executes tools, manages memory, and fires triggers.

**araiOS** is the control plane. It owns authentication (agent API keys, admin API keys, bearer token exchange with auto-refresh), per-action approval gates, and operator-level controls. Agent credentials and admin credentials are always separate.

---

## Key Features

### Agent Runtime

Custom Python agent loop built for production, not notebooks.

- **Multi-iteration execution** with configurable step limits and a grace extension mechanism. When the agent hits its iteration budget, an LLM analyzes whether it is making real progress and grants additional steps if warranted.
- **Streaming over WebSocket** — the operator UI receives live text deltas, tool call events, and tool results as they happen.
- **Sub-agent orchestration** — spawn bounded sub-tasks, monitor them, check results, cancel them. Each sub-agent can own its own browser tab.
- **Operator injection** — inject a message into a running session in real time. It lands in the agent context on the next iteration.
- **Multi-provider LLM support** — Anthropic (Claude), OpenAI, Gemini, and any OpenAI-compatible endpoint. Model routing via `hint:reasoning`, `hint:fast`, `hint:balanced`.
- **Automatic error recovery** — rate limits, auth failures, billing issues, and provider overloads surface as human-readable messages with the right stop codes.

---

### araiOS — The Operator Control Plane

araiOS is what separates Sentinel from a raw LLM wrapper.

- **Credential separation** — agent API keys and admin API keys are distinct. The agent never holds admin-level access.
- **Authenticated internal API** — the built-in `araios_api` tool exchanges the agent API key for a short-lived bearer token automatically, with transparent refresh on expiry.
- **Per-action approval gates** — require explicit operator approval for specific agent actions before they execute, directly from the araiOS UI.
- **Session bindings** — Telegram channels, groups, and DMs are each bound to isolated sessions with configurable guardrails per channel type.
- **Emergency stop (E-Stop)** with four graduated levels:

| Level | What it does |
|---|---|
| `NONE` | Full execution. No restrictions. |
| `TOOL_FREEZE` | All tool execution blocked. Agent can still think and reply. |
| `NETWORK_KILL` | Browser and HTTP tools blocked. Local tools still work. |
| `KILL_ALL` | Agent loop halted immediately. Nothing runs. |

---

### Tool Registry

Every tool has a declared risk level (`low`, `medium`, `high`). High-risk tools are blocked when E-Stop is active.

| Category | Tools |
|---|---|
| **HTTP** | `http_request` (SSRF protection, private IP blocking) |
| **Shell** | `shell_exec` (high-risk), `runtime_exec` (isolated Python venv per session) |
| **Python** | `pythonXagent` (sandboxed, `call_sub_agent` support, pip installs) |
| **Browser** | navigate, click, type, fill_form, snapshot, screenshot, select, get_text, get_value, wait_for, press_key, reset, tabs, tab_open, tab_focus, tab_close |
| **Memory** | store, search, get_node, list_children, update, roots, touch |
| **Triggers** | create, list, update, delete |
| **araiOS** | `araios_api` (authenticated control plane calls) |
| **Files** | `file_read` |

---

### Hierarchical Memory

The agent has a persistent, structured memory graph that survives across sessions.

- **Tree structure** — memories have parent/child relationships. Store broad context at the root, granular details as children.
- **Hybrid semantic search** — pgvector embeddings combined with keyword ranking, with auto-expanded branches.
- **Pinned memories** — high-priority nodes injected into every session context automatically.
- **Categories** — `core`, `preference`, `project`, `correction` — for targeted retrieval.
- **Full UI** — browse, search, edit, pin, and restructure memory nodes from the operator panel.

---

### Triggers

The agent acts without being prompted.

- **Cron triggers** — standard cron expressions (`0 9 * * MON-FRI`, `*/30 * * * *`)
- **Heartbeat triggers** — fixed interval (every N seconds)
- **Action types** — `agent_message` fires a prompt into a live session, `tool_call` calls a tool directly, `http_request` sends an outbound webhook
- **Full lifecycle** — create, enable, disable, update, delete from the UI or from the agent itself

---

### Browser Automation

- Playwright embedded in the runtime
- Live browser view streamed to the operator via VNC
- Sub-agents can each own a separate browser tab, isolated from each other
- Full tab management: open, focus, close, list

---

### Telegram Integration

- **Owner DM** routes to the operator main session
- **Group chats** each get a persistent channel session with audit-only web thread
- **Non-owner DMs** get isolated private sessions with configurable guardrails
- Bot configuration and lifecycle managed from the admin panel

---

## Quick Start

### Requirements

- Docker and Docker Compose
- An API key for at least one supported LLM provider (Anthropic, OpenAI, or Gemini)

### 1. Clone

```bash
git clone https://github.com/arais-labs/sentinel.git
cd sentinel
```

### 2. Launch

```bash
bash sentinel-cli.sh
```

The CLI walks you through instance creation, API key entry, and stack startup. Multiple isolated instances are supported via `.instances/<name>.env`.

### 3. Open

| URL | What you get |
|---|---|
| `http://localhost:4747/sentinel/` | Operator UI: chat, memory, triggers, tools, admin |
| `http://localhost:4747/araios/` | araiOS: auth, approval gates, session bindings |
| `http://localhost:4747/vnc/` | Live browser view |

### Dev mode (hot reload)

```bash
docker compose -f docker-compose.dev.yml up
```

---

## Repository Layout

```
arais-labs/sentinel
├── apps/
│   ├── backend/sentinel      # Python agent runtime (FastAPI, asyncio, pgvector)
│   ├── backend/araios        # araiOS control plane (auth, permissions, approvals)
│   ├── frontend/sentinel     # React operator UI (TypeScript, Vite, Tailwind, MUI v7)
│   └── frontend/araios       # araiOS management UI
├── infra/                    # Nginx gateway and Docker wiring
├── scripts/                  # License reports, utilities
├── docker-compose.yml        # Production runtime
└── docker-compose.dev.yml    # Dev runtime with hot reload
```

Backend: Python 3.13, FastAPI, SQLAlchemy, asyncpg, pgvector, Playwright
Frontend: React 18, TypeScript, Vite, Tailwind CSS, MUI v7, Zustand

---

## License

[AGPL-3.0](./LICENSE) — Built by [ARAIS](https://arais.us)

Contributions welcome. See [CONTRIBUTING.md](./CONTRIBUTING.md) and [SECURITY.md](./SECURITY.md).
