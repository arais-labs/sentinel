# Anthropic Token Caching Spec Cheatsheet

## Request Side

Use `cache_control` on content blocks that should be cached.

Example block:

```json
{
  "type": "text",
  "text": "stable prefix",
  "cache_control": {
    "type": "ephemeral",
    "ttl": "1h"
  }
}
```

Supported practical TTLs in this POC:

1. `1h`
2. `5m`
3. omitted ttl behaves like short cache bucket in observed usage counters

## Response Usage Fields

Read from `usage`:

1. `input_tokens`
2. `output_tokens`
3. `cache_creation_input_tokens`
4. `cache_read_input_tokens`
5. `cache_creation.ephemeral_1h_input_tokens`
6. `cache_creation.ephemeral_5m_input_tokens`

## Behavioral Rules Verified

1. No `cache_control` means no cache creation and no cache read.
2. Stable cached text must be byte identical for read hit.
3. Dynamic text can change safely if it is outside cached block boundaries.
4. One character change inside cached stable block causes miss.

## Header Notes Used in POC

Base:

1. `anthropic-version: 2023-06-01`
2. `authorization: Bearer <oauth token>`

Optional tested beta:

1. `prompt-caching-scope-2026-01-05`

Observed in this POC:

1. baseline caching worked without this extra beta in direct `/v1/messages` path
