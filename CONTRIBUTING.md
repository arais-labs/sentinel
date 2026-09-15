# Contributing to Sentinel

Thanks for contributing to Sentinel by ARAIS.

## Quick Rules

1. Keep changes focused and production-safe.
2. Add tests when behavior changes.
3. Keep docs updated for user-facing changes.
4. Preserve third-party notices and license files.

## Development Setup

On macOS, install [Homebrew](https://brew.sh) and Xcode Command Line Tools
(`xcode-select --install`), then run from the repository root:

```bash
make setup
make dev
```

`make setup` installs missing uv/Node tooling, uv-managed Python 3.12, locked backend development
dependencies, frontend and Electron dependencies, and the repository Git hooks.
SQLite storage requires no separate database installation. The managed Python
build supports SQLite extension loading, required by sqlite-vec; system Python
builds that disable extension loading are not supported. Setup verifies that
sqlite-vec loads successfully.

`make dev` opens Electron. Its service manager generates secrets and starts the
backend, which creates its SQLite databases under the dev profile's `state/storage/`. Vite provides
frontend hot reload, Uvicorn reloads backend changes, and Electron source changes
rebuild and restart the shell. Quit Electron or press Ctrl+C to stop everything.
Compilation errors are printed in the launching terminal.

The renderer uses the electron-vite development URL for hot reload, and packaged
builds use `sentinel://app`. Backend requests and streams pass through Electron
to a private Unix socket. FastAPI has no TCP listener or user-login flow.

Development data, configuration, logs, and workspace disks live in
`~/Library/Application Support/Sentinel Dev/` on macOS, separate from the installed
desktop app. Data survives restarts and stays outside the checkout, so the Sentinel
repository itself can be mounted as a workspace. Unix sockets use temporary storage.
The app manages its development configuration and database startup.

Python uses `apps/backend/sentinel/.venv` with uv-managed Python 3.12. Select
`apps/backend/sentinel/.venv/bin/python` as your IDE interpreter.

```bash
make logs           # Follow desktop and backend service logs
make lint           # Black formatting check and Ruff lint
make format         # Apply Black formatting
make test           # Backend and desktop transport tests
make typecheck      # Frontend and Electron TypeScript checks
make check          # All the checks above
make desktop-build  # Build the Electron desktop distribution
```

Black owns formatting; Ruff checks Python correctness. To fix individual lint
findings, run `uv run --project apps/backend/sentinel ruff check --fix <files>`
and review the changes. The Makefile wraps the existing tools; they can also be
run directly. `npm --prefix apps/desktop/sentinel run dev` starts the same desktop
development workflow.

## Pull Request Checklist

1. The change is scoped and explained clearly.
2. Tests pass locally for affected components.
3. New env vars, endpoints, or UI flows are documented.
4. Commit messages are clear and include DCO sign-off.
5. For approval-gated flows, verify create -> stream -> refresh/rehydrate -> approve/reject.

## Git Hook Policy

This repository ships local hooks in `.githooks/`:

1. `pre-commit`
2. `commit-msg`

Enforced checks:

1. Whitespace and conflict marker validation.
2. Black formatting and Ruff lint checks for staged Python files.
3. Block common secret file patterns and large staged files.
4. Heuristic secret scanning in staged text content.
5. Commit message hygiene (no WIP/fixup/squash, <=72 char subject).
6. Mandatory DCO sign-off line.

## Release Legal Checks

1. Confirm you have rights to all custom code, logos, screenshots, and content added.
2. Preserve third-party license notices when redistributing source or images.
3. Generate dependency license inventories before release:
   `bash scripts/generate-license-reports.sh`
4. When distributing desktop builds, include `LICENSE` and `NOTICE` in release artifacts.
5. For OAuth deployment, ensure privacy policy and provider terms are configured.

## DCO (Developer Certificate of Origin)

All commits must be signed off.

Use:

```bash
git commit -s -m "your message"
```

This adds a line like:

```text
Signed-off-by: Your Name <you@example.com>
```

By signing off, you certify the contribution terms under the Developer Certificate of Origin (DCO) 1.1.

### DCO 1.1 (Short Form)

By making a contribution to this project, I certify that:

1. The contribution was created by me and I have the right to submit it under the project license.
2. The contribution is based on prior work that is appropriately licensed and I have the right to submit it.
3. The contribution was provided directly to me by another person who certified one of the above, and I have not modified it.
4. I understand this project and contribution history are public and that contribution records are retained indefinitely.

## Licensing

By contributing, you agree that your contributions are licensed under the repository license:
- GNU AGPL-3.0 (`LICENSE`)
