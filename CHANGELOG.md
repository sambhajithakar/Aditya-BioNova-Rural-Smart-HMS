# Changelog

## M6 (2026-10-17) — IPD + operations (API + web)

### Added
- **Wards & beds**: wards per branch (typed: general/ICU/maternity/…)
  and labelled beds whose `status` tracks *physical* availability only
  (`available | maintenance | blocked`) — **occupancy is derived**, never
  stored twice: two partial unique indexes guarantee at most ONE active
  admission per bed and per patient, so a double-booking is refused by the
  database itself (raw-SQL proof in tests), with the API answering 409
  first.
- **Admissions**: admit → transfer → discharge with `Idempotency-Key`
  replay (double submit cannot admit twice); bed transfers write an
  **append-only `admission_transfers` ledger** and move bed + ward together;
  discharge needs a closed-list outcome + a mandatory summary and is the
  ONLY legal transition — a discharged admission is completely frozen and
  can never be deleted (column-level DB trigger; proven with raw SQL).
- **Nursing**: inpatient vitals (range-checked as strictly as OPD vitals,
  SpO₂ < 85 demands a documented escalation note) and free-text records
  that are **append-only clinical evidence** and can only be added while
  the admission is open (DB trigger backstop after discharge).
- **Inventory** (general supplies, mirroring the pharmacy ledger rules):
  item master per branch; every movement is ONE append-only
  `inventory_movements` row, on-hand is trigger-maintained as SUM(in) −
  SUM(out) so negative stock is impossible even via raw SQL; retired items
  are frozen; low-stock alerts follow the reorder level.
- **Expenses**: category/amount/date records whose amounts and dates are
  FROZEN; the only legal change is `recorded → void` with a reason, and
  DELETE is blocked — voided rows stay on the books for audit; per-category
  period summary.
- **Reports**: `GET /reports/summary` (tenant-scoped, `report.read`) —
  IPD occupancy & flows, finance (succeeded payments − refunds − recorded
  expenses, split by category), inventory health.
- **RBAC**: 11 new permissions (`bed.*`, `admission.manage`, `discharge`,
  `nursing.*`, `inventory.*`, `expense.*`, `report.read`) with role grants
  continuing the least-privilege posture: doctor/nurse get the clinical
  set, billing the money set — **support and auditor get zero M6 grants**.
  All 8 new tenant tables carry strict RLS (zero-row proof for a foreign
  tenant context).
- **Web**: `/ipd` (bed board + admit/transfer/discharge/nursing),
  `/inventory`, `/expenses`, `/reports` pages added to the app shell with
  EN/मराठी/हिंदी strings.
- **Tests**: 102/102 (13 new) — uniqueness backstops, state machine +
  freezes, ledger invariants, idempotency, RBAC matrix, cross-tenant 404s
  and raw RLS proofs over all 8 tables.
- **Live verification through the web origin**: admitted → idempotent
  replay → double-book 409 → nursing (+422 out of range) → transfer →
  inventory receive + oversell 409 → expense record + void → discharge +
  frozen-transfer 409 → report figures, all served through the page's own
  `/api/v1` path.

### Not claimed (explicitly)
- Inpatient **day-rate/tariff billing** stays out of scope until the
  pricing model is decided (outpatient billing covers invoicing today).
- Playwright E2E remains pending repo-wide (headless browser in CI).

## M4 (2026-10-17) — Offline-first Android app (Flutter)

### Added
- **Offline core**: every mutation is an `OutboxOp` with a client-generated
  UUID replayed as the server `Idempotency-Key`; one open op per dedupe key
  (double-taps collapse); statuses `pending → inFlight → {applied, failed,
  conflicted}` with explicit UI states **Saved on device · Pending sync ·
  Syncing… · Synced · Conflict**. The engine classifies 2xx / 409-412 /
  422 / 401 / 5xx exactly as the acceptance matrix requires, stops the run
  on 401, and never resolves a conflict by last-write-wins.
- **Encrypted local storage** (R4.2): the whole offline document (outbox,
  directory cache, id map, Rx drafts) is sealed in **AES-256-GCM** — fresh
  random 12-byte nonce per write, `HMS1` magic as additional authenticated
  data, atomic temp-file + rename. The AES key and the session tokens live
  in the **Android Keystore** (`flutter_secure_storage`), never in the
  offline file. Tests prove: round trip, no plaintext byte at rest, tamper
  detection, wrong-key rejection, atomic writes.
- **Offline workflows** in one role-aware binary (Staff app: register →
  queue → billing; Doctor app: Fast Rx):
  - registration with device-side dedupe, immediately usable in the queue;
  - OPD queue entry with per-day dedupe (no double-booking);
  - Fast Rx **draft** from the validated drug catalog only (free-text
    medication is impossible); **signing is online-only** — the API's
    blocking allergy check and `prescription.sign` permission must run
    server-side, so an offline draft can never become a signed Rx;
  - billing: invoice lines queued with idempotency; totals are always
    computed by the server.
- **Local → server id map**: an entity created offline keeps a device id;
  dependent mutations reference it as `$ref:<local>` and the sync engine
  substitutes the real id just before pushing. An unresolved reference
  DEFERS the dependent op (never a bogus id, never the wrong patient), and
  `$ref` never reaches the API (`extra=forbid` models).
- **Restart recovery**: outbox, directory cache, id map and drafts persist
  on every change and are restored by `AppGraph.open()`; `flush()` waits
  for the sealed write. Proven end-to-end: register + queue offline →
  "restart" → one sync pushes both with the server id substituted.
- **Release signing process**: `android/app/build.gradle.kts` reads the
  git-ignored `android/key.properties` (and `*.keystore`/`*.jks` are
  ignored too) — keys can never be committed; a clone without the file
  builds with the debug key and is not distributable.
- **Tests**: 40/40 (18 new) — outbox, engine matrix, id map (substitution,
  deferral), encrypted store, restart recovery, and widget flows for the
  offline registration/queue/Rx/billing slices incl. role gating.

### Not claimed (explicitly)
- On-device QA (airplane-mode visit, restart recovery, duplicate matrix on
  entry-level hardware) is still open — everything above is proven in the
  Dart test harness against a fake transport.
- Notifications, vitals/device capture and the encounter workspace are not
  in the app yet.

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
- **Web**: `/pharmacy` (expiring/expired/low-stock alerts, receive stock,
  FEFO dispensing against a signed prescription or OTC with branch
  inference, batch table, movement ledger) and `/lab` (order creation
  against the validated test catalog, specimen collection with barcodes
  and rejection, import → verify → release → amend worklist) — full
  EN/मराठी/हिंदी strings, navigation entry for both modules.
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
