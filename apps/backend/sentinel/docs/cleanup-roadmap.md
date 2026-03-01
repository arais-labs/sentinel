# Cleanup Roadmap

Top refactor targets from current state, ordered by impact:

1. settings mutability vs module-level cached constants

- `CHAT_*` is computed once at import in `config.py:108`, but runtime code mutates
  `settings` in `settings_service.py:167` and `settings_service.py:188`.
- Refactor: either make `settings` immutable after startup, or expose
  `get_chat_iteration_limits()` and stop using module-level cached constants.

2. Inconsistent `app.state` policy (strict in WS, fallback in sessions)

- WS fails fast in `ws.py:151`.
- Sessions silently creates missing registry in `sessions.py:38`.
- Refactor: one policy only (prefer strict + deterministic startup init everywhere).

3. Runtime assembly logic is still split/duplicated

- Startup wiring is huge in `main.py:64`.
- Rebuild path repeats part of loop construction in `runtime_rebuild.py:13`.
- Refactor: extract a single `RuntimeContainerBuilder` used by both startup and
  rebuild.

4. Dependency typing/diagnostics are still weak

- `get_settings` returns `object` in `dependencies.py:14`.
- `get_llm_provider` raises bare `RuntimeError()` in `dependencies.py:40`.
- Refactor: type it as `Settings` and raise explicit typed error.

5. Frontend maintainability hotspots

- `SessionsPage.tsx` and `LogsPage.tsx` are still very large and mix
  fetch/state/render/parsing.
- Refactor: split into hooks + feature components (streaming timeline, context
  inspector, filters, message list).

6. Markdown theming is improved but syntax theme is fixed dark

- Static import in `main.tsx:5` forces dark code theme even in light mode.
- Refactor: switch highlight theme based on app theme or move to CSS vars.

Quick health check:

- Backend targeted tests pass (ws, sessions, chat, integration set).
- Frontend `tsc --noEmit` passes.

Next execution proposal:

- Execute refactor items 1 -> 3 first in one clean pass.
