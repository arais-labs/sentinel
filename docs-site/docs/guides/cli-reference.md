---
sidebar_position: 2
title: CLI Reference
---

# Sentinel CLI reference

`sentinel-cli.sh` controls the Docker Compose deployment and manages instances.
A single deployment hosts **multiple logical instances** (one shared stack,
one manager database, a per-instance database for each instance), so most
instance operations target the backend manager API by name rather than spinning
up separate stacks.

The CLI is **interactive** when launched without arguments and **scriptable**
through subcommands. It always runs from the repository root regardless of where
you invoke it.

:::note Desktop app
The macOS desktop app (`apps/desktop/sentinel`, shipped as an unsigned DMG)
manages its own bundled Postgres and backend as child processes and does **not**
use `sentinel-cli.sh`. This reference applies to the Docker Compose deployment.
:::

## Launch

```bash
# Interactive control center
bash ./sentinel-cli.sh

# Local development mode
bash ./sentinel-cli.sh --dev
```

On startup the CLI loads the root `.env`, reconciles required stack settings,
then either shows the menu or runs the requested subcommand. Readiness is probed
with `GET /health/ready`; authenticated manager calls use the `/api/v1` base.

## Commands

All subcommands can be combined with the global flags below (for example
`./sentinel-cli.sh --dev up`).

| Command | What it does |
|---|---|
| `./sentinel-cli.sh` | Opens the interactive control center |
| `./sentinel-cli.sh up` | Starts the shared stack |
| `./sentinel-cli.sh down` | Stops the shared stack |
| `./sentinel-cli.sh restart` | Restarts the shared stack |
| `./sentinel-cli.sh reset [--yes] [--prod-confirm]` | Tears down and recreates the stack (destructive). Prod mode requires both confirmation flags |
| `./sentinel-cli.sh logs [service]` | Follows Compose logs (optionally for one service) |
| `./sentinel-cli.sh status` | Shows Compose status and registered instances |
| `./sentinel-cli.sh instances list` | Lists manager DB instances |
| `./sentinel-cli.sh instances create <name> [display-name]` | Creates an instance: provisions its database, runs the instance schema migrations, and registers it in the manager DB |
| `./sentinel-cli.sh instances rename <old-name> <new-name>` | Renames the manager registry entry |
| `./sentinel-cli.sh instances delete <name>` | Deletes the instance database and registry row (`rm` is accepted as an alias) |
| `./sentinel-cli.sh sessions transcript <session-id> [instance] [--json]` | Exports a session transcript (defaults to the `main` instance; `--json` for machine-readable output) |
| `./sentinel-cli.sh help` | Prints usage |

### Instance names

Instance names are normalized to lowercase alphanumeric plus dashes (1–80
characters). The backing database name is derived from the name plus a short
hash (`sentinel_{safe}_{hash}`) and is **not** renamed when you rename an
instance — see [Current limitations](#current-limitations).

## Global flags

These flags must come before the subcommand:

| Flag | Effect |
|---|---|
| `--dev` | Switch to development mode (equivalent to `SENTINEL_MODE=dev`) |
| `--prod` / `--production` | Force production mode |
| `--compose-file <path>` | Expert override for the Compose file (same as `SENTINEL_COMPOSE_FILE`) |

## Modes

The CLI defaults to **production** mode (`docker-compose.yml`). Development mode
is explicit — run `./sentinel-cli.sh --dev` or set `SENTINEL_MODE=dev` — and
uses `docker-compose.dev.yml`. In dev mode the CLI can write a complete root
`.env` populated with local development defaults; in prod mode it rejects
placeholder/default credentials and requires real values before starting.

## Overrides

The shared stack uses deterministic defaults. The root `.env` is the source of
truth for stack credentials, `COMPOSE_PROJECT_NAME`, and `STACK_PORT`; the CLI
creates or reconciles these before showing the menu or running a subcommand.
The remaining variables are launch/debug knobs typically passed in the shell:

| Variable | Default |
|---|---|
| `COMPOSE_PROJECT_NAME` | `sentinel` |
| `SENTINEL_MODE` | `prod` |
| `SENTINEL_COMPOSE_FILE` | empty; derived from mode |
| `STACK_PORT` | `4747` |
| `SENTINEL_URL` | `http://localhost:$STACK_PORT` |
| `SENTINEL_TOKEN` | empty; interactive login is used when needed |
| `SENTINEL_READY_TIMEOUT` | seconds to wait for backend readiness (default `60`) |
| `SENTINEL_STATUS_TTL` | status cache TTL in seconds |

Changing `COMPOSE_PROJECT_NAME` only changes Compose project metadata and
container or volume names. The Compose default network is explicitly named
`sentinel_default` so Docker runtime containers can join it deterministically.

`SENTINEL_COMPOSE_FILE` is an expert override for stack controls. In normal use,
prefer `SENTINEL_MODE=prod` / `SENTINEL_MODE=dev` or the `--dev` / `--prod`
flags.

## Stack credentials (root `.env`)

The CLI treats the root `.env` as the source of truth for the managed stack and
reconciles missing values on startup. `docker-compose.yml` fails during config
rendering unless these variables are present:

| Variable | Purpose |
|---|---|
| `SENTINEL_POSTGRES_PASSWORD` | Postgres password used by the database and backend |
| `SENTINEL_JWT_SECRET_KEY` | JWT signing secret |
| `SENTINEL_DATA_ENCRYPTION_KEY` | Encryption key for secrets stored in the database (per-instance Runtime credentials, etc.) |
| `SENTINEL_AUTH_USERNAME` | App admin username synced into the manager database on backend startup. Required in both modes; prod rejects defaults |
| `SENTINEL_AUTH_PASSWORD` | App admin password synced into the manager database on backend startup. Required in both modes; prod rejects defaults |

:::note LLM provider credentials are not stack secrets
Provider API keys (Anthropic, OpenAI, Gemini, etc.) are **not** set here and are
no longer read from environment variables. They are configured per instance and
stored encrypted in that instance's database. Configure them in the UI or via
`POST /api/v1/instances/{instance_name}/settings/api-keys`. See
[Multi-instance](./multi-instance.md) and [Installation](./installation.md).
:::

## Interactive control center

Launched without a subcommand, the CLI opens a menu (the "Stack Control
Center") for stack lifecycle, instance management, transcripts, status, and
logs. Navigation:

| Keys | Action |
|---|---|
| `↑`/`↓` or `j`/`k` | Navigate |
| `1`–`9` | Jump to option |
| `Enter` / `Space` | Select |
| `q` or `Esc` | Back / cancel |

Main-menu letter shortcuts jump and select in one keystroke: `u` Start Stack,
`d` Stop Stack, `r` Restart Stack, `x` Reset Stack, `i` Instances,
`t` Transcripts, `s` Status, `l` Logs, `f` Refresh, `e` Exit. Inside the
Instances submenu: `l` List, `c` Create, `r` Rename, `d` Delete, `b` Back.

## Current limitations

- **Renaming an instance does not rename its database.** The derived database
  name is fixed at creation; a rename only updates the manager registry entry.
- **No automatic database cleanup beyond `instances delete`.** Deleting an
  instance drops its database and registry row, but databases left orphaned by
  failed operations must be dropped manually in Postgres.
- **`reset` is destructive.** It recreates the stack and its data; in prod mode
  it requires both `--yes` and `--prod-confirm`.
