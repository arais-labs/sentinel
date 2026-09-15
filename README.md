<p align="center">
  <img src="docs-site/static/img/brandmark.png" alt="Sentinel logo" width="120" />
</p>

<h1 align="center">Sentinel</h1>
<p align="center"><strong>An AI workspace for getting work done.</strong></p>
<p align="center">
  <a href="https://sentinel.arais.us">Documentation</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="SECURITY.md">Security</a> ·
  <a href="LICENSE">AGPL-3.0</a>
</p>

Sentinel brings chat, a live desktop, terminals, files, browser automation, and
agent tools into one desktop app. Work in a Linux workspace on your Mac or connect
to a remote machine over SSH. Follow the agent's work, steer it while it runs, and
review actions that need your approval.

Built by [ARAIS](https://arais.us).

## What you can do

| Capability | What it provides |
| --- | --- |
| Workspaces | Persistent files and installed tools, a shared project folder, configurable resources, and Linux distribution selection. |
| Desktop and terminals | Interact with the same workspace the agent uses, with live desktop viewing and tmux terminals. |
| Files | Browse, edit, upload, and download workspace files. Preview supported documents and media inline. |
| Agents | Stream responses, steer ongoing work, delegate to sub-agents, and resume conversations. |
| Modules | Built-in tools and custom modules for actions, integrations, and structured records. |
| Approvals | Allow, request approval, or deny module actions; grant approval for a session when appropriate. |
| Memory and automation | Persistent memory, scheduled tasks, and event-driven triggers. |
| Telegram | Chat with Sentinel, switch sessions, and steer an active turn from Telegram. |

Connect Anthropic, OpenAI, or Gemini in Settings using the supported API-key or
OAuth options. Provider credentials and settings belong to each instance.

## Start developing

On macOS, install Xcode Command Line Tools and Homebrew, then run:

```bash
make setup
make dev
```

Electron starts the local backend and frontend. Complete onboarding, configure a
model provider, and create a workspace on this computer or an SSH machine.
Frontend and backend changes reload during development. Quit Electron or press
Ctrl+C to stop the app; stored data is preserved.

See the [installation guide](docs-site/docs/guides/installation.md) for setup,
[Contributing](CONTRIBUTING.md) for development, and the
[desktop README](apps/desktop/sentinel/README.md) for packaging.

## How it fits together

```text
Desktop app / Telegram / scheduled triggers
                    │
             Sentinel backend
             ├── Conversations and agent execution
             ├── Providers, memory, and sub-agents
             ├── Modules, approvals, and automation
             └── Workspace connections
                    │
        Local or remote Linux workspace
        └── Desktop · terminals · browser · files · tools
```

A deployment can host multiple instances with separate databases, conversations,
provider settings, and module configuration. Machines are shared across instances;
multiple conversations can attach to the same workspace.

On Apple Silicon, the workspace runtime uses Apple Containerization. SSH connects
the app to a remote machine running that runtime. Project folders are shared with
the workspace, and its other files and installed tools live on persistent storage.
See [workspace execution](docs-site/docs/guides/runtime-exec-security.md) for the
execution boundary and paths.

## Repository guide

| Location | Responsibility |
| --- | --- |
| [Backend](apps/backend/sentinel) | Agent integration, instances, modules, storage, and workspace orchestration. |
| [Frontend](apps/frontend/sentinel) | Chat, workspace panes, settings, and approvals. |
| [Desktop](apps/desktop/sentinel) | Electron shell, local services, native runtime, and packaging. |
| [Shared runtime](packages/sentral) | Reusable agent engine, model providers, and shared tools. |
| [Standalone TUI](apps/tui) | Terminal chat using Sentral with local command execution and HTTP tools. |
| [Documentation](docs-site) | Product guides and reference documentation. |
| [Scripts](scripts) | Development setup and project tooling. |

The standalone TUI has its own entry point and configuration. It uses the shared
agent engine without starting the desktop app or its workspace services.

## Controls and security

Module permissions determine whether an action executes, requests approval, or is
blocked. Session approvals apply to that session; they do not change the global
policy. Emergency-stop controls can interrupt agent execution.

Custom module Python runs in the backend, separately from workspace command
execution. Review code and permissions before enabling a custom module. An
instance is a data/configuration boundary, not a separate operating-system process.

Read [approvals](docs-site/docs/concepts/approvals.md),
[modules and permissions](docs-site/docs/concepts/modules-and-permissions.md), and
[SECURITY.md](SECURITY.md) for details.

## Documentation and checks

- [Quickstart](docs-site/docs/quickstart.md)
- [Creating modules](docs-site/docs/guides/creating-modules.md)
- [Telegram](docs-site/docs/guides/telegram.md)
- [API reference](docs-site/docs/reference/api.md)
- [Backend development](apps/backend/sentinel/README.md)

Run `make check` for formatting, lint, backend and desktop tests, and TypeScript
checks. See each component's README for its additional commands.

## License

GNU AGPL-3.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
