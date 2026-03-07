---
sidebar_position: 1
title: Installation
---

# Installation and CLI workflow

This guide explains the real lifecycle implemented by `sentinel-cli.sh`.

---

## Requirements

- Docker available and running
- bash shell with interactive TTY
- git

---

## CLI entrypoint

```bash
bash ./sentinel-cli.sh
```

The CLI is stateful and manages instance configs under:

- `.instances/<instance>.env`

Each instance maps to an isolated compose project:

- project name format: `sentinel-<instance>`

---

## Main menu actions

| Action | What it does |
|---|---|
| New/Edit Instance | Creates or updates `.instances/<instance>.env` then starts instance |
| Start Instance | Starts selected instance with compose |
| Stop Instance | Stops selected instance |
| Reset Auth (Managed Instance) | Rewrites auth username and password hashes in DB |
| Global Status | Shows running service count per instance |
| Tail Logs | `docker compose logs -f` for selected instance |
| Delete Instance | `down -v --remove-orphans` plus remove env file |
| Advanced Mode | Dev mode compose start and custom DB auth management |

---

## New instance creation flow

When you choose **New/Edit Instance**, CLI prompts for:

- instance name
- gateway port
- db name
- db user
- db password
- JWT secret
- admin username
- admin password

Then it writes `.instances/<instance>.env` and starts stack.

---

## What happens on Start Instance

`action_up` does the following in order:

1. `docker compose up --build -d`
2. attempts auth credential seeding in DB
3. attempts bootstrap araiOS agent token creation via APIs
4. seeds cross app URL settings
5. prints onboarding instructions

If auth seed fails, CLI tells you to use Reset Auth.

---

## Auth details

For managed instances, auth reset writes password hash into `system_settings` table keys:

- `sentinel.auth.username`
- `sentinel.auth.password_hash`
- `araios.auth.username`
- `araios.auth.password_hash`

Target can be both apps, Sentinel only, or araiOS only.

---

## Dev mode in Advanced Mode

Advanced mode start uses `docker-compose.dev.yml` and shares Postgres volume name with project.

Use this for local development where you need dev compose behavior.

---

## Common operations

### Start existing instance

1. Run CLI
2. Select **Start Instance**
3. Pick instance

### Rotate credentials

1. Run CLI
2. Select **Reset Auth (Managed Instance)**
3. Choose target app and set new username and password

### Delete instance fully

1. Run CLI
2. Select **Delete Instance**
3. Type `DELETE`

This removes containers, volumes, and instance env file.

---

## Troubleshooting quick checks

- Docker not running -> CLI will fail readiness check
- Port already in use -> CLI warns during create
- Login fails after start -> run Reset Auth action
- araiOS token not auto created -> open manage UI and create token manually
