---
sidebar_position: 4
title: Multi-Instance
---

# Multi-Instance

Sentinel runs one shared local stack and supports multiple logical instances
inside it. Each logical instance has its own app database, sessions, memory,
modules, permissions, and runtime workspace root.

---

## Use cases

- Separate agents for different clients or projects
- Isolated dev vs. staging data
- Testing different LLM settings side by side
- Running one logical workspace per team member

---

## Setup

Use the CLI to create instances through the backend manager API:

```bash
bash ./sentinel-cli.sh
# Select: Instances
# Select: Create Instance
# Enter instance name: project-alpha
```

Each instance gets a manager registry row, its own Postgres database, and a
workspace root under the shared runtime workspace area.

---

## Managing instances

```bash
./sentinel-cli.sh instances list
./sentinel-cli.sh instances create project-alpha "Project Alpha"
./sentinel-cli.sh instances rename project-alpha alpha
./sentinel-cli.sh instances delete alpha
```

The stack lifecycle remains shared:

```bash
./sentinel-cli.sh up
./sentinel-cli.sh status
./sentinel-cli.sh logs
./sentinel-cli.sh down
```

---

## Isolation model

| Isolated by logical instance | Shared by the stack |
|---|---|
| App database | Compose services |
| Sessions and history | Published HTTP port |
| Memory tree | Manager auth database |
| Modules and permissions | Docker default network |
| Runtime workspace root | Host Docker daemon |

All instances use the same URL origin. Instance selection happens in the UI and
API routes, not through separate ports or generated Compose files.
