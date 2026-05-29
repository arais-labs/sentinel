---
sidebar_position: 2
title: CLI Reference
---

# Sentinel CLI reference

This page maps the CLI menu and commands to `sentinel-cli.sh` behavior.

## Launch

```bash
bash ./sentinel-cli.sh
```

The CLI uses one Compose project by default and talks to the backend manager API for instance operations.

Readiness checks use `GET /health/ready`. Authenticated manager calls use the
`/api/v1` API base.

## Commands

| Command | What it does |
|---|---|
| `./sentinel-cli.sh up` | Starts the shared stack |
| `./sentinel-cli.sh down` | Stops the shared stack |
| `./sentinel-cli.sh restart` | Restarts the shared stack |
| `./sentinel-cli.sh logs [service]` | Follows Compose logs |
| `./sentinel-cli.sh status` | Shows Compose status and registered instances |
| `./sentinel-cli.sh instances list` | Lists manager DB instances |
| `./sentinel-cli.sh instances create <name> [display-name]` | Creates an instance database and registry row |
| `./sentinel-cli.sh instances rename <old-name> <new-name>` | Renames the manager registry entry |
| `./sentinel-cli.sh instances delete <name>` | Deletes the instance database and registry row |

## Overrides

The shared stack uses deterministic defaults. Process-level overrides are available for launch/debug only:

| Variable | Default |
|---|---|
| `COMPOSE_PROJECT_NAME` | `sentinel` |
| `SENTINEL_MODE` | `prod` |
| `SENTINEL_COMPOSE_FILE` | empty; derived from mode |
| `STACK_PORT` | `4747` |
| `SENTINEL_URL` | `http://localhost:$STACK_PORT` |
| `SENTINEL_TOKEN` | empty; interactive login is used when needed |

The CLI reconciles the managed stack settings from simple `KEY=value` entries
in the root `.env`. Shell variables are still useful for launch/debug knobs such
as `SENTINEL_MODE`, `SENTINEL_COMPOSE_FILE`, `SENTINEL_URL`, and
`SENTINEL_TOKEN`, but the stack credentials are written to
and reloaded from `.env`.

Changing `COMPOSE_PROJECT_NAME` only changes Compose project metadata and
container or volume names. The Compose default network is explicitly named
`sentinel_default` so Docker runtime containers can join it deterministically.

The CLI defaults to production mode and uses `docker-compose.yml`. Dev mode is
explicit: run `./sentinel-cli.sh --dev` or set `SENTINEL_MODE=dev`; that path
uses `docker-compose.dev.yml` and can write a complete root `.env` with local
dev defaults.

`SENTINEL_COMPOSE_FILE` is an expert override for stack controls. In normal use,
prefer `SENTINEL_MODE=prod` or `SENTINEL_MODE=dev`.

## Production compose secrets

The CLI treats the root `.env` as the source of truth for the managed stack. On
startup it creates or reconciles missing values before showing the menu or
running subcommands. `docker-compose.yml` fails during config rendering unless
these variables are present:

| Variable | Purpose |
|---|---|
| `SENTINEL_POSTGRES_PASSWORD` | Postgres password used by the database and backend |
| `SENTINEL_JWT_SECRET_KEY` | JWT signing secret |
| `SENTINEL_DATA_ENCRYPTION_KEY` | Encryption key for runtime secrets stored in the database |
| `SENTINEL_AUTH_PASSWORD` | App admin password synced into the manager database on backend startup |
| `SENTINEL_AUTH_USERNAME` | App admin username synced into the manager database on backend startup |
