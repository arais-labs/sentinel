# Refactor Roadmap

Top refactor targets from current state, ordered by impact:

1. Settings mutability vs module-level cached constants
- `CHAT_*` values are computed at import time while runtime settings can be updated.
- Refactor goal: centralize runtime config reads behind typed accessors instead of import-time constants.

2. Inconsistent `app.state` policy
- Some routes fail fast when missing runtime state; others create fallbacks dynamically.
- Refactor goal: single strict startup policy with deterministic runtime container initialization.

3. Runtime assembly logic split/duplication
- Startup wiring and rebuild wiring duplicate assembly concerns.
- Refactor goal: shared typed runtime container builder used by startup + rebuild paths.

4. Dependency typing and diagnostics
- Some dependency providers are loosely typed and runtime errors are generic.
- Refactor goal: explicit types for dependencies and explicit service configuration errors.

5. Frontend maintainability hotspots
- Large pages mix transport, state orchestration, parsing, and rendering.
- Refactor goal: split by feature hooks/components and isolate data-fetch orchestration.

6. Markdown theming consistency
- Highlight theming is static and not aligned with active app theme.
- Refactor goal: theme-aware markdown/code rendering with shared styling tokens.
