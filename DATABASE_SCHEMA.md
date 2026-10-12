# Database Schema — PostgreSQL 16 (M1)

Migrations: `apps/api/alembic/versions/`
- `0001_initial_schema` — tables, app role, grants, RLS policies, audit trigger
- `0002_seed_rbac` — permission catalogue + 10 system roles
- `0003_audit_insert_function` — `insert_audit_event()` SECURITY DEFINER helper
- `0004_fix_auditor_role` — auditor corrected to true read-only

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
branch.read, branch.manage, member.read, member.manage, audit.read.
`roles.key`: owner, admin, doctor, nurse, receptionist, pharmacist,
lab_technician, billing, auditor, support. 41 seeded mappings; **auditor is
read-only** (corrected in 0004).

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

## Known schema follow-ups (M2+)
- patients/UHID, appointments, encounters, prescriptions (Blueprint §6),
  with composite FKs enforcing tenant consistency.
- `updated_at` triggers (currently app-managed).
- FHIR/ABDM mapping tables when that phase starts.
