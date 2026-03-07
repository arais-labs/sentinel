---
sidebar_position: 4
title: Multi-Instance
---

# Multi-Instance

Sentinel supports running multiple isolated instances on the same machine. Each instance has its own config, memory, agent state, tools, and port.

---

## Use cases

- Separate agents for different clients or projects
- Isolated dev vs. staging environments
- Testing different LLM configurations side-by-side
- Running one instance per team member

---

## Setup

The `sentinel-cli.sh` tool manages multiple instances via named configs.

```bash
bash ./sentinel-cli.sh
# Select: Create config
# Enter instance name: e.g. "project-alpha"
```

Each instance gets:

- Its own named `.env` file
- Its own Docker volumes (memory, database, state)
- Its own port offset (4747, 4748, 4749, ...)

---

## Managing instances

```bash
bash ./sentinel-cli.sh
# Select instance from the list
# Then: Start / Stop / Logs / Destroy
```

---

## Isolation guarantees

| What is isolated | Shared |
|---|---|
| Memory tree | — |
| Agent sessions and history | — |
| araiOS modules and permissions | — |
| LLM API keys (per config) | — |
| Docker volumes | — |
| — | Host machine resources (CPU, RAM) |
| — | Docker daemon |

One instance's memory, tools, and agent state cannot affect another instance.

---

## Port allocation

Instances use sequential port offsets from the base port (4747 by default). If you run three instances:

| Instance | Port |
|---|---|
| default | 4747 |
| project-alpha | 4748 |
| project-beta | 4749 |

Each instance has the same URL structure (`/`, `/sentinel/`, `/araios/`, `/vnc/`) on its own port.
