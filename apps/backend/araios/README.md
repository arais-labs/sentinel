# araiOS Backend

FastAPI backend for centralized auth, approvals, tools, and platform services used by Sentinel.

## Source Of Truth

Use the root setup and operations guide first:
- [Root README](../../../README.md)

## Run (Via Stack Compose)

From repo root:

```bash
docker compose -f docker-compose.dev.yml up --build postgres araios-backend
```

## Tests

From repo root:

```bash
docker compose -f docker-compose.dev.yml exec -T araios-backend pytest -q
```
