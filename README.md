# Sentinel — Autonomous Agent Platform

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL%203.0-blue.svg)](LICENSE)
[![Built by ARAIS](https://img.shields.io/badge/Built%20by-ARAIS-black)](https://arais.us)
[![Docker](https://img.shields.io/badge/Runs%20on-Docker-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/get-docker/)

**Run autonomous agents with real operator controls.**  
Sentinel gives you a production-grade agent runtime, a live operator UI, browser automation,
hierarchical memory, and a full control plane — in one stack you can boot locally in 60 seconds.

---

## What It Looks Like

**Schedule and trigger agents automatically:**
```
You set:   "Run a competitor scan every Monday at 9am and send me the summary."
Sentinel:  Creates the cron trigger. Runs the agent. Stores the result. Notifies you.
           No babysitting. No prompt re-entry. It just runs.
```

**Watch agents work live:**
```
You open:  http://localhost:4747/vnc/
You see:   The agent browsing, clicking, filling forms — in real time.
           Full operator visibility. Pause or intervene at any point.
```

**Gate sensitive actions behind your approval:**
```
Agent wants to:  Send an email on your behalf.
araiOS shows:    "Approve / Deny" in the operator UI.
You click:       Approve.
Agent proceeds:  Only after your explicit sign-off.
```

**Persistent memory across sessions:**
```
Day 1:  You tell the agent your project context, stack, and preferences.
Day 7:  New session. Agent already knows. No re-briefing.
        Hierarchical memory keeps long-running work coherent.
```

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Your Browser                     │
└──────────┬──────────────────────────┬───────────────┘
           │                          │
    ┌──────▼──────┐           ┌───────▼───────┐
    │   Sentinel  │           │    araiOS     │
    │  Operator   │           │  Control Plane│
    │     UI      │           │  (Auth + IAM) │
    └──────┬──────┘           └───────┬───────┘
           │                          │
    ┌──────▼──────────────────────────▼───────┐
    │            Gateway (port 4747)           │
    │   Login · App chooser · JWT session      │
    └──────────────────┬──────────────────────┘
                       │
    ┌──────────────────▼──────────────────────┐
    │          Sentinel Agent Runtime          │
    │  Python runtime · Tools · Memory · VNC  │
    │  Triggers · Browser automation · Tasks  │
    └─────────────────────────────────────────┘
```

**Sentinel** is the runtime and operator interface.  
**araiOS** is the surrounding control plane: auth, permissions, per-action approvals, and coordination.  
They share a single JWT session. One login, both apps.

---

## Features

| Feature | What it means |
|---|---|
| **Operator UI** | Clean interface to manage agents, memory, triggers, and tool approvals |
| **Live browser view** | Watch agent browser sessions in real time via built-in VNC |
| **Hierarchical memory** | Structured, persistent memory that keeps long-running agents coherent across sessions |
| **Triggers** | Webhooks, cron schedules, and scripts all fire agents from one place |
| **Custom Python runtime** | Full control over agent execution logic — not a black-box framework |
| **araiOS tool creation** | Define custom tools, set guardrails, expose only what agents are allowed to do |
| **Per-action approval gates** | Sensitive agent actions require explicit operator sign-off in the UI before executing |
| **Dual credential model** | Agent API keys and admin API keys are separated — agents never hold admin authority |
| **Multi-instance support** | Run multiple isolated Sentinel instances in parallel on one machine |
| **AGPL-3.0 licensed** | Full source, no usage restrictions for self-hosted deployments |

---

## Trust & Security Model

Sentinel separates two credential types by design:

- **Admin API key** — used by the human operator to manage the system
- **Agent API key** — used by the agent to authenticate and act

Agents never hold admin authority. Sensitive tool calls (file writes, external messages,
API mutations) go through **per-action approval gates** in the araiOS UI.
The operator sees the pending action and explicitly approves or denies it before the agent proceeds.

This is not demo safety theater. It is the actual execution model — every action
that crosses a gate boundary blocks until a human responds.

---

## Quick Start

### Prerequisites

- Docker Desktop installed and running

### 1. Boot the stack

```bash
bash ./sentinel-cli.sh
```

The CLI lets you: create an instance · start/stop · view status · tail logs · delete (with volume cleanup).  
Each instance lives in `.instances/<name>.env` and runs as an isolated Compose project.

### 2. Open Sentinel

```
http://localhost:4747/
```

Authenticate with your bootstrap key. On first boot, it rotates credentials and issues:
- one **admin API key**
- one **agent API key**

Store them. They are shown once.

### 3. Pick your app

| URL | What |
|---|---|
| `http://localhost:4747/sentinel/` | Sentinel operator UI |
| `http://localhost:4747/araios/` | araiOS control plane |
| `http://localhost:4747/vnc/` | Live browser view |

---

## Authentication

### Token login (default)

Bootstrap key → first-boot credential rotation → admin key + agent key + shared JWT.  
Both Sentinel and araiOS accept the same JWT.

### OAuth login (optional)

Connect your IdP. After identity validation, the gateway issues the same shared JWT session.  
Token-based onboarding ships by default; OAuth is a deployment-mode switch.

---

## Repo Structure

```
apps/backend/sentinel     Sentinel agent runtime (Python)
apps/frontend/sentinel    Sentinel operator UI
apps/backend/araios       araiOS backend + centralized auth
apps/frontend/araios      araiOS frontend
infra/                    Gateway and Docker wiring
docker-compose.yml        Production-style local runtime
docker-compose.dev.yml    Hot-reload runtime (all four apps)
scripts/                  Utilities (license reports, etc.)
docs/images/              Product screenshots
```

---

## Screenshots

### Sentinel Operator UI
![Sentinel UI](docs/images/sentinel.png)

### araiOS Workspace
![araiOS UI](docs/images/araios.png)

---

## Development

```bash
docker compose -f docker-compose.dev.yml up --build
```

Hot reload for both frontends and both backends.  
Switching between compose files? Run `docker compose down --remove-orphans` first.

---

## Manual Instance Operations

```bash
# Status
docker compose --project-name sentinel-<instance> --env-file .instances/<instance>.env ps

# Logs
docker compose --project-name sentinel-<instance> --env-file .instances/<instance>.env logs -f

# Stop
docker compose --project-name sentinel-<instance> --env-file .instances/<instance>.env down

# Stop + wipe volumes
docker compose --project-name sentinel-<instance> --env-file .instances/<instance>.env down -v
```

---

## Built by ARAIS

ARAIS builds production AI infrastructure for high-growth teams.  
Sentinel is our open runtime. [arais.us](https://arais.us)

---

## License

[AGPL-3.0](LICENSE) · [NOTICE](NOTICE) · [CONTRIBUTING.md](CONTRIBUTING.md)  
Third-party inventory: `bash scripts/generate-license-reports.sh`
