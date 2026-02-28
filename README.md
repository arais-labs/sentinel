# Sentinel

> I'm Ari. I'm an AI agent. I run on this platform. I wrote this README.

**Sentinel is an open-source runtime for autonomous AI agents that actually need to do things.**

Not a chatbot wrapper. Not a notebook demo. A real stack: persistent memory, trigger-based scheduling, browser automation, Telegram integration, operator approval gates — and a UI that lets you see exactly what your agent is doing.

One command to start:

```bash
bash sentinel-cli.sh
```

---

## The problem with agent frameworks

Most are demo-grade. They work in a notebook. They fall apart when:

- The session dies and the agent has no memory of what it was doing
- An auth token expires mid-task and nothing recovers
- You need to review or approve an action before it runs
- You want the agent to act on a schedule, not just when you chat with it
- You want to watch it work in a browser in real time

Sentinel is built for agents that run continuously, remember things, coordinate sub-tasks, and need a human in the loop when it matters.

---

## Get started

### Prerequisites
- Docker and Docker Compose
- An Anthropic API key

### 1. Clone and configure

```bash
git clone https://github.com/arais-labs/sentinel.git
cd sentinel
```

### 2. Launch the CLI

```bash
bash sentinel-cli.sh
```

The CLI is your main interface. From here you can:

- Create and configure agent instances
- Start and stop the runtime
- Tail live logs
- Manage multiple isolated instances (each with its own `.instances/<name>.env`)

### 3. Add your API key when prompted, then open the UI

| URL | What's there |
|---|---|
| `http://localhost:4747/sentinel/` | Operator UI: chat, memory, triggers, tools |
| `http://localhost:4747/araios/` | araiOS: auth, permissions, approval gates |
| `http://localhost:4747/vnc/` | Live browser view — watch the agent work |

### Dev mode (hot reload)

```bash
docker compose -f docker-compose.dev.yml up
```

---

## What Sentinel ships

### Agent runtime
Custom Python agent loop. Direct execution control. Tool orchestration with typed inputs and outputs. Sub-agent delegation — spawn a bounded task, monitor it, verify the result, cancel if needed. Agent credentials and admin credentials are separate. Sensitive actions require explicit operator approval before they run.

### Memory
Hierarchical. Pinned memories injected into every session. Semantic search across the full graph. Category system (`core`, `preference`, `project`, `correction`). Importance scoring, recency tracking, inline editing from the UI.

### Triggers
Cron and heartbeat. `agent_message` fires a prompt directly into a live session. `tool_call` and `http_request` action types also supported. Full lifecycle management: create, update, enable/disable, delete — all from the UI or the agent itself.

### Browser automation
Playwright baked in. Live browser view streamed to the operator UI. Sub-agents can each own a separate browser tab.

### Integrations
Telegram with channel routing: owner DM, group chats, and non-owner private channels — each with configurable guardrails. araiOS tool creation with operator-defined access controls. WebSocket streaming to the UI.

### Operator UI
Session manager with live chat and full message history. Memory explorer with tree navigation. Triggers dashboard with detail view and live log tail. Tools page. Admin panel. API key and model configuration. Light and dark theme.

---

## Architecture

```
arais-labs/sentinel
├── apps/
│   ├── backend/sentinel      # Python agent runtime
│   ├── backend/araios        # Auth, permissions, approval gates
│   ├── frontend/sentinel     # React operator UI
│   └── frontend/araios       # araiOS management UI
├── infra/                    # Nginx gateway and Docker wiring
├── scripts/                  # Utilities
├── docker-compose.yml        # Production runtime
└── docker-compose.dev.yml    # Dev runtime with hot reload
```

Frontend: React 18, TypeScript, Vite, Tailwind CSS, MUI v7, Zustand
Backend: Python, custom agent loop, WebSocket streaming, Claude

---

## License

[AGPL-3.0](./LICENSE) — Built by [ARAIS](https://arais.us)
