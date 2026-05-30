---
sidebar_position: 1
title: Installation
---

# Installation

Sentinel runs as a single shared stack (Postgres + backend + frontend) that hosts
**multiple logical instances**. You install the stack once; instances are created
afterwards through the manager API or CLI and stored in the manager database — there
are no per-instance ports or per-instance Compose files. See
[Multi-instance](./multi-instance.md) for the architecture details.

There are two supported ways to run Sentinel:

- **Docker Compose stack**, driven by `sentinel-cli.sh` (recommended for servers and
  local development).
- **macOS desktop app**, an Electron build that bundles Postgres and runs the backend
  as a managed child process (no Docker required).

## Docker Compose stack

### Requirements

- Docker available and running
- bash shell with an interactive TTY
- git

### CLI entrypoint

```bash
bash ./sentinel-cli.sh
```

The CLI starts one shared Compose stack and shows an interactive menu. It defaults to
production mode and uses `docker-compose.yml`. The stack publishes a single port
(`STACK_PORT`, default `4747`); the frontend proxies API calls to the backend under
`/api/v1`. All instances are reached through that one origin — instance selection
happens in the UI and in the API route path, not through separate ports.

### Configure the root `.env`

The root `.env` is the source of truth for stack credentials. Production mode refuses
placeholder or default values, so create `.env` from the template and replace every
placeholder before starting:

```bash
cp .env.example .env
```

Replace these required infrastructure secrets:

| Variable | Purpose |
|---|---|
| `SENTINEL_POSTGRES_PASSWORD` | Postgres password for the shared cluster |
| `SENTINEL_JWT_SECRET_KEY` | Signing key for auth tokens |
| `SENTINEL_DATA_ENCRYPTION_KEY` | Key that encrypts secrets stored in the database (e.g. SSH runtime credentials) |
| `SENTINEL_AUTH_USERNAME` | Admin login username |
| `SENTINEL_AUTH_PASSWORD` | Admin login password |

`COMPOSE_PROJECT_NAME` (default `sentinel`) and `STACK_PORT` (default `4747`) are also
read from `.env`. The CLI creates or reconciles `.env` on startup before showing the
menu.

To target an external Postgres instead of the bundled `postgres` service, set the
optional `SENTINEL_DATABASE_*` variables in `.env`. Containers reaching a host-running
Postgres usually need `host.docker.internal` rather than `127.0.0.1`.

:::info LLM provider credentials are not env vars
Provider keys (Anthropic, OpenAI, Gemini, etc.) are **not** read from the environment.
Each instance stores its provider credentials encrypted in its own database, and you
configure them per instance through the UI or API after the stack is up. Legacy LLM
environment variables are ignored and trigger a warning at startup. See
[Multi-instance](./multi-instance.md).
:::

### Development mode

For local development defaults, use explicit dev mode. This uses
`docker-compose.dev.yml` instead of the production Compose file:

```bash
bash ./sentinel-cli.sh --dev
```

### Main menu actions

| Action | What it does |
|---|---|
| Start Stack | Starts the shared Docker Compose stack |
| Stop Stack | Stops the shared Docker Compose stack |
| Restart Stack | Restarts the shared stack |
| Reset Stack | Tears down the stack and its data (destructive; prod requires confirmation) |
| Instances | Lists, creates, renames, or deletes manager DB instances |
| Transcripts | Exports a session transcript |
| Status | Shows stack health and registered instances |
| Logs | Tails Compose logs (optionally for one service) |

Most actions are also available non-interactively, e.g. `sentinel-cli.sh up`,
`sentinel-cli.sh down`, `sentinel-cli.sh status`, and the `instances` /
`sessions transcript` subcommands. See [CLI reference](./cli-reference.md) for the
full command surface.

### Instance creation flow

Creating an instance asks for an instance name and optional display name. The backend
normalizes the name (lowercase alphanumeric and dashes, 1–80 characters), creates a
dedicated logical Postgres database for that instance, runs the instance schema
migrations, and initializes defaults. If creation fails, the instance row is rolled
back and the database is dropped.

The CLI does not ask for extra ports, generated database credentials, JWT secrets, or
generated runtime configuration files. After an instance exists, configure its LLM
provider credentials and (optionally) a runtime target through the UI or API.

:::warning No automatic instance database cleanup on delete
Deleting an instance removes its manager-database record but does not currently drop
the underlying Postgres database. If you need the storage reclaimed, drop the
`sentinel_<name>_<hash>` database manually. Renaming an instance also keeps the original
database name — the database is not renamed.
:::

### Auth details

Login is global. Auth settings and token revocation live in the manager database so
users can authenticate before selecting an instance. The admin username and password
come from `SENTINEL_AUTH_USERNAME` / `SENTINEL_AUTH_PASSWORD` in `.env`.

In server/Compose mode, `.env` is the source of truth: the backend re-reads these
values on every startup and syncs the password hash into the manager database. The
in-app password-change endpoint is disabled in this mode (returns 404) — rotate the
password by editing `.env` and restarting.

## macOS desktop app

The desktop app is an Electron shell for running Sentinel locally on macOS. The current
build targets **Apple Silicon (arm64) only** and ships as an **unsigned development
DMG**, suitable for local testing.

It bundles Postgres + pgvector and runs the FastAPI backend as a managed local child
process — **no Docker required**. Pinned runtime versions (Postgres, pgvector, Python)
live in `runtime.lock.json`. Mutable data (instance config/env, Postgres data,
workspaces, backups) is stored under Electron's app support directory.

The current release is version **0.1.0**: `Sentinel-0.1.0-arm64.dmg`. Because the DMG is
unsigned, macOS Gatekeeper will warn on first launch.

To build the DMG yourself from `apps/desktop/sentinel/`:

```bash
npm run desktop:build -- --target macos-arm64
```

If `--target` is omitted, the build uses the current platform. The final DMG is written
to `apps/desktop/sentinel/dist/`. See the desktop app README
(`apps/desktop/sentinel/README.md`) for verification, payload/service management, and
distribution details.

:::warning Desktop platform support
The desktop build is macOS Apple Silicon only — there is no x86_64, Linux, or Windows
build today. Bundled runtimes are vendored, so runtime updates require a rebuild. Code
signing and notarization are not yet implemented.
:::

## Current limitations

- The instance session registry has no idle eviction beyond roughly 30 instances; many
  created-but-unused instances can exhaust the Postgres connection pool.
- LLM provider credentials must be set per instance via the UI/API after install;
  environment variables are not supported.
