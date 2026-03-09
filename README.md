<p align="center">
  <img src="docs/logo.png" alt="Sentinel logo" width="120" />
</p>

<h1 align="center">Sentinel</h1>
<p align="center"><strong>One autonomous agent. Full execution stack.</strong></p>

<p align="center">
  <a href="https://github.com/arais-labs/sentinel/blob/main/LICENSE"><img alt="License: AGPL-3.0" src="https://img.shields.io/badge/license-AGPL--3.0-blue.svg"></a>
  <img alt="Deployment" src="https://img.shields.io/badge/deploy-Docker%20Compose-2496ED">
  <a href="https://github.com/arais-labs/sentinel/commits/main"><img alt="Last commit" src="https://img.shields.io/github/last-commit/arais-labs/sentinel?branch=main"></a>
  <a href="https://github.com/arais-labs/sentinel/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/arais-labs/sentinel"></a>
  <a href="https://github.com/arais-labs/sentinel/network/members"><img alt="Forks" src="https://img.shields.io/github/forks/arais-labs/sentinel"></a>
  <a href="https://github.com/arais-labs/sentinel/issues"><img alt="Open issues" src="https://img.shields.io/github/issues/arais-labs/sentinel"></a>
  <a href="https://github.com/arais-labs/sentinel/pulls"><img alt="Open pull requests" src="https://img.shields.io/github/issues-pr/arais-labs/sentinel?label=open%20PRs"></a>
  <img alt="Top language" src="https://img.shields.io/github/languages/top/arais-labs/sentinel">
</p>

Sentinel is a self hosted AI operator that turns intent into execution.
It combines an agent runtime, browser automation, scheduling, memory, approvals, and tool access in one product.

Built by [ARAIS](https://arais.us).

## Quick links

- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [What Sentinel can do](#what-sentinel-can-do)
- [Documentation](#documentation)
- [Security model](#security-model)
- [Contributing](#contributing)

## What Sentinel can do

- Run multi step tasks with tool calls and recovery.
- Use a real browser with Playwright and live VNC monitoring.
- Execute scheduled runs with cron or heartbeat triggers.
- Keep persistent hierarchical memory across sessions.
- Delegate bounded work to sub agents.
- Gate risky actions behind human approvals.
- Connect to custom araiOS modules for data and actions.
- Run git operations freely and only gate at push or PR creation, so the agent moves fast without surprise commits to main.
- Authenticate with your existing Claude Code or Codex CLI OAuth token, no extra API subscription needed.

## Architecture

```text
User / Telegram / Trigger
        ↓
  Sentinel UI  ↔  araiOS Workspace
        ↓
  Agent Runtime (Python)
  ├── Context builder (memory + history)
  ├── LLM provider (Anthropic / OpenAI / failover)
  ├── Tool adapter (araiOS + browser + runtime + git)
  ├── Approval gate (pause/resume on sensitive actions)
  └── Estop service (freeze or kill execution at any depth)
        ↓
  araiOS Control Plane
  ├── Custom tool modules (sandboxed Python)
  ├── Data modules (persistent record stores)
  ├── Permissions (allow / approval / deny per action)
  └── Approval queue (async human review)
        ↓
  Browser + External APIs + Git
```

## Quick Start

### 1) Clone

```bash
git clone https://github.com/arais-labs/sentinel.git
cd sentinel
```

### 2) Launch Sentinel CLI

```bash
bash ./sentinel-cli.sh
```

For first run:

1. Choose `New/Edit Instance`
2. Set instance values or accept defaults
3. Let CLI start services and seed auth

### 3) Open gateway

Default URLs:

- `http://localhost:4747/` gateway
- `http://localhost:4747/sentinel/` Sentinel
- `http://localhost:4747/araios/` araiOS
- `http://localhost:4747/vnc/` live browser monitor

### 4) Sign in

Use the admin username and password you configured in CLI.
If login fails, run `Reset Auth (Managed Instance)` from CLI and retry.

## Installation paths

### Recommended

- Use `sentinel-cli.sh` for instance lifecycle, auth seeding, startup, status, logs, and cleanup.

### Manual compose

```bash
cp .env.example .env
docker compose up --build -d
```

### Dev mode

```bash
docker compose -f docker-compose.dev.yml up --build
```

## Repository layout

- `apps/backend/sentinel` Sentinel backend
- `apps/frontend/sentinel` Sentinel frontend
- `apps/backend/araios` araiOS backend
- `apps/frontend/araios` araiOS frontend
- `infra/` gateway and runtime wiring
- `docs-site/` full documentation source
- `docs/` project notes and assets

## Documentation

- Docs site source: [`docs-site/`](docs-site)
- Intro: [`docs-site/docs/introduction.md`](docs-site/docs/introduction.md)
- Quickstart: [`docs-site/docs/quickstart.md`](docs-site/docs/quickstart.md)
- Installation guide: [`docs-site/docs/guides/installation.md`](docs-site/docs/guides/installation.md)
- CLI reference: [`docs-site/docs/guides/cli-reference.md`](docs-site/docs/guides/cli-reference.md)
- API reference: [`docs-site/docs/reference/api.md`](docs-site/docs/reference/api.md)

## Security model

Sentinel uses explicit policy based controls through araiOS:

- `allow` executes immediately
- `approval` pauses and requests human review
- `deny` blocks action

High risk actions can be reviewed before execution.
Emergency stop levels can freeze active execution when needed.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## Runtime Exec Security Model

`runtime_exec` supports two explicit modes:

- `privilege=user` (default): confined execution with write access limited to the session workspace and runtime temp mounts
- `privilege=root`: unconfined execution, approval-gated before command execution

For long-running commands, use `detached=true`.
Inline timeout results include a detached-mode hint.

## License

GNU AGPL-3.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
