# Database Schema — PostgreSQL 16 (M2)

Migrations: `apps/api/alembic/versions/`
- `0001_initial_schema` — tables, app role, grants, RLS policies, audit trigger
- `0002_seed_rbac` — permission catalogue + 10 system roles
- `0003_audit_insert_function` — `insert_audit_event()` SECURITY DEFINER helper
- `0004_fix_auditor_role` — auditor corrected to true read-only
- `0005_patients_appointments_queue` — patients/appointments/opd_queue +
  idempotency_keys, clinical RBAC seed (7 permissions)

Run: `cd apps/api && alembic upgrade head` (uses `MIGRATION_DATABASE_URL`,
the **table-owner** role). Runtime queries use `DATABASE_URL` (`hms_app`,
non-owner → RLS enforced).

## Tables

### organizations (RLS ✚ bootstrap-gated writes)
| column | type | notes |
|---|---|---|
| id | uuid PK | `gen_random_uuid()` |
| name | varchar(200) | |
| slug | varchar(80) UNIQUE | URL/tenant key, lowercase |
| status | varchar(20) | active/suspended/deleting (ck_org_status) |
| created_at / updated_at | timestamptz | UTC |

### branches (RLS)
id uuid PK · organization_id FK→organizations CASCADE · name · code ·
status · created_at — **UNIQUE (organization_id, code)**; index
`ix_branches_org_created (organization_id, created_at)`.

### users (**no RLS — see ARCHITECTURE**)
id uuid PK · email varchar(320) **UNIQUE** (case-normalised in app) ·
full_name · password_hash (scrypt string, never serialized) · status ·
is_superadmin · timestamps.

### roles / permissions / role_permissions (reference, read-only for app)
`permissions.key` PK: org.read, org.manage, org.transfer_ownership (reserved),
branch.read, branch.manage, member.read, member.manage, audit.read, plus the
M2 clinical set: patient.read, patient.write, patient.merge,
appointment.read, appointment.write, queue.read, queue.manage.
`roles.key`: owner, admin, doctor, nurse, receptionist, pharmacist,
lab_technician, billing, auditor, support. **auditor is read-only**
(corrected in 0004). M2 clinical grants: owner/admin all 7;
receptionist patient+appointment+queue (write/manage); doctor
patient.read + appointment.read + queue.read/manage; nurse
patient.read + appointment.read + queue.read; billing
patient.read + appointment.read; pharmacist/lab patient.read.
**support and auditor have NO patient-data permissions** (tested).

### memberships (RLS)
id uuid PK · user_id FK CASCADE · organization_id FK CASCADE · branch_id FK
SET NULL (nullable) · role_id FK RESTRICT · status · created_at —
**UNIQUE (user_id, organization_id)**; index `ix_memberships_org`.

### auth_sessions (**no RLS — token-gated**)
id uuid PK (= `sid` in access JWT) · family_id (rotation family, indexed) ·
user_id FK · organization_id FK · token_hash varchar(64) UNIQUE (SHA-256 of
opaque refresh token — plaintext shown once) · issued_at · expires_at ·
revoked_at (null = live) · replaced_by (rotation chain) · user_agent · ip.

### audit_events (RLS **✚ append-only**)
id bigint **IDENTITY** PK · organization_id FK SET NULL nullable (NULL =
system/security event, tenant-invisible) · actor_user_id FK SET NULL ·
action varchar(80) · resource_type · resource_id · metadata jsonb ·
correlation_id · ip_address · created_at — indexes
`ix_audit_org_created`, `ix_audit_action`.

Immutability (three independent layers):
1. no UPDATE/DELETE row policies (RLS default-deny for `hms_app`)
2. `UPDATE`/`DELETE` grants revoked from `hms_app`
3. `BEFORE UPDATE OR DELETE` trigger → `RAISE EXCEPTION 'append-only'`
   (fires even for the table owner — tested)


### patients (RLS) — M2
id uuid PK · organization_id FK CASCADE · **uhid varchar(20)** · full_name ·
gender (male/female/other/unknown) · date_of_birth date nullable +
dob_estimated bool · phone varchar(20) normalized digits · address ·
blood_group (8 values, nullable) · allergies text · status
(active/inactive/merged) · merged_into_id FK→patients SET NULL ·
created_by FK · timestamps.

- **UNIQUE (organization_id, uhid)** — UHID is tenant-scoped, never global.
- UHID `P0000001` allocated under a per-org `pg_advisory_xact_lock`
  (concurrent registrations cannot collide; constraint is the backstop).
- Duplicate detection is **supervised**: registration matches on normalized
  phone OR (lower(name), dob) inside the tenant; candidates are returned and
  creation needs `allow_duplicates=true` (audited).
- Merge is non-destructive: source keeps its UHID, gets
  `merged_into_id = survivor`, status `merged`, becomes read-only.

### appointments (RLS) — M2
id uuid PK · organization_id FK · branch_id FK RESTRICT · patient_id FK
RESTRICT · provider_user_id FK SET NULL nullable · starts_at/ends_at
timestamptz (**ends_at > starts_at**) · reason · status
(scheduled/checked_in/completed/cancelled/no_show) · notes · created_by ·
timestamps. Indexes: (org, starts_at), (org, patient_id),
(org, provider, starts_at). Provider double-booking in an overlapping
window → 409 (service layer).

### opd_queue (RLS) — M2
id uuid PK · organization_id FK · branch_id FK RESTRICT · patient_id FK
RESTRICT · appointment_id FK SET NULL nullable · service_date date ·
token_number int · status (waiting/called/in_consultation/completed/
skipped/cancelled) · priority (normal/urgent) · doctor_user_id FK SET NULL ·
called_at/started_at/completed_at · created_by · timestamps.
**UNIQUE (organization_id, service_date, token_number)**; tokens allocated
under a per-(org, day) advisory lock. State machine enforced in the service
layer: waiting→called→in_consultation→completed; waiting/called may be
skipped or cancelled; skipped may be recalled (called); terminal states
never move (409 on illegal transitions).

### idempotency_keys (RLS) — M2
id uuid PK · organization_id FK · idempotency_key varchar(100) · endpoint ·
request_hash (SHA-256 of canonical payload) · response_status ·
response_body jsonb · created_at. **UNIQUE (organization_id,
idempotency_key)**. Retry with same payload replays the stored response;
same key + different payload → 409. Used by POST /patients and
POST /opd/queue (M4 offline sync depends on this).

## Row-level security policies

GUC expressions (set per transaction by `set_request_context()`):
`GUC_ORG = nullif(current_setting('app.current_org_id', true),'')::uuid`,
`GUC_USER` likewise. **All values come from verified tokens only.**

| table | cmd | policy | rule |
|---|---|---|---|
| organizations | SELECT | org_select_member | `id = GUC_ORG` OR membership exists for `GUC_USER` |
| organizations | INSERT | org_insert_bootstrap | `app.is_bootstrap = 'true'` AND id claimed via `GUC_ORG` |
| organizations | UPDATE | org_update_member | active membership exists |
| branches | ALL | branch_tenant | `organization_id = GUC_ORG` (USING + WITH CHECK) |
| memberships | SELECT | membership_select | `user_id = GUC_USER` OR `organization_id = GUC_ORG` |
| memberships | INSERT/UPDATE/DELETE | … | `organization_id = GUC_ORG` |
| audit_events | SELECT | audit_select | `organization_id = GUC_ORG` (NULL-org rows: operators only) |
| audit_events | INSERT | audit_insert | `organization_id IS NULL OR = GUC_ORG` |
| patients | ALL | patients_tenant | `organization_id = GUC_ORG` (USING + WITH CHECK) |
| appointments | ALL | appointments_tenant | `organization_id = GUC_ORG` |
| opd_queue | ALL | opd_queue_tenant | `organization_id = GUC_ORG` |
| idempotency_keys | ALL | idempotency_keys_tenant | `organization_id = GUC_ORG` |
| encounters, encounter_vitals, encounter_diagnoses | ALL | `<table>_tenant` | `organization_id = GUC_ORG` |
| prescriptions, prescription_items | ALL | `<table>_tenant` | `organization_id = GUC_ORG` |
| invoices, invoice_items, payments, refunds | ALL | `<table>_tenant` | `organization_id = GUC_ORG` |
| ledger_entries | SELECT/INSERT only (UPDATE/DELETE revoked) | `ledger_entries_tenant` | `organization_id = GUC_ORG` |

Write-guard for audit rows is also enforced inside
`insert_audit_event()` (SECURITY DEFINER), so a compromised app role still
cannot forge another tenant's audit history.

## Integrity rules

- UUID primary keys; unique constraints (tenant UHID arrives with M2 patients).
- FKs tenant-consistent via composite policy checks + service-layer scoping;
  membership branch FK validates against the caller's tenant (422 otherwise).
- All timestamps `timestamptz` UTC; display timezone handling arrives with
  scheduling (M2).
- Soft delete: none yet; `status` columns instead. Deletion policies to be
  defined with legal retention requirements.

## Indexes added beyond constraints
`ix_branches_org_created`, `ix_memberships_org`, `ix_auth_sessions_family`,
`ix_auth_sessions_user_org`, `ix_audit_org_created`, `ix_audit_action`.
Rule: measure query plans on real workload before adding more (no
speculative over-indexing).

## M3 tables (migration 0006)

| Table | Purpose | RLS policy |
|---|---|---|
| `encounters` | OPD consultation (open → completed) | `encounters_tenant` |
| `encounter_vitals` | BP/pulse/Temp/SpO₂/RR/weight/height with range CHECKs | `encounter_vitals_tenant` |
| `encounter_diagnoses` | structured diagnosis (code, label, primary/secondary, suspected/confirmed/ruled_out) | `encounter_diagnoses_tenant` |
| `drug_catalog` | **global** validated drug reference (45 seeded rows), read-only for `hms_app` | none (reference data) |
| `prescriptions` | versioned Rx: draft → signed → superseded | `prescriptions_tenant` |
| `prescription_items` | one row per medicine, FK to `drug_catalog` (free-text entry impossible) | `prescription_items_tenant` |
| `invoices` + `invoice_items` | billing document; totals computed server-side | `invoices_tenant` / `invoice_items_tenant` |
| `payments` + `refunds` | manual or `sandbox` gateway, state machine pending→succeeded/failed, refund bounds | `payments_tenant` / `refunds_tenant` |
| `ledger_entries` | append-only financial ledger | `ledger_entries_tenant` |

**Immutability guarantees (DB triggers, proven by raw-SQL tests):**

- `trg_rx_immutable` — a signed prescription cannot change: the only legal
  UPDATE is `signed → superseded` with every other column byte-identical
  (amendments therefore insert version N+1); DELETE blocked for non-draft.
- `trg_rxitem_immutable` — items follow their prescription's status.
- `trg_enc_completed_immutable` — a completed encounter is frozen.
- `trg_inv_issued_frozen` / `trg_invitem_frozen` — invoice financials and
  line items freeze on issue.
- `trg_ledger_append_only` + `REVOKE UPDATE, DELETE` — the ledger is
  append-only exactly like `audit_events`; unique partial indexes
  (`uq_ledger_charge_per_invoice`, `uq_ledger_payment_once`,
  `uq_ledger_refund_once`, `uq_ledger_void_per_invoice`) make double
  posting impossible even under races.

**M3 RBAC:** 8 permissions (`encounter.read/write`, `prescription.read/sign`,
`billing.read/invoice/payment/refund`) across 6 roles. Only doctor/owner/admin
hold `prescription.sign`; support and auditor receive **no** M3 permissions.

## Known schema follow-ups (M4+)
- `updated_at` triggers (currently app-managed).
- FHIR/ABDM mapping tables when that phase starts.
- Timezone: `service_date` uses the UTC day; facility-local day boundary
  lands with scheduling polish.
