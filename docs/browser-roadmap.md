# Browser Roadmap

This document defines the browser automation roadmap for Sentinel.
It focuses on reliability, operator visibility, and low-friction agent execution.

## Goals

- Make multi-step browser tasks deterministic and easy to debug.
- Keep tool usage simple for models while preserving operator control.
- Reduce flaky runs caused by dynamic UIs, overlays, and frame boundaries.

## Current Baseline

Today Sentinel supports:

- navigation and screenshots
- DOM accessibility snapshots
- click/type/select/wait/key press actions
- tab list/open/focus/close
- value reads for verification
- high-level form execution via `browser_fill_form`

## Roadmap

## Phase 1: Deterministic Targeting

- Add stable element references in snapshots that can be reused across calls.
- Keep current semantic selectors (`button: X`, `textbox: Y`) as fallback.
- Return clear stale-reference errors when DOM changes invalidate refs.

Acceptance:

- same element can be targeted across multiple actions without selector drift
- tooling gracefully recovers when refs expire

## Phase 2: Frame-Aware Automation

- Add explicit frame/iframe targeting in snapshot and action tools.
- Expose frame metadata in snapshot output.
- Support scoped actions inside nested frames.

Acceptance:

- agent can fill and submit forms inside iframe-heavy pages
- frame context is visible in tool results for debugging

## Phase 3: Browser Diagnostics

- Add browser console log retrieval as a first-class tool.
- Add network error summary output for failed loads/requests.
- Add optional per-step trace IDs in browser tool responses.

Acceptance:

- agent can explain frontend failures using console/network evidence
- operators can correlate tool calls with runtime diagnostics

## Phase 4: Advanced Interactions

- Add `hover` and `drag` actions.
- Add file upload support.
- Add dialog handlers (confirm/prompt).
- Add PDF export and page print capture.

Acceptance:

- agent can complete flows requiring drag/drop, uploads, and modal confirms
- PDF/report workflows can be automated without custom scripts

## Phase 5: Controlled Script Execution

- Add a guarded `evaluate` action for complex edge cases.
- Gate usage behind strict policy and logging.
- Keep it opt-in for high-trust environments.

Acceptance:

- agents can recover from edge UIs without broad unsafe access
- every evaluation call is auditable

## Phase 6: Session and Profile Modes

- Add explicit browser profile modes:
  - isolated runtime profile
  - attach-to-existing-browser profile
- Add clear attach-state UX and failure messaging.

Acceptance:

- users can choose isolated vs attached mode per task
- attach failures are actionable and easy to recover

## Non-Goals

- solving CAPTCHA or bypassing human verification systems
- trying to evade anti-bot protections in unsupported ways

## Product Direction

- Default path should remain simple: snapshot -> fill form -> verify -> continue.
- Advanced capabilities should be layered on top, not required for basic success.
- Every new browser feature must improve either:
  - reliability,
  - debuggability, or
  - operator control.
