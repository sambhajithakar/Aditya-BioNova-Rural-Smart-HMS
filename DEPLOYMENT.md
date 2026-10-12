# Deployment

**M1 status:** development/staging procedures below are implemented and
documented; production deployment requires the M9 human gates (security,
clinical, legal, pilot). No public deployment has been performed.

## Environments

| Env | Data | Controls |
|---|---|---|
| Local dev | Synthetic only | No public access; `.env` git-ignored |
| Staging | Synthetic/de-identified | Production-like security; CI runs there first |
| Pilot production | Authorized real records **only after gates** | Tenant isolation, MFA*, backups, audit, DPA |
| Commercial production | Contracted records | SLAs, DR, monitoring, audits |

\* MFA is pending (M7) — a hard pre-pilot requirement.

## Required environment variables

| Variable | Used by | Notes |
|---|---|---|
| `DATABASE_URL` | API | **non-owner** `hms_app` role → RLS enforced |
| `MIGRATION_DATABASE_URL` | Alembic | table-owner `hms` role; deploy-step only |
| `JWT_SECRET` | API | ≥16 chars; generate `python3 -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `JWT_ACCESS_TTL_SECONDS` / `JWT_REFRESH_TTL_SECONDS` | API | defaults 1800 / 1209600 |
| `CORS_ORIGINS` | API | JSON list of exact origins |
| `ENVIRONMENT` | API | development / staging / production |
| `API_ORIGIN` | Web (rewrites) | `http://api:8000` in compose, same-origin URL in prod |
| `HMS_PG_PASSWORD`, `HMS_APP_PASSWORD` | Postgres/bootstrap | local defaults only |

Never commit `.env` or secrets. In production put secrets in the platform's
secret store.

## Path A — Docker Compose (staging)

```bash
export JWT_SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")
docker compose -f infra/docker/docker-compose.yml up --build
# API  http://localhost:8000  (docs at /api/v1/docs)
# Web  http://localhost:3000
```
Notes: API runs `alembic upgrade head` at start (fine for single instance);
first Postgres boot provisions `hms_app` via `infra/docker/postgres-init/`.
⚠️ This stack was authored but **not executed in the M1 build workspace**
(no Docker daemon) — verify on first CI/staging run.

## Path B — bare services (what M1 verification used)

1. PostgreSQL 16 with superuser `hms` on `127.0.0.1:5433`
   (local non-root instance documented in README).
2. `./scripts/setup_local_db.sh` → role + `hms_dev`/`hms_test`.
3. `.venv/bin/pip install -r apps/api/requirements-dev.txt`
4. `cd apps/api && alembic upgrade head`
5. `uvicorn app.main:app --host 0.0.0.0 --port 8000`
6. `cd apps/web && npm ci && npm run build && npm run start`

Reverse proxy (nginx/Caddy) terminates TLS; API binds loopback only;
Web proxy path `/api/v1/*` → API (next.config.mjs `API_ORIGIN`).

## Release procedure (M2+)

1. PR → CI green (lint, tests vs real Postgres, web build, contract diff).
2. Deploy DB migrations as a **separate controlled step** with a tested
   rollback (`alembic downgrade -1`); keep one-version schema compatibility.
3. Deploy API → web; smoke: `/api/v1/health`, login, one read endpoint.
4. Tag `X.Y.Z`; update CHANGELOG; archive SBOM output (LICENSES.md).

## Backups & restore (draft — drill required at M7)

```bash
# daily encrypted logical backup
pg_dump -Fc -h 127.0.0.1 -p 5433 -U hms -d hms_dev -f hms_dev.dump
gzip -c hms_dev.dump > hms_dev.dump.gz   # encrypt with platform key in prod
# restore into a scratch database (the M7 drill's acceptance step)
createdb hms_restore && pg_restore -d hms_restore hms_dev.dump
```
Targets: RPO 24h / RTO 8h to start (Blueprint §5) — improve before wide
rollout. The drill must prove: counts match, one login works, audit trail
integrity intact.

## Monitoring & operations (M7 backlog)

Structured JSON logs without PHI (correlation id only) → central log store;
uptime/latency alerts on `/api/v1/health` (p95 <500 ms read target);
error tracking with source maps disabled publicly; incident runbook;
patch cadence for base images and dependencies.

## Rollback

- Application: redeploy previous image/tag (config unchanged).
- Database: `alembic downgrade <rev>` tested before any production
  migration; never auto-downgrade.
- Kill switch: suspend tenant (`organizations.status='suspended'`) → all
  sessions reject on next request without deleting data.
