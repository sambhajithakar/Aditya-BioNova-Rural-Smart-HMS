# Test Results — Milestone M1

Environment: Ubuntu 24.04 VM, Python 3.12, PostgreSQL 16.15 (local instance,
application queries run as non-owner `hms_app` → **RLS active in tests**),
Next.js 15.5.27 / TypeScript 5.9.3. Date: 2026-10-12.

## Summary

| Suite | Command | Result |
|---|---|---|
| API lint | `ruff check apps/api --select F,E --line-length 100` | ✅ clean |
| API tests | `cd apps/api && pytest` | ✅ **33 passed, 0 failed** (≈3.3 s) |
| Web typecheck | `npm run typecheck` | ✅ clean |
| Web production build | `npm run build` | ✅ 6 routes, type+lint pass |
| OpenAPI contract | `scripts/generate-contracts.sh` | ✅ 10 paths, snapshot committed |
| Docker stack | `docker compose up` | ⚠️ **not executed** — no Docker daemon in this workspace; files provided, unverified |
| GitHub Actions CI | push/PR | ⚠️ not run yet (repository not pushed); workflow mirrors the local commands above |

## Coverage by acceptance area

**Authentication (tests/test_auth.py)**
- register → /me round trip; no password/hash leakage in output
- duplicate e-mail 409, duplicate slug 409, weak password 422
- login success; wrong password and unknown e-mail return *identical* 401 bodies
- multi-org login requires `organization_slug` (409 lists slugs)
- refresh rotation; replay of rotated token → 401 + whole family revoked +
  `auth.refresh_reuse_detected` audited; newest token dead afterwards
- logout kills access *and* refresh tokens immediately
- tampered JWT, wrong-key JWT, missing token → 401

**Tenant isolation (tests/test_tenancy.py)** — API level *and* raw SQL
- branch/member/audit listings scoped to caller's tenant
- cross-tenant branch id → 404; forged `X-Org-Id` header ignored
- member created via tenant A lands in A only
- raw `hms_app` connections: no GUC context → **0 rows everywhere**;
  org A context sees only A; org B only B; INSERT with wrong org → RLS
  violation; org INSERT without bootstrap flag → denied; audit INSERT for
  foreign org → denied
- membership visibility requires user context
- audit immutability: UPDATE/DELETE raise `append-only` (table owner) and are
  grant-denied (app role)

**RBAC & audit (tests/test_rbac_audit.py)**
- receptionist: reads OK, branch/member management and audit → 403
- auditor: audit read OK, all writes → 403 (regression test for the M1 seed
  bug found and fixed in migration 0004)
- support role cannot list branches
- owner role ungrantable (403), unknown role 422, duplicate membership 409
- branch codes unique *per tenant* only
- audit trail contains register/login/branch/member actions; failed logins
  recorded with reasons (operator-only visibility)

## Defects found & fixed during M1
1. RLS `RETURNING` rejection during registration → org-id claimed in tenant
   context before insert (code) — regression covered by register tests.
2. Org-NULL security events could not be written through `RETURNING` →
   `insert_audit_event()` SECURITY DEFINER function (migration 0003).
3. **RBAC seed bug:** auditor role had full admin permissions → fixed in
   migration 0004, guarded by `test_auditor_can_read_audit_only`.
4. Test e-mail domains used reserved `.test` TLD rejected by email-validator
   → switched to `example.com` (test-only change).

## Known gaps / not tested yet
- No load/performance measurement (p95 <500 ms goal measured at M7 with
  realistic data).
- No backup/restore drill executed (procedure drafted, DEPLOYMENT.md).
- Docker images built-by-CI not yet verified end-to-end.
- Browser UI flows verified by build/typecheck only — manual sign-in check
  pending in the dev preview; Playwright E2E starts at M2.
- Security: no external penetration test (pre-launch gate).

## Reproduce

```bash
./scripts/setup_local_db.sh
cd apps/api && ../../.venv/bin/python -m pytest -v
```
