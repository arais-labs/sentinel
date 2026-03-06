---
sidebar_position: 3
title: Memory
---

# Memory

Sentinel uses a **hierarchical memory model** — structured like a tree rather than a flat list of facts. This keeps agents coherent across long-running sessions without context overload.

---

## Structure

Memory is a tree of nodes:

```
Root node (domain/project anchor)
├── Child node (subtopic or workstream)
│   ├── Grandchild (granular detail)
│   └── Grandchild
└── Child node
```

Each node has:

| Field | Description |
|---|---|
| `title` | Short label |
| `content` | Full stored text |
| `summary` | Short description for retrieval indexing |
| `category` | `core`, `preference`, `project`, or `correction` |
| `importance` | Numeric weight for retrieval ranking |
| `pinned` | Pinned nodes inject into every turn |

---

## Pinned vs. non-pinned

| Type | Behavior |
|---|---|
| **Pinned** | Injected into every agent turn — always in context |
| **Non-pinned** | Retrieved on demand via semantic + keyword search |

Use pinned nodes for stable, high-priority anchors (identity, critical constraints). Use non-pinned nodes for domain knowledge, project details, and history.

---

## Retrieval

Non-pinned memory is retrieved using hybrid semantic + keyword ranking. The agent searches memory at the start of each turn and expands relevant branches as needed — it doesn't load the full tree every time.

---

## Categories

| Category | Use for |
|---|---|
| `core` | Identity, roles, stable constraints |
| `preference` | User preferences, style, behavior settings |
| `project` | Project state, plans, artifacts |
| `correction` | Explicit corrections to agent behavior |

---

## Memory hygiene

Agents manage memory proactively — storing new context, updating stale nodes, and reorganizing structure when needed. Operators can inspect and edit the memory tree directly in the Sentinel UI.
