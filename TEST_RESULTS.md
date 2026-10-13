# Test Results — Milestones M1 + M2

Environment: Ubuntu 24.04 VM, Python 3.12, PostgreSQL 16.15 (local instance,
application queries run as non-owner `hms_app` → **RLS active in tests**),
Next.js 15.5.27 / TypeScript 5.9.3. M1: 2026-10-12, M2: 2026-10-13.

## Summary

| Suite | Command | Result |
|---|---|---|
| API lint | `ruff check apps/api --select F,E --line-length 100` | ✅ clean |
| API tests | `cd apps/api && pytest` | ✅ **48 passed, 0 failed** (≈4.8 s; 33 M1 + 15 M2) |
| Web typecheck | `npm run typecheck` | ✅ clean |
| Web production build | `npm run build` | ✅ 7 routes (M2 adds /patients, /appointments, /queue), type+lint pass |
| OpenAPI contract | `scripts/generate-contracts.sh` | ✅ 20 paths, snapshot committed |
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

---

# Milestone M2 — Registration → Appointments → OPD queue (2026-10-13)

## M2 coverage (tests/test_m2_clinical.py, 15 tests)

**Patient registration & supervised dedupe**
- UHID issued per tenant (`P0000001`, `P0000002`…); same demographics in a
  second tenant do NOT collide (tenant-scoped duplicate detection)
- duplicate phone → 409 with `candidates[].matched_on=["phone"]`
  (country-code +91 normalized); duplicate name+dob → same, both flags
  listed when both match
- `allow_duplicates=true` creates the record and is audited
  (`patient.created` with `allow_duplicates: true`)
- search by name/UHID/phone with `{items,total,limit,offset}` pagination
- supervised merge: self-merge 409, merge 200, source retained as
  `merged` + `merged_into_id` (never deleted), source read-only (409 on
  PATCH), hidden from search, double-merge 409

**Appointments**
- create → reschedule → cancel lifecycle; completed/cancelled frozen (409)
- provider double-booking in an overlapping window → 409; free window OK
- ends≤starts → 422
- foreign patient id / foreign branch id → **404** (no FK-error leak)
- date-range + status filters with pagination

**OPD queue**
- tokens 1, 2, … per tenant per day; duplicate active entry → 409
- state machine: waiting→called→in_consultation→completed stamps
  called_at/started_at/completed_at; illegal jumps (waiting→start,
  completed→call) → 409; terminal states frozen
- `Idempotency-Key` replay returns the SAME entry (no duplicate token);
  same key + different payload → 409

**Tenancy / RBAC negative tests**
- cross-tenant patient GET/PATCH → 404; cross-tenant queue call → 404;
  tenant B's board shows 0 of tenant A's entries
- forged `X-Org-Id` on clinical routes ignored (404)
- support role: patient/queue endpoints → 403 (blueprint rule: vendor
  support never sees identifiable patient data)
- doctor: queue read+manage OK, patient registration → 403;
  nurse: patient/appointment read OK, queue.manage → 403
- raw SQL RLS proofs: no GUC context → 0 rows; tenant B context sees 0 of
  tenant A's patients; INSERT with foreign organization_id → denied
  (WITH CHECK)

## Defects found & fixed during M2
1. Phone duplicate detection missed `+91…` / `0…` prefixes → schema-level
   digit normalization before matching (regression-tested via `+91` case).
2. (test-only) `_patient()` defaults collided across tests in the shared
   session DB → unique per-call phone/name sequence.

## M2 known gaps
- Playwright E2E not started (needs headless browser in CI) — starts M3.
- Password reset e-mail pending the e-mail provider decision (M7 gate).
- Translation QA: M2 strings translated programmatically; native-speaker
  review still pending.
- `service_date` uses the UTC day; facility-local day boundary is M3.
