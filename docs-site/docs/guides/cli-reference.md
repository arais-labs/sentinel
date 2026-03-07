---
sidebar_position: 2
title: CLI Reference
---

# Sentinel CLI reference

This page maps the CLI menu directly to `sentinel-cli.sh` behavior.

---

## Launch

```bash
bash ./sentinel-cli.sh
```

The CLI is interactive and uses TTY controls for menu navigation.

Instance config files are stored in:

- `.instances/<instance>.env`

Each instance uses compose project name:

- `sentinel-<instance>`

---

## Main menu actions

| Menu item | Internal action | What it does |
|---|---|---|
| New/Edit Instance | `action_create` | creates or overwrites instance env, then starts instance |
| Start Instance | `action_up` | starts selected instance and runs startup seeding flow |
| Stop Instance | `action_down` | `docker compose down` for selected instance |
| Reset Auth (Managed Instance) | `action_reset_auth_managed` | rewrites auth settings in DB |
| Global Status | `action_list` | shows service count per instance |
| Tail Logs | `action_logs` | follows compose logs |
| Delete Instance | `action_delete` | `down -v --remove-orphans` plus env deletion |
| Advanced Mode | `action_advanced_mode` | dev mode start and custom DB auth path |

---

## New instance flow

`action_create` prompts for:

- instance name
- gateway port
- db name
- db user
- db password
- JWT secret
- admin username
- admin password

Then it writes `.instances/<instance>.env` with core values and calls `action_up`.

---

## Start flow details

`action_up` does more than compose up.

1. `docker compose up --build -d`
2. tries managed auth seed into `system_settings`
3. tries bootstrap araiOS agent token creation via API
4. seeds cross app URL settings
5. prints onboarding block with URLs and token hints

If API based cross app seeding fails, it falls back to DB seeding.

---

## Auth reset behavior

Managed reset writes these keys:

Sentinel keys:

- `sentinel.auth.username`
- `sentinel.auth.password_hash`

araiOS keys:

- `araios.auth.username`
- `araios.auth.password_hash`

You can target both, Sentinel only, or araiOS only.

Password hash generation uses PBKDF2 SHA256 with random salt.

---

## Advanced mode

Advanced menu includes:

1. Start instance in dev mode
   - uses `docker-compose.dev.yml`
2. Manage custom instance auth
   - direct Postgres connection inputs
   - writes same auth setting keys into custom DB

---

## Safety checks and prompts

CLI performs:

- docker readiness check
- port occupancy warning on create
- delete confirmation requiring literal `DELETE`
- instance picker for all actions touching existing instances

---

## Useful operator sequence

For a fresh machine:

1. New/Edit Instance
2. Start Instance
3. Open gateway URL printed by CLI
4. Sign in with admin credentials you entered
5. If login fails run Reset Auth and retry

For routine ops:

- Start Instance
- Global Status
- Tail Logs
- Stop Instance

