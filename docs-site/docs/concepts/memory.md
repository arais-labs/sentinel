---
sidebar_position: 4
title: Memory
---

# Memory

Sentinel uses a **hierarchical persistent memory model**. Memory is structured as a tree rather than a flat list, so agents stay coherent across long-running sessions without loading everything into context on every turn.

Memory is **scoped to the instance**. Each Sentinel instance has its own application database, and the `memories` table lives there — so memory never crosses between instances. (See [Multi-instance](../guides/multi-instance.md) for how instances are isolated.)

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
| `title` | Short label for the node (optional) |
| `content` | Full stored text |
| `summary` | Short description used for the non-pinned root index and retrieval (optional) |
| `category` | `core`, `preference`, `project`, or `correction` |
| `importance` | Integer weight `0`–`100` used for retrieval/injection ranking |
| `pinned` | If true, a **root** node is injected into every turn (see below) |
| `parent_id` | Parent node, or null for a root |
| `embedding` | Optional 384-dimension local vector for semantic search |

---

## Pinned vs non-pinned

This distinction directly affects how much context the agent has on each turn.

| Type | Behavior |
|---|---|
| **Pinned root** | Full content injected into every agent turn — always present |
| **Non-pinned root** | Listed in a summary index every turn; full content retrieved on demand |
| **Child nodes** | Reached by expanding a branch (the agent reads them via tool calls) |

A node is auto-injected in full only when it is **both pinned and a root** (no parent). Non-pinned roots appear every turn as a one-line index entry (title + summary + importance) so the agent knows they exist and can drill in. Child nodes are not listed automatically; the agent walks into them with `tree`, `get_node`, and `list_children`.

**Use pinned roots for:** agent identity, critical constraints, user profile, long-lived preferences.

**Use non-pinned nodes for:** project history, domain knowledge, event logs, and anything that is only sometimes relevant.

:::warning Token budget
Each pinned root's full content is included in every turn. Pinning too many large roots will fill the context window and silently reduce how much conversation history and retrieved memory the agent can use. Keep pinned roots lean and summary-level.
:::

---

## Context injection order

On each turn, the agent injects root memory in this order, ranked by `(pinned, importance, recency)` descending:

1. **Pinned roots** — full content, highest-importance first.
2. **Non-pinned root index** — a compact list of every non-pinned root (id, title, summary, importance) for orientation.
3. **Expanded branches** — children/lineage the agent pulls in during the turn.

The agent controls which non-pinned branches to expand by calling the memory tool's `tree`, `get_node`, and `list_children` actions during a turn. Branch expansion includes lineage back to the root plus direct children, so the agent gets enough surrounding context to act on a node.

---

## Retrieval pipeline

When the agent searches memory (the `search` action), it runs a hybrid pipeline with automatic fallbacks:

```
1. Vector search (sqlite-vec cosine similarity)   ← only if an embedding service is active
       +
2. Keyword search (SQLite FTS5 full-text)
       ↓ results from 1 + 2 merged via Reciprocal Rank Fusion (RRF)
       ↓ if the merge is empty
3. Substring scan (Python-level string matching)
       ↓ if still empty
4. Most recent (returns most recently created nodes)
```

When an embedding service is active, vector and keyword results are combined with Reciprocal Rank Fusion. When no embedding service is active, the pipeline skips vector search and starts at keyword search, then falls through substring and recent.

:::important
The final fallback (most recent) **always returns results** — even if the query is unrelated to the returned nodes. If you see the agent citing oddly irrelevant memory context, this is likely the cause.
:::

:::note Local embeddings — no API key
Sentinel runs `BAAI/bge-small-en-v1.5` on CPU through FastEmbed. The model downloads once into `<storage_root>/models/embeddings`; subsequent inference uses cached files. Memory text is not sent to an embedding API. Inference runs off the server event loop, and model initialization is shared across instances; each instance's stored memories remain isolated.

New and edited memories are embedded automatically. Startup backfill populates missing vectors and rebuilds embeddings with an unknown or different model identifier. Long memories are split into overlapping chunks and pooled into a normalized vector. If model loading fails (for example, an offline first launch), text storage and keyword retrieval remain available; model loading retries after a cooldown.
:::

---

## System memories

Two memory nodes are protected and cannot be deleted, moved off the root, unpinned, or have their category changed via the API:

| Key | Content |
|---|---|
| `agent_identity` | The agent's core identity and behavior instructions |
| `user_profile` | The user's profile and preferences |

These are always present and always pinned. Protected operations raise an explicit error (`ProtectedMemoryOperationError`) at the service layer.

:::note
System-node protection is enforced in the memory service, not solely by database constraints. The `memories` table does carry a check constraint keeping `is_system` and `system_key` consistent, but the "cannot delete/unpin/move" rules live in service logic.
:::

---

## Categories

| Category | Use for |
|---|---|
| `core` | Identity, roles, stable constraints |
| `preference` | User preferences, communication style, behavior settings |
| `project` | Project state, plans, artifacts, workstreams |
| `correction` | Explicit corrections to agent behavior or past mistakes |

Categories are validated by a database check constraint and are used both for organization and as filters in search. Searching within a specific category improves precision.

---

## Memory operations

The agent works with memory through a single grouped `memory` tool that exposes these actions:

| Action | Purpose |
|---|---|
| `store` | Create a new node |
| `roots` | List root nodes |
| `tree` | Expand a full tree from a root |
| `get_node` | Fetch one node by ID |
| `list_children` | List a node's direct children |
| `update` | Update a node (content, title, summary, importance, pinned, category, metadata) |
| `touch` | Refresh recency for one or more nodes |
| `move` | Reparent a node or move it to root |
| `delete` | Delete a node |
| `search` | Run the hybrid retrieval pipeline |

---

## Editing memory

Operators can inspect and edit the full memory tree in the Sentinel UI under **Memory** (per instance). You can:

- Browse the tree structure
- Edit node content directly
- Delete nodes
- Pin or unpin nodes

Agents also manage memory autonomously — storing new context, updating stale nodes, merging duplicates, and reorganizing structure when it becomes crowded — using the operations above. Protected system nodes (`agent_identity`, `user_profile`) can have their content updated, but cannot be deleted, unpinned, or moved. The policy calls for saving durable corrections, preferences, decisions, and verified discoveries at natural milestones, updating existing entries rather than duplicating them. Subagents report findings to the parent for durable writes.

---

## Current limitations

- **The local model is English-focused.** Multilingual retrieval quality is not guaranteed. First use requires downloading model assets; offline installations need the model cache provisioned beforehand.
- **The "most recent" fallback can return unrelated nodes.** Because it always returns results, low-quality matches can surface as memory context when keyword/substring searches come up empty.
- **System-node protection is service-layer, not fully constraint-enforced.** Direct database writes that bypass the service could violate the protection rules.
