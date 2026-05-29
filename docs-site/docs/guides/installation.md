---
sidebar_position: 1
title: Installation
---

# Installation and CLI workflow

This guide describes the current shared-stack lifecycle implemented by `sentinel-cli.sh`.

## Requirements

- Docker available and running
- bash shell with an interactive TTY
- git

## CLI entrypoint

```bash
bash ./sentinel-cli.sh
```

The CLI starts one shared Compose stack. Instances are registered through the backend manager API and are stored in the manager database, not in generated config files.

The CLI defaults to production mode and uses `docker-compose.yml`. Create a
root `.env` from `.env.example` before starting the default stack:

```bash
cp .env.example .env
```

Then replace the placeholder values for `SENTINEL_POSTGRES_PASSWORD`,
`SENTINEL_JWT_SECRET_KEY`, `SENTINEL_DATA_ENCRYPTION_KEY`, and
`SENTINEL_AUTH_PASSWORD`.

For local development defaults, use explicit dev mode:

```bash
bash ./sentinel-cli.sh --dev
```

## Main menu actions

| Action | What it does |
|---|---|
| Start Stack | Starts the shared Docker Compose stack |
| Stop Stack | Stops the shared Docker Compose stack |
| Restart Stack | Restarts the shared stack |
| Instances | Lists, creates, renames, or deletes manager DB instances |
| Status | Shows stack health and registered instances |
| Logs | Tails Compose logs |

## Instance creation flow

Creating an instance asks for an instance name and optional display name. The backend creates a logical Postgres database for that instance and initializes the app schema there.

The CLI does not ask for extra ports, generated database credentials, JWT secrets, or generated runtime configuration files.

## Auth details

Login is global. Auth settings and token revocation live in the manager database so users can authenticate before selecting an instance. The default username is `admin` unless `SENTINEL_AUTH_USERNAME` is set.
