# token-caching

Standalone Anthropic token caching proof of concept and research notes.

## Contents

1. `poc/` executable scripts and captured result json
2. `FINDINGS.md` distilled findings from live runs
3. `SPEC_CHEATSHEET.md` practical request and usage field cheat sheet
4. `TEST_PLAN.md` repeatable validation plan

## Why this exists

To verify caching behavior outside Sentinel runtime first, using isolated probes with controlled prompt structure, then feed verified rules back into Sentinel implementation.
