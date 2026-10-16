# Aditya BioNova Smart HMS

Multi-tenant Hospital & Clinic Management System for Indian clinics and
hospitals — offline-first mobile workflows (M4+), trilingual UI (English / मराठी / हिंदी),
role-based access, audit trails and a commercial SaaS business layer.

> **Status: Milestone M1 — Foundation (this repository).**
> Authentication, multi-tenancy, RBAC and audit logging are implemented and
> tested. Clinical modules (patients, OPD, prescriptions, billing) are **not
> built yet**. This software is **not production-ready, not certified and not
> clinically validated**. Synthetic test data only — never enter real patient
> data at this stage.

## Repository layout

```
Aditya-BioNova-Smart-HMS/
├── apps/
│   ├── api/            FastAPI + PostgreSQL backend (versioned /api/v1)
│   │   ├── app/        application code (routers, models, security, RLS context)
│   │   ├── alembic/    database migrations (schema, RLS policies, RBAC seed)
│   │   └── tests/      pytest suite incl. raw PostgreSQL RLS proofs
│   └── web/            Next.js 15 + TypeScript web portal (login/dashboard shell)
├── packages/contracts/ OpenAPI snapshot (single source of truth; generated)
├── infra/docker/       Dockerfiles + docker-compose (local/staging stack)
├── scripts/            setup_local_db.sh, generate-contracts.sh
├── .github/workflows/  CI (lint, tests against real Postgres, web build, contract check)
└── README, REQUIREMENTS, ARCHITECTURE, DATABASE_SCHEMA, API_DOCUMENTATION,
    TASKS, TEST_RESULTS, SECURITY_CHECKLIST, LICENSES, CHANGELOG, DEPLOYMENT
```

## Quick start (local development)

Prerequisites: Python 3.12+, Node 20+, PostgreSQL 16 (any local instance).

```bash
# 1) PostgreSQL with a superuser "hms" listening on 127.0.0.1:5433
#    (scripts/setup_local_db.sh provisions the app role + databases)
./scripts/setup_local_db.sh

# 2) Backend
python3 -m venv .venv && .venv/bin/pip install -r apps/api/requirements-dev.txt
cp .env.example .env            # then edit JWT_SECRET
cd apps/api && ../../.venv/bin/alembic upgrade head
../../.venv/bin/uvicorn app.main:app --port 8000

# 3) Web (separate terminal)
cd apps/web && npm ci && npm run dev   # http://localhost:3000
```

Or with Docker (see DEPLOYMENT.md):

```bash
export JWT_SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")
docker compose -f infra/docker/docker-compose.yml up --build
```

## Tests

```bash
ruff check apps/api --select F,E --line-length 100   # lint
cd apps/api && ../../.venv/bin/python -m pytest      # 33 tests (auth, RBAC, RLS, audit)
cd apps/web && npm run typecheck && npm run build    # type-safe production build
```

## Project rules

- **This repository is fully isolated.** The Aditya BioNova corporate website
  and the OncoTrial SMO product are separate systems and must never be
  modified from here.
- **No real patient data** in development, demos, logs or screenshots.
- Third-party licences are tracked in [LICENSES.md](LICENSES.md); no AGPL
  dependencies are permitted in this product.
- Milestone status and next actions live in [TASKS.md](TASKS.md).
