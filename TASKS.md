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

## M4 — Android app + offline core (Flutter) ✅ (2026-10-17) — device QA pending
- [x] app shells: ONE binary with two role-aware apps (Staff = register →
      queue → billing; Doctor = queue → Fast Rx), switched by the role the
      server reports on `/auth/me`; secure login against the real
      `POST /auth/login` contract (failures surface the server's `detail`)
- [x] encrypted local storage: the whole offline state is one AES-256-GCM
      sealed file (fresh random nonce per write, `HMS1` as AAD, atomic
      temp-file + rename writes); the AES key and the session tokens live
      in the Android Keystore (`flutter_secure_storage`) — no PHI and no
      token is readable at rest on a lost/stolen device
- [x] offline workflows through the outbox: **registration, OPD queue,
      Fast Rx draft (validated drug catalog only — never free-text
      medication), billing** — every mutation carries a client UUID as the
      server `Idempotency-Key`, and per-workflow dedupe keys collapse a
      double-tap into ONE op (no double-booking, no double invoice)
- [x] local → server id map: a patient registered offline gets a device id;
      dependent ops reference it as `$ref:<local>` and the sync engine
      swaps in the server id before pushing — a still-unresolved reference
      DEFERS the op instead of ever attaching a queue entry/Rx/invoice to
      the wrong patient; `$ref` never reaches the API
- [x] conflict queue (no last-write-wins): 409/412 and policy rejections
      park the op in the sync centre with explicit "Keep local (re-send)" /
      "Discard" resolution; explicit sync states per queued item —
      Saved on device · Pending sync · Syncing… · Synced · Conflict
- [x] restart recovery: outbox, directory cache, id map and Rx drafts are
      persisted on every change and restored by `AppGraph.open()`; signing
      a prescription is online-only (the server's blocking allergy check
      and `prescription.sign` permission must see the patient first)
- [x] signed release build process: `android/app/build.gradle.kts` reads
      `android/key.properties`; `key.properties`, `*.keystore` and `*.jks`
      are git-ignored (verified), so keys can never enter the repo; without
      the file the build falls back to the debug key and says so
- [x] Tests: 40/40 — outbox dedupe/serialize/restore, sync-engine outcome
      matrix, id-map substitution + deferral, encrypted store (round trip,
      NO plaintext at rest, tamper and wrong-key detection, atomic writes),
      end-to-end restart recovery, and the offline registration/queue/Rx/
      billing widget flows (including the role gate and sign-after-sync)
- [ ] ⏳ Device QA: airplane-mode visit, restart recovery and the duplicate
      matrix on entry-level Android hardware — proven in the Dart test
      harness against a fake transport, NOT yet on physical devices
- [ ] ⏳ Notifications, vitals/device capture and the encounter workspace
      on mobile (web already covers them; they reuse this outbox/engine)

## M5 — Pharmacy + Laboratory ✅ (2026-10-16)
- [x] stock in batches with explicit expiry against the validated drug
      catalog (no free-text stock); receiving writes the batch AND its
      opening ledger row; FEFO allocation locks batches in expiry order;
      reason-coded adjustments; expiring/expired/low-stock alert report
- [x] DB-enforced stock integrity: append-only `stock_movements` ledger with
      trigger-maintained on-hand (SUM(in)-SUM(out)), expired stock cannot
      be dispensed, recalled batches frozen, dispense documents immutable
- [x] dispensing against SIGNED prescriptions only (items must be on the
      Rx; OTC against a registered patient); `Idempotency-Key` replay
- [x] lab orders → specimens (unique per-tenant barcode, reject transition)
      → results with the imported → verified → released state machine;
      FOUR-EYES verification (verifier ≠ importer); released results are
      immutable — supersession-only amendments (deferrable FK + partial
      unique index on live results); released/cancelled orders frozen
- [x] RBAC for all 8 M5 permissions (pharmacist & lab_technician roles;
      least privilege for support/auditor); cross-tenant 404s; raw RLS
      proofs for all 7 new tables
- [x] Tests: 89/89 (17 M5) — FEFO split across batches, ledger invariant,
      trigger backstops proven with raw SQL, RBAC matrix, four-eyes,
      amendment supersession, idempotency replay
- [x] Web pages: /pharmacy (expiry/low-stock alerts, receive stock, FEFO
      dispensing against a signed Rx or OTC, batch & movement ledger) and
      /lab (order creation against the test catalog, specimen barcodes,
      import → verify → release → amend worklist) — EN/मराठी/हिंदी
- [x] Live flow verified through the web origin (page's own API path):
      receive → alerts → OTC dispense with branch inference; lab order →
      specimen → import → four-eyes 403 → second-person verify → release

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
