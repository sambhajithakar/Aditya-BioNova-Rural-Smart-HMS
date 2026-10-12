# TASKS — milestone plan & live status

Rule: only *completed = tested* items are checked. Simulated, blocked and
pending work is labelled explicitly. (M1 scope approved by the product owner;
implementation sessions continue milestone by milestone.)

## M1 — Foundation + secure multi-tenant core ✅ (2026-10-12)
- [x] Isolated repository `Aditya-BioNova-Rural-Smart-HMS` (existing
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

## M2 — Registration → Appointments → OPD queue (next)
- [ ] patients (+ supervised duplicate detection/merge), patient profile
- [ ] appointments, tokens, OPD queue, doctor's authorized queue view
- [ ] Web pages for reception/doctor workflows; Playwright E2E starts here
- [ ] i18n full workflow strings + translation QA plan; password reset e-mail
- [ ] API pagination/idempotency conventions hardened; contracts typed clients

## M3 — Consultation → Prescription → Billing → Receipt
- [ ] encounters, vitals, structured diagnosis; signed Rx with immutable
      amendments (+ audit); allergy/duplicate-drug warnings (validated data)
- [ ] invoices/receipts/refunds with append-only financial ledger;
      idempotency keys on payment creation; sandbox payment gateway spike
- [ ] printable A4/A5 trilingual Rx/PDF; full E2E acceptance chain tested

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
- Pilot BP/Sp2 device model for physical validation (M8)
- Hosting provider + payment provider final selection
