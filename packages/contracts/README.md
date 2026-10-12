# packages/contracts

Single source of truth for API contracts.

- `openapi.json` — generated from the FastAPI app via `scripts/generate-contracts.sh`.
  CI fails if it goes stale. Do not edit by hand.
- Generated TypeScript/Dart client types are added here in M2 (web uses typed
  helpers in `apps/web/src/lib/api.ts` until then; Flutter will consume this
  spec in M4).
