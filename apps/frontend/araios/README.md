# araiOS Frontend

React workspace for araiOS capabilities: auth management, approvals, tooling, and operator-facing platform controls.

## Source Of Truth

Use the root docs for complete setup and routing:
- [Root README](../../../README.md)

## Run (Via Stack Compose)

From repo root:

```bash
docker compose -f docker-compose.dev.yml up --build araios-frontend araios-backend
```

## Standalone Frontend Dev (Optional)

```bash
npm ci
cp .env.example .env.local
VITE_BASE_PATH=/araios/ npm run dev -- --host --port 5174
```
