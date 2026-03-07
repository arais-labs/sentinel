---
sidebar_position: 4
title: Memory
---

# Memory

Sentinel uses a **hierarchical persistent memory model**. Memory is structured as a tree rather than a flat list. This keeps agents coherent across long-running sessions without loading everything into context on every turn.

---

## Structure

Memory is a tree of nodes. Each node can have children, and children can have children.

```
Root node (domain anchor)
├── Child node (subtopic or workstream)
│   ├── Grandchild (granular detail)
│   └── Grandchild
└── Child node
```

Each node has the following fields:

| Field | Description |
|---|---|
| `title` | Short label for the node |
| `content` | Full stored text |
| `summary` | Short description used in retrieval indexing |
| `category` | `core`, `preference`, `project`, or `correction` |
| `importance` | Numeric weight for retrieval ranking |
| `pinned` | If true, node is injected into every turn |

---

## Pinned vs non-pinned

This distinction directly affects how much context the agent has on each turn.

| Type | Behavior |
|---|---|
| **Pinned** | Full content injected into every agent turn — always present |
| **Non-pinned** | Retrieved on demand via search — only included when relevant |

**Use pinned nodes for:** agent identity, critical constraints, user profile, long-lived preferences.

**Use non-pinned nodes for:** project history, domain knowledge, event logs, and anything that is only sometimes relevant.

:::warning Token budget
Each pinned node's full content is included in every turn. Pinning too many large nodes will fill the context window and silently reduce how much conversation history and retrieved memory the agent can use. Keep pinned nodes lean and summary-level.
:::

---

## Retrieval pipeline

When the agent searches non-pinned memory, it runs a hybrid pipeline with automatic fallbacks:

```
1. Vector search (cosine similarity on embeddings)
       ↓ if embeddings not configured or empty
2. Keyword search (PostgreSQL tsvector full-text)
       ↓ if no results
3. Substring scan (Python-level string matching)
       ↓ if still no results
4. Most recent (returns most recently accessed nodes)
```

:::important
Fallback 4 (most recent) always returns results — even if the query is completely unrelated to the returned nodes. If you see the agent citing oddly irrelevant memory context, this is likely the cause. Configuring an embedding provider eliminates this problem.
:::

If no embedding provider is configured, the pipeline starts at step 2. Retrieval still works but relevance quality degrades for semantic queries.

---

## System memories

Two memory nodes are protected and cannot be deleted or overwritten via the API:

| Key | Content |
|---|---|
| `agent_identity` | The agent's core identity and behavior instructions |
| `user_profile` | The user's profile and preferences |

These are always present and always pinned. Attempts to delete them are silently ignored.

---

## Categories

| Category | Use for |
|---|---|
| `core` | Identity, roles, stable constraints |
| `preference` | User preferences, communication style, behavior settings |
| `project` | Project state, plans, artifacts, workstreams |
| `correction` | Explicit corrections to agent behavior or past mistakes |

Categories are used both for organization and as filters in search. You can search within a specific category to improve precision.

---

## Context injection order

On each turn, the agent injects memory in this order:

1. All pinned nodes (full content, in importance order)
2. Non-pinned root summaries (for orientation)
3. Relevant auto-expanded branches from search results

The agent controls which non-pinned branches to expand via `memory_get_node` and `memory_list_children` calls during a turn.

---

## Editing memory

Operators can inspect and edit the full memory tree in the Sentinel UI under **Memory**. You can:

- Browse the tree structure
- Edit node content directly
- Delete nodes
- Pin or unpin nodes

Agents also manage memory autonomously — storing new context, updating stale nodes, merging duplicates, and reorganizing structure when it becomes crowded.
