# Token Caching POC Findings

This folder contains a standalone proof of concept for Anthropic prompt token caching behavior using OAuth bearer credentials.

## Goal

Reproduce the effective Claude style caching behavior outside Sentinel and measure exact cache usage counters from live API responses.

## Key Outcomes

1. Caching works with OAuth token in direct `/v1/messages` calls when request structure is correct.
2. `cache_control` markers are required on stable content blocks.
3. Stable cached block must remain byte identical between runs.
4. Dynamic content should be isolated in uncached blocks.
5. TTL controls cache bucket usage:
   1. `ttl: 1h` writes to `ephemeral_1h_input_tokens`
   2. `ttl: 5m` or no ttl writes to `ephemeral_5m_input_tokens`
6. Changing one character inside the cached stable block causes cache miss and fresh cache creation.
7. In this live test route, cache worked both with and without the `prompt-caching-scope-2026-01-05` beta header.

## Live Proof Summary

From `poc/claude_cache_flow_matrix_v2_results.json`:

1. Case A (`ttl:1h` + stable unchanged)
   1. Run 1: `cache_creation_input_tokens > 0`
   2. Run 2: `cache_read_input_tokens > 0`
2. Case C (no `cache_control`)
   1. Run 1 and Run 2: no cache creation and no cache read
3. Case D (`ttl:1h` but one character changed in stable block)
   1. Run 1: cache creation
   2. Run 2: cache miss and fresh creation
4. Case E (`cache_control` without ttl)
   1. Uses `ephemeral_5m_input_tokens`

## Important Caveats

1. Results are account and capability dependent.
2. Counter values vary by exact prompt size.
3. Cross test contamination can occur if stable prefixes are reused; v2 matrix isolates each case using unique per case prefixes.

## Suggested Sentinel Integration Rules

1. Split system context into stable and dynamic blocks.
2. Apply `cache_control` only to stable blocks.
3. Keep stable block byte identical across loop turns.
4. Preserve cache usage counters end to end in telemetry and response schemas.
