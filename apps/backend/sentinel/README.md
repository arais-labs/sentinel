# Sentinel Backend

FastAPI runtime for Sentinel agents, memory, triggers, and operator controls.

## Source Of Truth

Use the root setup and operations guide first:
- [Root README](../../../README.md)

This component README is intentionally short and only covers backend-specific commands.

## Run (Via Stack Compose)

From repo root:

```bash
docker compose -f docker-compose.dev.yml up --build postgres araios-backend sentinel-backend
```

## Tests

From repo root:

```bash
docker compose -f docker-compose.dev.yml exec -T sentinel-backend python -m pytest -q
```

## Health Check

- `GET /api/v1/health`

## runtime Contract

- `command=user`: execution as the default runtime user inside the session workspace.
- `command=root`: root execution inside the session runtime workspace and approval-gated by default.
- Job actions are explicit commands too: `jobs`, `job_status`, `job_logs`, `job_stop`.
