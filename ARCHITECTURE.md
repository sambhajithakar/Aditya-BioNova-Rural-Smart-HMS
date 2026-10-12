# Architecture — Aditya BioNova Rural Smart HMS (M1)

## Style: modular monolith

One deployable FastAPI backend with enforced module boundaries, one Next.js
web frontend, PostgreSQL as the system of record. A Flutter Android app
(M4) and an on-premises device gateway (M8) attach to the same API.
Microservices are deliberately deferred: premature distribution costs more
than it returns at pilot scale, while module boundaries keep a later split
possible.

```
Browser (Next.js) ──/api/v1──▶ Next rewrite proxy ──▶ FastAPI (uvicorn)
Flutter app (M4)  ──────────────────────────────────▶      │
                                                          ▼
                                          auth (JWT) → RBAC dep → tenant GUC set
                                                          ▼
                                             domain service (routers)
                                                          ▼
                                    PostgreSQL (row-level security)  +  audit_events
```

## Stack (locked for M1; alternatives considered)

| Layer | Choice | Why | Rejected alternatives |
|---|---|---|---|
| Web | Next.js 15 + React 19 + TS | SSR-ready, typed, your prompt's lead option | Vite SPA (weaker for future portals) |
| API | FastAPI + Pydantic v2 | Validation, auto-OpenAPI, async-ready | NestJS (split-language maintenance), Django (heavier than needed) |
| DB | PostgreSQL 16 + Alembic | RLS for tenant isolation, constraints, migrations | Per-tenant DBs (later paid tier), MySQL (no RLS) |
| Auth | First-party JWT (PyJWT, HS256) + server-side sessions | No extra infra at pilot scale; instant revocation | Keycloak/OIDC (M7 Enterprise), third-party IdP (cost/lock-in) |
| Passwords | scrypt (stdlib, n=16384,r=8,p=1) | OWASP-approved KDF, zero licence risk | bcrypt/argon2 (extra deps; revisit if params need tuning) |
| Offline mobile | Flutter + encrypted SQLite (M4) | Background sync, scanners/printers beyond PWA | PWA-only (insufficient for scanner/background needs) |
| Cache/jobs | Redis — deferred | Not needed until notifications/reports | Early Redis (complexity without load) |

## Request lifecycle & tenant isolation

1. **Transport**: TLS at ingress (deployment), CORS restricted to configured
   origins, security headers on every response.
2. **Correlation**: `X-Request-ID` generated/propagated; used in logs and
   error bodies (no PHI in logs).
3. **Authentication**: `Authorization: Bearer <JWT>`. Claims: `sub` (user),
   `sid` (session), `org`, `iss`, `exp`, `typ=access`. The session row is
   re-checked every request → logout and refresh-rotation revoke access
   immediately (DB-backed, not just exp-claims).
4. **Tenant context**: after session validation the API sets *transaction-
   local* PostgreSQL GUCs — `app.current_user_id`, `app.current_org_id` —
   **derived only from the verified token**. Client headers/query params are
   never trusted (tested: forged `X-Org-Id` is ignored).
5. **RBAC**: `require_permission("…")` dependency loads the role's permission
   set from the database; missing permission → 403.
6. **Data access**: service layer scopes by `organization_id`; PostgreSQL RLS
   repeats the check (defence in depth). Cross-tenant identifiers return 404.
7. **Audit**: sensitive actions write to `audit_events` before commit.
8. **Commit/rollback**: one session/transaction per request (`get_db`).

## RLS policy summary (see DATABASE_SCHEMA.md for full text)

- `organizations`: SELECT if `id = GUC(org)` OR membership exists; INSERT
  only during registration (`app.is_bootstrap` one-shot flag) and only for
  the id claimed in `GUC(org)`; UPDATE if active membership; no DELETE.
- `branches`, `memberships`: strict `organization_id = GUC(org)` (memberships
  additionally visible by `user_id = GUC(user)` for login discovery).
- `audit_events`: SELECT only own tenant; INSERT only own tenant or
  system-NULL; UPDATE/DELETE impossible (no policy + revoked grants +
  `BEFORE UPDATE OR DELETE` trigger).
- `users`, `auth_sessions`: **no RLS by design** — authentication must read
  them before any tenant context exists. Mitigations: never joined into
  cross-tenant responses, `password_hash` excluded from every API schema,
  session lookup keyed by unguessable token.

### Two PostgreSQL gotchas solved in this codebase (documented for reviewers)

1. **`INSERT … RETURNING` is checked against SELECT policies.** During
   registration the organization row had no membership yet, so the ORM's
   `RETURNING` failed RLS. Fix: the register flow *claims* the new org id in
   `app.current_org_id` before inserting (policy `id = GUC(org)` covers it).
2. **Org-NULL security events** (failed logins for unknown e-mails) are
   invisible to every tenant by design, so no `RETURNING` statement can write
   them. Fix: `insert_audit_event()` SECURITY DEFINER function (migration
   0003) keeps the tenancy *write* guard but performs a plain INSERT.

## Auth & session flows

- **Register**: creates user + organization + owner membership atomically;
  bootstrap flag is the only way past the organizations INSERT policy.
- **Login**: generic 401 for unknown e-mail *and* wrong password (timing
  equalised with a dummy scrypt verify); multi-org accounts must pass
  `organization_slug` (409 lists their slugs).
- **Refresh**: opaque rotating token (SHA-256 stored). Replaying a rotated
  token revokes the whole session family and writes
  `auth.refresh_reuse_detected` — even if the request then 401s (security
  events commit in their own transaction).
- **Logout**: revokes the session row → access token dies immediately.

## Environments & data rules

- Local = synthetic data only; UTC storage; tenant-scoped UHID comes in M2
  (UUID PKs already in place); idempotency keys for payments/orders arrive
  with M3.
- Feature flags hide unbuilt modules (web nav greys out M2 modules).

## Scaling path (not built yet)

Read replicas + Redis for notifications/reports; object storage for
documents; per-request GUCs unchanged; dedicated-database tier for
high-assurance customers; device gateway as a separate on-prem deployable
talking to `POST /api/v1/device-messages` (M8).
