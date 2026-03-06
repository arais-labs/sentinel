---
sidebar_position: 4
title: Multi-Instance
---

# Multi-Instance

Sentinel supports running multiple isolated instances locally. Each instance has its own config, memory, agent state, and port.

---

## Use cases

- Separate agents for different projects or clients
- Isolated dev vs. staging environments
- Running parallel experiments with different LLM configs

---

## Setup

The `sentinel-cli.sh` tool manages multiple instances via named configs.

```bash
bash ./sentinel-cli.sh
# Select: Create config
# Enter instance name: e.g. "project-alpha"
```

Each instance gets:
- Its own `.env` file
- Its own Docker volume for memory and state
- Its own port offset (e.g. 4747, 4748, 4749...)

---

## Managing instances

```bash
bash ./sentinel-cli.sh
# Select instance from list → Start / Stop / Logs / Destroy
```

Instances are fully isolated — one instance's memory and tools don't affect another.
