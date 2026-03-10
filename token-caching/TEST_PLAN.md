# Token Caching Test Plan

## Objective

Validate prompt token caching behavior with controlled two run probes.

## Preconditions

1. `ANTHROPIC_TOKEN` environment variable is set.
2. Model available for test, default in scripts is `claude-3-haiku-20240307`.

## Test Matrix

See `poc/claude_cache_flow_matrix_v2.py`.

Cases:

1. `ttl:1h` + stable unchanged
2. no scope beta + `ttl:1h`
3. no `cache_control`
4. one character stable change
5. no ttl
6. `ttl:5m`

## Pass Criteria

1. Case with stable unchanged and cache markers:
   1. Run 1 has `cache_creation_input_tokens > 0`
   2. Run 2 has `cache_read_input_tokens > 0`
2. Case without `cache_control`:
   1. Run 1 and Run 2 both have zero cache creation and read
3. Case with one character stable change:
   1. Run 2 does not read previous cache

## Commands

```bash
python3 token-caching/poc/claude_cache_flow_poc.py
python3 token-caching/poc/claude_cache_flow_matrix.py
python3 token-caching/poc/claude_cache_flow_matrix_v2.py
```

## Artifacts

1. `poc/claude_cache_flow_matrix_results.json`
2. `poc/claude_cache_flow_matrix_v2_results.json`
