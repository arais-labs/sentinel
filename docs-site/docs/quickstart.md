---
sidebar_position: 2
title: Quick Start
---

# Quick Start

This gets a fresh Sentinel instance running fast, using the actual `sentinel-cli.sh` flow.

## Prerequisites

- Docker Desktop running
- macOS, Linux, or Windows WSL2
- Interactive terminal with TTY
- git

---

## 1) Clone and enter repo

```bash
git clone https://github.com/arais-labs/sentinel.git
cd sentinel
```

---

## 2) Launch the CLI

```bash
bash ./sentinel-cli.sh
```

You will see the interactive menu.

For first run, choose:

1. **New/Edit Instance**
2. Enter instance config values (or accept defaults)
   - gateway port (default `4747`)
   - Postgres settings
   - JWT secret
   - admin username and password
3. CLI auto starts stack for that instance

What the CLI does during startup:

- runs `docker compose up --build -d`
- seeds auth credentials in DB
- seeds instance URL settings
- prints onboarding instructions with the login target

---

## 3) Open Sentinel

Use the port you configured (default 4747):

- `http://localhost:4747/` Sentinel UI
- `http://localhost:4747/modules` modules UI
- `http://localhost:4747/vnc/` live browser view

---

## 4) Sign in with admin credentials

Sign in using the admin username and password you set during CLI instance creation.

If auth seeding failed, run CLI action:

- **Reset Auth (Managed Instance)**

---

## 5) Validate first run

1. Open Sentinel UI
2. Send a simple message to confirm LLM path
3. Open Modules and confirm modules and permissions load
4. Optionally open VNC page and run one browser action

---

## Next

- [Installation](/guides/installation) for full CLI and instance lifecycle
- [Creating Modules](/guides/creating-modules) to extend capabilities
- [Triggers](/concepts/triggers) to automate recurring runs
