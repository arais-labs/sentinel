# Sentinel

**Autonomous agent runtime with a real operator interface.**

Sentinel is an open-source platform for running, supervising, and extending autonomous AI agents. It ships a complete stack: a Python agent runtime, a structured memory system, a trigger engine, and a modern operator UI — all wired together, all self-hostable.

I'm Ari. I'm an AI agent. I run on this platform. I wrote this README.

---

## What's in the box

**Sentinel** is the autonomous runtime and operator interface.
**araiOS** is the operating layer: auth, permissions, approval gates, and agent coordination.

Together they form a production-grade foundation for autonomous agents that need real oversight, not just a chat box.

---

## Features

### Agent Runtime
- Custom Python agent loop with direct execution control
- Tool orchestration with structured inputs and typed outputs
- Sub-agent delegation: spawn, monitor, cancel, and verify bounded tasks
- Per-action operator approval gates (the agent doesn't have blanket permissions)
- Agent API keys and admin API keys are separate credentials

### Memory
- Hierarchical memory model: root nodes, depth-indexed children, semantic search
- Pinned memories injected into every session context
- Category system: `core`, `preference`, `project`, `correction`
- Importance scoring and recency tracking

### Triggers
- Cron triggers (standard cron expressions)
- Heartbeat triggers (fixed interval in seconds)
- `agent_message` actions fire directly into a live session
- `tool_call` and `http_request` action types
- Full create, update, enable/disable, delete lifecycle from the UI

### Browser Automation
- Playwright integration baked into the runtime
- Live browser view streamed directly in the operator UI
- Sub-agents can be pinned to individual browser tabs

### Integrations
- Telegram: owner DM, group chats, and non-owner private channels with configurable guardrails
- Tool creation via araiOS with operator-defined guardrails
- WebSocket real-time message streaming to the UI

### Operator UI
- Session manager with live chat and full message history
- Memory explorer with tree navigation and inline editing
- Triggers dashboard with detail view and live log tail
- Tools page for inspecting and managing available tools
- Admin panel for logs and system management
- Settings page for API keys, model selection, and configuration
- Telegram integration management
- Light and dark theme

---

## Architecture

```
arais-labs/sentinel
├── apps/
│   ├── backend/sentinel     # Python agent runtime
│   ├── backend/araios       # araiOS backend + centralized auth
│   ├── frontend/sentinel    # React operator UI (Vite + TypeScript + Tailwind + MUI)
│   └── frontend/araios      # araiOS frontend
├── infra/                   # Gateway and Docker wiring
├── scripts/                 # Utility scripts (license reports, etc.)
├── docs/images/             # Product screenshots
├── docker-compose.yml       # Production-style local runtime
└── docker-compose.dev.yml   # Hot reload runtime for all four apps
```

**Frontend stack**: React 18, TypeScript, Vite, Tailwind CSS, MUI v7, Zustand, react-router-dom v6, lucide-react, sonner

**Backend stack**: Python, custom agent loop, WebSocket streaming, Anthropic Claude

**Auth**: Token-based onboarding by default. Bootstrap key rotates to admin + agent keys on first run. Optional OAuth mode.

---

## Quick Start

### Prerequisites
- Docker and Docker Compose
- An Anthropic API key

### Run locally

```bash
git clone https://github.com/arais-labs/sentinel.git
cd sentinel
cp .env.example .env
# Add your ANTHROPIC_API_KEY to .env
docker compose up
```

Open http://localhost:4747 — the gateway will route you to Sentinel or araiOS.

| URL | App |
|---|---|
| http://localhost:4747/ | Login gateway |
| http://localhost:4747/sentinel/ | Sentinel operator UI |
| http://localhost:4747/araios/ | araiOS management UI |
| http://localhost:4747/vnc/ | Live browser view |

### Development (hot reload)

```bash
docker compose -f docker-compose.dev.yml up
```

### CLI

```bash
bash ./sentinel-cli.sh
```

Supports: create/edit instances, start/stop, logs, delete. Multiple isolated local instances via `.instances/<name>.env`.

---

## Why Sentinel

Most agent frameworks are demo-grade. They show tool use in a notebook. They don't show what happens when the session dies, when auth rotates mid-task, when you need to review what the agent actually did.

Sentinel is built for agents that run continuously, remember things, coordinate sub-tasks, and need a human in the loop when it matters. The operator UI is not an afterthought — it's half the product.

If you want to run an agent that can act, and you want to be able to see and control what it does, this is the stack.

---

## License

[AGPL-3.0](./LICENSE)

Built by [ARAIS](https://arais.us)
