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

1. Let the CLI create or reconcile the root `.env`. Prod mode proposes
   generated values and rejects placeholders/default credentials. Dev mode is
   explicit via `./sentinel-cli.sh --dev` and may write local dev defaults.
2. Choose **Start Stack**
3. Choose **Instances** -> **Create Instance**
4. Create a logical instance, for example `main`

What the CLI does during startup:

- runs `docker compose up --build -d`
- waits for `GET /health/ready`
- creates and manages logical instances through the manager API

---

## 3) Open Sentinel

Use the port you configured (default 4747):

- `http://localhost:4747/` Sentinel UI
- `http://localhost:4747/modules` modules UI
- `http://localhost:4747/vnc/` live browser view

---

## 4) Sign in with admin credentials

Sign in with the admin username and password from the root `.env`.

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
