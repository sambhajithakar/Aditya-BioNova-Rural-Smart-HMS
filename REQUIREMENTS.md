# Requirements — Aditya BioNova Smart HMS

Source: the two official project PDFs (Complete Expanded Blueprint 2026 and
Final Product Layout & Competitor Study 2026) plus the Master Prompt.
`✅` = implemented and tested in this repository; `⬜` = planned milestone.

## 1. Product & packaging
- R1.1 ⬜ One configurable multi-tenant platform for clinics *and* hospitals,
  one shared core with feature flags (packages: Clinic Starter, Clinic Plus,
  Hospital Core, Enterprise — prices configured, never hardcoded).
- R1.2 ✅ Separate repository/deployment from the corporate website (this repo).
- R1.3 ⬜ Customer data ownership: open export formats, documented APIs.

## 2. Tenancy & identity (M1)
- R2.1 ✅ Organizations + branches with server-enforced tenant isolation
  (PostgreSQL row-level security; org identity only from verified tokens).
- R2.2 ✅ Accounts: owner/admin/doctor/nurse/receptionist/pharmacist/
  lab_technician/billing/auditor/support roles with permission catalogue.
- R2.3 ✅ Secure authentication: scrypt password hashing, short-lived JWT
  access tokens, rotating refresh tokens with reuse detection, logout revocation.
- R2.4 ✅ Tamper-evident append-only audit trail for sensitive actions
  (register, login, failed logins, member/branch changes, session reuse).
- R2.5 ⬜ MFA for privileged users, SSO/OIDC (M7), password reset emails.

## 3. Clinical scope (phased)
- R3.1 ✅ M2+M3: patient registration + duplicate detection,
  appointments/tokens, OPD queue (M2); doctor workspace, vitals, structured
  e-prescription (sign/amend) with allergy gate, printable A4 trilingual Rx
  (M3). A5 paper-size print preset is CSS-ready but untested on hardware.
- R3.2 ✅ M3 (minus notifications): billing, invoices, receipts, refunds,
  idempotent payments, append-only ledger, sandbox gateway spike.
  Notifications ⬜ — blocked on the e-mail provider decision (M7 gate).
- R3.3 ⬜ M5: pharmacy (stock/batch/expiry/FEFO), laboratory (orders,
  specimens, two-stage result verification).
- R3.4 ⬜ M6: IPD admissions, wards, beds, nursing, discharge; inventory,
  expenses, reports.
- R3.5 ✅ Non-negotiable clinical rules (from Blueprint): no silent
  post-sign changes (DB-trigger-enforced versioned amendments + audit), no
  medication selection from symptoms (nothing in the code maps symptoms to
  drugs), allergy checks before prescribing (blocking + acknowledged), only
  `prescription.sign` holders (doctor/owner/admin) sign — all tested.

## 4. Offline-first mobile (M4+)
- R4.1 ⬜ Flutter Android app (Doctor + Staff apps) sharing this backend:
  secure login, dashboard, registration, queue, consultation, Fast Rx, vitals,
  billing, notifications.
- R4.2 ⬜ Offline-first: encrypted local SQLite, outbox queue, explicit sync
  states (Saved on device / Pending sync / Synced / Conflict), idempotent
  server operations, conflict queue — **never last-write-wins for
  prescriptions or clinical records**; UPI success only after provider
  confirmation.
- R4.3 ⬜ Performance targets (measured in pilot): local save <2s, return-
  patient lookup ~30s→target faster, standard Rx median <60s, airplane-mode
  visit works, no duplicates after sync retries; entry-level Android hardware.
- R4.4 ⬜ Loss/device policy: encrypted storage, session expiry, remote
  revocation, documented lost-device response.

## 5. Languages & UX
- R5.1 ✅ Language switcher scaffolding with EN/मराठी/हिंदी dictionaries and
  Unicode-safe rendering (full workflow translation at M4 with clinician review).
- R5.2 ✅ Design system: navy/teal/white, high contrast, visible focus rings,
  ≥44px touch targets, responsive layout.
- R5.3 ⬜ Full navigation per Layout PDF (Dashboard, Patients, Queue,
  Consultation, Prescription, Billing, Pharmacy, Vitals & Devices, Health
  Camps, Reports, Settings), KPI dashboard, sync indicator.

## 6. Devices & diagnostics (M8)
- R6.1 ✅ (design only) Device strategy: P0 manual entry with range/unit
  checks and clinician verification; P1 simulator-first BLE/serial gateway;
  P2 lab analyzers (HL7 v2/ASTM); no alarms, no device control, nothing
  claimed as hardware-supported until physically validated.
- R6.2 ⬜ Device registry, observation verification queue, compatibility matrix.

## 7. Commercial SaaS (M7)
- R7.1 ⬜ Plans/entitlements tables, trials, subscriptions (monthly/annual),
  per-facility + per-branch + per-user licensing, tenant export/offboarding,
  support tickets, usage analytics, backup/restore operations.
- R7.2 ⬜ India payment gateway in sandbox first (candidates: Razorpay,
  Cashfree, PhonePe), webhook signature verification + idempotency before any
  real transaction.

## 8. Security, privacy, compliance (continuous)
- R8.1 ✅ M1 baseline: RBAC + tenant RLS + audit + generic auth errors +
  OWASP-aligned inputs; full checklist in SECURITY_CHECKLIST.md.
- R8.2 ⬜ Independent security assessment, DPDP/ABDM/CDSCO expert review,
  backup restore drills, incident response — **no compliance claims until
  independently verified**.
- R8.3 ⬜ ABDM/HL7 FHIR R4 mapping after certification & consent design (P3).

## M1 acceptance (Blueprint §10) — status
- ✅ Synthetic patient... (registration arrives M2; M1 equivalent: org+owner
  onboarding with duplicate-slug/email conflict detection)
- ✅ User from Clinic A cannot read/write Clinic B data by manipulating
  identifiers (automated, incl. raw SQL RLS proofs)
- ✅ Authenticated user sees only their authorized context; RBAC denials tested
- ✅ Audit trail records sensitive actions; append-only enforced by the database
- ✅ Queue/consultation/Rx/billing acceptance tests (M2–M3, incl. the full
  E2E chain register→queue→consult→sign→invoice→pay→receipt)
- ⬜ Backup restorable in staging → M7 (procedure drafted in DEPLOYMENT.md)
