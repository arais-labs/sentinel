# Contributing to Sentinel

Thanks for contributing to Sentinel by ARAIS.

## Quick Rules

1. Keep changes focused and production-safe.
2. Add tests when behavior changes.
3. Keep docs updated for user-facing changes.
4. Preserve third-party notices and license files.

## Development Setup

```bash
docker compose -f docker-compose.dev.yml up --build
```

The development compose file uses local-only defaults. The production-shaped
`docker-compose.yml` requires explicit `SENTINEL_POSTGRES_PASSWORD`,
`SENTINEL_JWT_SECRET_KEY`, `SENTINEL_DATA_ENCRYPTION_KEY`, and
`SENTINEL_AUTH_PASSWORD` values.

Install Python formatting tooling:

```bash
python3 -m pip install black
```

Enable repository git hooks:

```bash
bash scripts/install-git-hooks.sh
```

## Pull Request Checklist

1. The change is scoped and explained clearly.
2. Tests pass locally for affected components.
3. New env vars, endpoints, or UI flows are documented.
4. Commit messages are clear and include DCO sign-off.
5. For approval-gated flows, verify create -> stream -> refresh/rehydrate -> approve/reject.

## CLI and Compose Checks

For CLI, Compose, or documentation cleanup, run:

```bash
bash -n sentinel-cli.sh
SENTINEL_POSTGRES_PASSWORD=test-postgres-password \
  SENTINEL_JWT_SECRET_KEY=test-jwt-secret-at-least-local-config \
  SENTINEL_DATA_ENCRYPTION_KEY=test-data-encryption-key-at-least-32 \
  SENTINEL_AUTH_PASSWORD=test-admin-password \
  docker compose config -q
docker compose -f docker-compose.dev.yml config -q
git diff --check
```

## Git Hook Policy

This repository ships local hooks in `.githooks/`:

1. `pre-commit`
2. `commit-msg`

Enforced checks:

1. Whitespace and conflict marker validation.
2. Black formatting check for staged Python files.
3. Block common secret file patterns and large staged files.
4. Heuristic secret scanning in staged text content.
5. Commit message hygiene (no WIP/fixup/squash, <=72 char subject).
6. Mandatory DCO sign-off line.

## Release Legal Checks

1. Confirm you have rights to all custom code, logos, screenshots, and content added.
2. Preserve third-party license notices when redistributing source or images.
3. Generate dependency license inventories before release:
   `bash scripts/generate-license-reports.sh`
4. If distributing Docker images, include `LICENSE` and `NOTICE` in release artifacts.
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
