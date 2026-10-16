# Changelog

## M5 (2026-10-15) — Pharmacy + Laboratory (API)

### Added
- **Pharmacy stock**: batches with explicit expiry against the validated
  `drug_catalog` (free-text stock is impossible); receiving writes the
  batch AND its opening ledger row in one transaction; **FEFO allocation**
  locks batches in expiry order and splits across batches; reason-coded
  adjustments (found stock IN, spoilage/write-off OUT);
  `Idempotency-Key` replay on receiving and dispensing; expiring-soon /
  expired / low-stock alert report.
- **Stock integrity (DB-enforced)**: append-only `stock_movements` ledger
  (trigger + revoked grants) whose sums maintain the cached on-hand
  quantity — negative stock is impossible even via raw SQL; **expired
  stock cannot be dispensed** (only written off); **recalled batches are
  frozen**; dispense documents are corrections-by-return, never edited.
- **Dispensing**: against **signed prescriptions only**, restricted to the
  drugs written on the Rx (no free substitution), or OTC against a
  registered patient; every dispense records batch-level traceability.
- **Laboratory**: orders → specimens (unique per-tenant barcode, reject
  transition) → results with the **imported → verified → released** state
  machine; **four-eyes verification** (the verifier must differ from the
  importer); released results are immutable — **supersession-only
  amendments** (new audited version, deferrable FK + partial unique index
  on live results); released/cancelled orders frozen except the audited
  amendment reopen.
- **RBAC**: 8 new permissions (`pharmacy.*`, `lab.*`) across the role
  catalogue, including new **pharmacist** and **lab_technician** grant
  sets; support/auditor keep zero clinical access (least privilege).
- **Tests**: 17 new (89 total) — FEFO split/drain order, ledger invariant
  (SUM(in)-SUM(out) == on-hand), raw-SQL trigger backstops (expired,
  recalled, append-only, immutable dispenses/results/specimens), four-eyes
  and state-machine coverage, RBAC matrix, cross-tenant 404s and raw RLS
  proofs for all 7 new tables.

### Fixed
- `POST /pharmacy/dispenses` without `branch_id` 500'd: `_infer_branch`
  indexed the scalar returned by `db.scalar()` (`'UUID' object is not
  subscriptable`).
- Releasing the last lab result left the order in `in_progress`: the
  session runs with `autoflush=False`, so the pending-result count read a
  stale status — transitions now flush before callers query.
- Re-importing a test that already had a live result tripped the partial
  unique index (500). It is now a clean 409 pointing at the amendment path.
- The `0007` trigger froze released lab orders so tightly that the
  audited amendment reopen (`released → in_progress`) was impossible; the
  trigger now allows exactly that one transition while keeping the order
  header frozen.

## M3 (2026-10-13) — Consultation → Prescription → Billing → Receipt

### Added
- **Consultation workspace**: encounters with a one-open-per-patient guard,
  range-checked vitals (BP/pulse/Temp/SpO₂/RR/weight/height), structured
  diagnoses (code/label/kind/status), close flow that completes the linked
  queue entry. Completed encounters are frozen by a database trigger.
- **Prescriptions**: draft → sign workflow for `prescription.sign` holders
  only; every item must reference the validated `drug_catalog` (45 seeded
  drugs — free-text medication entry is impossible by construction);
  **allergy check blocks signing** unless the prescriber explicitly
  acknowledges it (acknowledgement persisted + audited); duplicate-drug
  warnings are advisory; **versioned amendments** insert version N+1 and
  flip the previous row to `superseded` — the only UPDATE the DB trigger
  permits after signing; signed rows can never change or be deleted.
- **Billing**: invoices with **server-computed totals** and per-tenant
  `INV-000001` numbering (advisory lock), freeze-on-issue for financials
  and line items, issue/void with reversing adjustment, **append-only
  financial ledger** (trigger + revoked grants + unique partial indexes
  that make double-posting impossible), payments with
  **`Idempotency-Key`** support, bounded refunds (partial → fully
  refunded), printable receipt with ledger reconciliation, and a
  **sandbox payment gateway spike** (pending → confirm/fail callbacks).
- **Print**: A4 trilingual (EN/मराठी/हिंदी) prescription and receipt views
  at `/print/rx/[id]` using the browser print pipeline (Save as PDF).
- **Web**: `/consultation` (vitals, diagnosis, safety-gated signing) and
  `/billing` (invoice → issue → payment → receipt) pages with full
  EN/मराठी/हिंदी strings; navigation updated.
- **RBAC**: 8 new permissions across 6 roles; support and auditor receive
  no clinical or financial access (least privilege).
- **Tests**: 24 new (72 total) — cross-tenant 404s, raw RLS proofs,
  raw-SQL immutability proofs for signed Rx / completed encounters /
  issued invoices / the ledger, RBAC matrix, idempotency replay, refund
  bounds, and the full E2E acceptance chain with audit assertions.

### Fixed
- Ledger balance used `func.case` (not a SQLAlchemy function) — now the
  top-level `case()` construct.
- Ledger-consistency invariant now accounts for voided invoices (fully
  reversed by their adjustment entry).

## M2 (2026-10-13) — Registration → Appointments → OPD queue

### Added
- **Patients**: tenant-scoped UHID (`P0000001`…, advisory-lock allocated),
  registration with **supervised duplicate detection** (phone or name+dob;
  409 + candidates; explicit override audited), search with pagination,
  profile, non-destructive supervised **merge** (source retained, read-only).
- **Appointments**: booking with provider-conflict detection (409 on
  overlap), reschedule/cancel, frozen terminal states, date/status filters.
- **OPD queue**: daily tokens (unique per tenant/day, advisory-lock
  serialized), enforced state machine (waiting→called→in_consultation→
  completed; skip/recall; terminal frozen), doctor/reception board.
- **Idempotency-Key** support on POST /patients and POST /opd/queue
  (stored responses table; replay or 409 on payload mismatch).
- Clinical RBAC: 7 new permissions mapped to 10 roles; support/auditor
  deliberately have NO patient-data access (tested).
- Web: /patients, /appointments, /queue pages + shared AppShell;
  M2 workflow strings in EN/मराठी/हिंदी.
- Migration 0005 (4 new tables, all RLS-enabled with strict tenant
  policies); OpenAPI contract 10 → 20 paths; 15 new tests (48 total).

### Security
- Cross-tenant clinical identifiers return 404 (no existence leak); forged
  org headers ignored; raw-SQL RLS proofs extended to the new tables.

All notable changes to Aditya BioNova Smart HMS.
Format: Keep a Changelog; versions are pre-1.0 milestone tags.

## [0.1.0-m1] — 2026-10-12 — Milestone M1 (Foundation)

### Added
- FastAPI backend with versioned `/api/v1`: health, auth (register, login,
  refresh with rotation + reuse detection, logout, me), branches, members,
  audit-events.
- PostgreSQL schema + Alembic migrations 0001–0004, including row-level
  security for `organizations`/`branches`/`memberships`/`audit_events`,
  non-owner `hms_app` runtime role, append-only `audit_events`
  (policy + grants + trigger), `insert_audit_event()` SECURITY DEFINER helper,
  RBAC permission catalogue with 10 system roles.
- Session model: opaque rotating refresh tokens (SHA-256 stored), session
  family revocation, DB-backed access-token revocation.
- scrypt password hashing (stdlib), generic auth errors, timing equalisation.
- Next.js 15 web shell: login, dashboard, role/permission display, EN /
  मराठी / हिंदी language switcher, navy/teal design system.
- Test suite: 33 tests (auth, RBAC, cross-tenant isolation incl. raw SQL RLS
  proofs, audit immutability) running against a real PostgreSQL as the
  non-owner role.
- Monorepo scaffolding: CI workflow (lint/test/build/contract snapshot),
  Docker Compose stack, OpenAPI contract snapshot in packages/contracts,
  scripts (setup_local_db, generate-contracts).
- Full documentation set: README, REQUIREMENTS, ARCHITECTURE, DATABASE_SCHEMA,
  API_DOCUMENTATION, TASKS, TEST_RESULTS, SECURITY_CHECKLIST, LICENSES (SBOM),
  CHANGELOG, DEPLOYMENT.

### Fixed
- RBAC seed bug: `auditor` role had been granted admin permissions
  (migration 0004 + regression test).
- Registration failed under RLS because PostgreSQL evaluates SELECT policies
  against `INSERT … RETURNING` (org id now claimed in tenant context first).
- Org-NULL security audit events could not be inserted with RETURNING
  (moved to SECURITY DEFINER function).

### Known limitations (M1)
- No clinical modules yet (patients/appointments/OPD/Rx/billing → M2–M3).
- Web tokens in localStorage (planned move to httpOnly cookies).
- Docker stack provided but not executed in the build workspace; CI workflow
  not yet run on GitHub (first push pending).
- No MFA/password reset, no rate limiting, no backups executed.
