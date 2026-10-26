# Desktop tests

Run commands from `apps/desktop/sentinel`.

| Directory | Contract | Entry point |
| --- | --- | --- |
| `unit/` | Application adapters: the sentineld client, workspace lifecycle, bridges, notifications, packaging helpers | `npm test` |
| `integration/` | Electron windows and bundle loading, against a built app | `npx electron tests/integration/<name>.mjs` |

The workspace runtime has its own tests beside its code: `packages/sentineld` (daemon, guest
agent, images) and `packages/sentinel-workspace` (`make test` for the encoder test).
