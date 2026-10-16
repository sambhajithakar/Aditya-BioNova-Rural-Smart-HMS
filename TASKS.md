# TASKS — milestone plan & live status

Rule: only *completed = tested* items are checked. Simulated, blocked and
pending work is labelled explicitly. (M1 scope approved by the product owner;
implementation sessions continue milestone by milestone.)

## M1 — Foundation + secure multi-tenant core ✅ (2026-10-12)
- [x] Isolated repository `Aditya-BioNova-Smart-HMS` (existing
      website/OncoTrial untouched)
- [x] Monorepo: apps/api, apps/web, packages/contracts, infra/docker, CI
- [x] Postgres schema + Alembic migrations 0001–0004
- [x] RLS tenant isolation (non-owner app role) + append-only audit + RBAC seed
- [x] Auth: register/login/refresh-rotation+reuse-detection/logout/me
- [x] Branches, members, audit endpoints with permission guards
- [x] Web: login + dashboard shell, EN/मराठी/हिंदी switcher, design system
- [x] Tests: 33/33 (auth, RBAC, cross-tenant API + raw RLS proofs)
- [x] Docs: all 11 required files + SBOM/licence register
- [ ] ⏳ Dev-server preview sign-off (browser check by owner)
- [ ] ⏳ Push to customer's GitHub org + first GitHub Actions run

## M2 — Registration → Appointments → OPD queue ✅ (2026-10-13)
- [x] patients (+ supervised duplicate detection/merge), patient profile
- [x] appointments (provider-conflict detection, reschedule/cancel, frozen
      terminal states), tokens, OPD queue state machine, doctor's
      authorized queue view
- [x] Web pages: /patients (search + duplicate-aware registration + profile),
      /appointments (booking + list), /queue (live board with
      call/start/complete/skip)
- [x] i18n: full M2 workflow strings in EN/मराठी/हिंदी
- [x] API pagination ({items,total,limit,offset}) + Idempotency-Key on
      POST /patients and POST /opd/queue; contracts regenerated (20 paths)
- [x] Tests: 48/48 — includes cross-tenant 404s, forged-header test,
      RBAC matrix (support/auditor see NO patient data), raw RLS proofs,
      queue state-machine + idempotency-replay proofs
- [ ] ⏳ Playwright E2E (needs a headless browser in CI; starts M3)
- [ ] ⏳ Password reset e-mail (needs e-mail provider decision — see M7)
- [ ] ⏳ Translation QA review by a native speaker (plan in DEPLOYMENT.md)

## M3 — Consultation → Prescription → Billing → Receipt ✅ (2026-10-13)
- [x] encounters (+ one-open-per-patient guard), range-checked vitals,
      structured diagnosis; completed encounters frozen by DB trigger
- [x] signed Rx with versioned amendments — immutability enforced by DB
      trigger (only legal post-sign change: signed → superseded), audited;
      allergy check blocks signing unless explicitly acknowledged;
      duplicate-drug warnings advisory; items must reference the validated
      `drug_catalog` (45 seeded drugs, no free-text medication entry)
- [x] invoices (server-computed totals, per-tenant INV-000001 numbering,
      freeze-on-issue), receipts, refunds with bounds, append-only ledger
      (trigger + revoked grants + unique partial indexes vs double-posting);
      `Idempotency-Key` on payment creation; sandbox payment gateway spike
      (pending → confirm/fail callbacks)
- [x] printable A4 trilingual Rx + receipt (`/print/rx/[id]`, browser
      print/PDF pipeline); full E2E acceptance chain tested
- [x] Web pages: /consultation (vitals, diagnosis, safety-gated signing),
      /billing (invoice → issue → payment → receipt), print view —
      EN/मराठी/हिंदी strings
- [x] Tests: 72/72 — cross-tenant 404s for every new table, raw RLS
      proofs, signed-Rx/ledger/invoice-freeze raw-SQL immutability proofs,
      RBAC matrix (support/auditor see no clinical or financial data),
      idempotency replay, refund bounds, E2E chain audit assertions
- [ ] ⏳ Playwright E2E (needs a headless browser in CI — still pending)
- [ ] ⏳ Real payment gateway in sandbox (Razorpay/Cashfree/PhonePe
      shortlist) — M7 gate; M3 ships the local sandbox simulation only

## M4 — Android app + offline core (Flutter)
- [ ] app shells (Doctor/Staff), secure login, encrypted SQLite + outbox
- [ ] registration/queue/Rx-draft/billing workflows offline; conflict queue
      (no last-write-wins on clinical data); sync-centre UI
- [ ] airplane-mode, restart-recovery, duplicate-prevention test matrix on
      entry-level devices; signed release build process (keys never committed)

## M5 — Pharmacy + Laboratory
- [ ] stock/batch/expiry/FEFO, dispensing permissions
- [ ] lab orders/specimens/results with imported→verified→released states

## M6 — IPD + operations
- [ ] wards/beds/nursing/discharge flow; inventory, expenses, reports

## M7 — SaaS commercial layer + hardening
- [ ] plans/entitlements/trials/subscriptions, tenant export/offboarding
- [ ] payment gateway in sandbox (Razorpay/Cashfree/PhonePe shortlist)
- [ ] MFA, e-mail delivery, rate limiting, backups+restore drill, monitoring,
      dependency vuln scanning, support tickets

## M8 — Devices (simulator-first) + performance
- [ ] device registry, observation verification queue, BLE simulator harness,
      compatibility matrix; barcode workflows; p95 latency measurement

## M9 — Release readiness (human gates)
- [ ] independent security review, clinical validation, legal (DPDP/ABDM/CDSCO)
      review, supervised pilot 5–10 clinics, release documentation
- [ ] **Do not claim production-ready until these gates pass.**

## Backlog / decisions pending owner
- Edition strategy confirmation (one backend, two frontends)
- Pilot BP/SpO2 device model for physical validation (M8)
- Hosting provider + payment provider final selection
- E-mail provider for password reset + appointment reminders (M7 gate)
- M3 start approval: consultation → prescription → billing → receipt
