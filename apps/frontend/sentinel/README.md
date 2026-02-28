# Sentinel Frontend

React operator UI for the Sentinel runtime.

## Source Of Truth

Use the root docs for full stack setup and routing:
- [Root README](../../../README.md)

## Run (Via Stack Compose)

From repo root:

```bash
docker compose -f docker-compose.dev.yml up --build sentinel-frontend sentinel-backend
```

## Standalone Frontend Dev (Optional)

If you only need UI development:

```bash
npm ci
cp .env.example .env.local
```

Then run:

```bash
VITE_BASE_PATH=/sentinel/ \
VITE_ROUTER_BASENAME=/sentinel \
VITE_SENTINEL_API_BASE_URL=/sentinel/api/v1 \
VITE_PLATFORM_AUTH_BASE_URL=/platform/auth \
npm run dev -- --host --port 5173
```
