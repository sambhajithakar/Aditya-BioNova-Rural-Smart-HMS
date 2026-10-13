# API Documentation — `POST /api/v1`, `GET /api/v1`, `PATCH`, `DELETE`

Interactive OpenAPI docs (dev): **http://127.0.0.1:8000/api/v1/docs**
Machine spec: `packages/contracts/openapi.json` (regenerate with
`scripts/generate-contracts.sh`; CI fails if stale). Full URL list below is
generated from the same source — 42 paths at M3.

## Conventions

- JSON in/out; `Content-Type: application/json`; errors as
  `{"detail": string | {…}}` (FastAPI validation → `422` with field list).
- Every response carries `X-Request-ID` (correlation id; also in error bodies).
- Auth: `Authorization: Bearer <access_token>` (1800 s TTL).
- Pagination: `limit`/`offset` with `total` (`{items,total,limit,offset}`
  page shape on all list endpoints).
- Idempotency: POSTs /patients and /opd/queue accept an `Idempotency-Key`
  header — a retry with the same payload replays the stored response;
  same key + different payload → 409.
- No endpoint accepts a tenant id from the client — org comes from the token.

## Authentication & session

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | /api/v1/auth/register | — | Onboard clinic: user + organization + owner membership (201). 409 duplicate e-mail/slug |
| POST | /api/v1/auth/login | — | Password login (200). 401 generic (no enumeration); 409 → pass `organization_slug` |
| POST | /api/v1/auth/refresh | refresh token (body) | Rotate token pair. Reuse of rotated token ⇒ family revoked + audited + 401 |
| POST | /api/v1/auth/logout | bearer | Revoke session → access token invalid immediately |
| GET | /api/v1/auth/me | bearer | User + organization + role + permissions |

## Domain (M1) — org/branch/member/audit

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | /api/v1/branches | branch.read | Tenant-scoped list |
| POST | /api/v1/branches | branch.manage | 201; 409 duplicate code in tenant |
| GET | /api/v1/branches/{id} | branch.read | **Cross-tenant id → 404** |
| GET | /api/v1/members | member.read | Members + roles |
| POST | /api/v1/members | member.manage | 201; `role_key` must be assignable; **owner is grantable only at register** (403); 409 duplicate membership; 422 unknown role/branch |
| GET | /api/v1/audit-events | audit.read | `?action=&limit=&offset=`; tenant-scoped; org-NULL security events not exposed |
| GET | /api/v1/health | — | Status/version/environment |


## Clinical (M2)

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | /api/v1/patients | patient.write | 201 with tenant UHID (`P0000001`…). **Duplicate check**: phone OR name+dob match → 409 + `candidates[]` unless `allow_duplicates=true` (audited). Supports `Idempotency-Key` |
| GET | /api/v1/patients | patient.read | `?q=&limit=&offset=` — search by name/UHID/phone; merged records excluded |
| GET | /api/v1/patients/{id} | patient.read | **Cross-tenant id → 404** |
| PATCH | /api/v1/patients/{id} | patient.write | 200; merged record → 409 (read-only); audited with changed fields |
| POST | /api/v1/patients/{id}/merge | patient.merge | Supervised dedupe: source → `merged` + `merged_into_id`; non-destructive; self-merge / double-merge → 409; audited |
| POST | /api/v1/appointments | appointment.write | 201; ends≤starts → 422; foreign patient/branch → **404**; provider overlap → 409 |
| GET | /api/v1/appointments | appointment.read | `?from_date=&to_date=&patient_id=&status_filter=&limit=&offset=` |
| GET | /api/v1/appointments/{id} | appointment.read | Cross-tenant id → 404 |
| PATCH | /api/v1/appointments/{id} | appointment.write | Reschedule/cancel; completed/cancelled → 409 (frozen); conflict re-check on time/provider change; audited |
| POST | /api/v1/opd/queue | queue.manage | 201 with daily token (advisory-lock serialized); patient already active today → 409; supports `Idempotency-Key` |
| GET | /api/v1/opd/queue | queue.read | `?service_date=&status_filter=` — doctor's/reception's tenant-scoped board |
| POST | /api/v1/opd/queue/{id}/call | queue.manage | waiting→called (records `called_at`, optional doctor) |
| POST | /api/v1/opd/queue/{id}/start | queue.manage | called→in_consultation |
| POST | /api/v1/opd/queue/{id}/complete | queue.manage | in_consultation→completed (terminal) |
| POST | /api/v1/opd/queue/{id}/skip | queue.manage | waiting/called→skipped (recallable) |

Illegal queue transitions (e.g. completed→called, waiting→in_consultation)
→ **409**. Every transition is audited (`opd.queue_*`).

### Clinical RBAC matrix (seeded in migration 0005)

| Role | patient.write | patient.merge | appointment.write | queue.manage |
|---|---|---|---|---|
| owner / admin | ✅ | ✅ | ✅ | ✅ |
| receptionist | ✅ | — | ✅ | ✅ |
| doctor | — (read only) | — | — (read only) | ✅ |
| nurse / billing | — (read only) | — | — (read only) | — |
| pharmacist / lab | patient.read only | — | — | — |
| auditor / support | **none** | — | — | — |


## Consultation, prescription & billing (M3)

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | /api/v1/encounters | encounter.write | 201 opens a consultation. One OPEN encounter per patient → 409. Linked queue entry must be `called`/`in_consultation` (else 409) and advances to `in_consultation` |
| GET | /api/v1/encounters | encounter.read | `?patient_id=&enc_status=&limit=&offset=` |
| GET | /api/v1/encounters/{id} | encounter.read | Detail with vitals[], diagnoses[], prescription_ids[]. Cross-tenant → 404 |
| PATCH | /api/v1/encounters/{id} | encounter.write | Edit while open; completed → **409** (DB trigger) |
| POST | /api/v1/encounters/{id}/vitals | encounter.write | Range-checked (schema + CHECK constraints); systolic ≤ diastolic → 422; completed encounter → 409 |
| POST | /api/v1/encounters/{id}/diagnoses | encounter.write | 201 structured diagnosis (code/label/kind/status) |
| POST | /api/v1/encounters/{id}/complete | encounter.write | Closes the encounter and completes its linked queue entry (audited) |
| GET | /api/v1/prescriptions/drugs?q= | prescription.read | Validated `drug_catalog` search (global reference data) |
| POST | /api/v1/prescriptions | prescription.sign | 201 draft. Items must reference the catalog → 422 `Unknown or inactive drug(s)` (no free-text meds) |
| POST | /api/v1/prescriptions/{id}/warnings | prescription.sign | Pre-flight safety check → `{warnings[], blocking}` — allergy hits block, duplicate-drug warns |
| GET | /api/v1/prescriptions | prescription.read | `?patient_id=&rx_status=` |
| GET | /api/v1/prescriptions/{id} | prescription.read | Detail with items[] |
| POST | /api/v1/prescriptions/{id}/sign | prescription.sign | draft→signed. Allergy conflict → **409** + warnings unless `acknowledge_allergies=true` (ack persisted + audited). Already signed → 409 |
| POST | /api/v1/prescriptions/{id}/amend | prescription.sign | 201 new version N+1 (`supersedes_id`), immediately signed; old row → `superseded`. Amending a superseded row → 409; draft → 409 (edit it directly) |
| POST | /api/v1/billing/invoices | billing.invoice | 201 draft. **Totals computed server-side** from items; `discount > subtotal` → 422; per-tenant number `INV-000001` (advisory lock) |
| POST | /api/v1/billing/invoices/{id}/issue | billing.invoice | draft→issued; posts the `charge` ledger debit; re-issue → 409; zero-total → 422 |
| POST | /api/v1/billing/invoices/{id}/void | billing.invoice | Only unpaid draft/issued; paid → 409 (refund first). Issued→void posts reversing `adjustment` credit |
| GET | /api/v1/billing/invoices | billing.read | `?patient_id=&inv_status=` |
| GET | /api/v1/billing/invoices/{id} | billing.read | Detail with items[] + payments[] |
| POST | /api/v1/billing/invoices/{id}/payments | billing.payment | 201. Supports `Idempotency-Key` (replay → same payment; different payload → 409). Amount > balance → 422; draft invoice → 409. `gateway=manual` settles immediately; `gateway=sandbox` stays `pending` |
| POST | /api/v1/billing/payments/{id}/confirm | billing.payment | Sandbox gateway callback: pending→succeeded, credits the ledger once (double-confirm → 409) |
| POST | /api/v1/billing/payments/{id}/fail | billing.payment | Sandbox decline: pending→failed, no ledger credit, balance unchanged |
| POST | /api/v1/billing/payments/{id}/refund | billing.refund | 201 refund ≤ (paid − already refunded), else 422; posts a `refund` debit; full refund → payment `refunded`, invoice back to `issued` |
| GET | /api/v1/billing/invoices/{id}/ledger | billing.read | Append-only ledger rows for the invoice |
| GET | /api/v1/billing/invoices/{id}/receipt | billing.read | Printable receipt (invoice + items + payments + refunds + ledger + balance). Draft → 409 |
| GET | /api/v1/billing/ledger | billing.read | Tenant ledger, newest first |

### M3 RBAC matrix (migration 0006)

| Role | encounter.write | prescription.sign | billing.invoice/payment | billing.refund |
|---|---|---|---|---|
| owner / admin | ✅ | ✅ | ✅ | ✅ |
| doctor | ✅ | ✅ | — (billing.read only) | — |
| nurse | ✅ | — | — | — |
| receptionist | — | — | ✅ | — |
| billing | — | — | ✅ | ✅ |
| pharmacist | — | — | — | — (prescription.read only) |
| auditor / support | **none** | **none** | **none** | **none** |

## Example session

```bash
BASE=http://127.0.0.1:8000/api/v1

# 1) onboard
curl -s -X POST $BASE/auth/register -H 'Content-Type: application/json' -d '{
  "organization_name":"Sunrise Clinic","organization_slug":"sunrise-clinic",
  "full_name":"Dr. A. Sharma","email":"owner@sunrise-clinic.example.com",
  "password":"Str0ngPassphrase!"}'

# 2) login (keep access_token)
curl -s -X POST $BASE/auth/login -H 'Content-Type: application/json' -d '{
  "email":"owner@sunrise-clinic.example.com",
  "password":"Str0ngPassphrase!","organization_slug":"sunrise-clinic"}'

# 3) authenticated call
curl -s $BASE/branches -H "Authorization: Bearer $ACCESS"

# 4) rotate (old refresh token is dead afterwards)
curl -s -X POST $BASE/auth/refresh -H 'Content-Type: application/json' \
  -d "{\"refresh_token\":\"$REFRESH\"}"
```

## Status codes

`200` ok · `201` created · `401` bad/expired credentials or revoked session ·
`403` authenticated but missing permission/membership · `404` not found
(includes cross-tenant ids — no existence leak) · `409` conflict ·
`422` validation · `500` unexpected (generic body + correlation id).

## Rate limiting, idempotency, versions

- Rate limiting: ingress-level (deployment concern, M7) — not yet applied.
- Idempotency keys: implemented for POST /patients, POST /opd/queue and
  POST /billing/invoices/{id}/payments.
- Versioning: URL versioning `/api/v1`; breaking changes bump the segment;
  contract snapshot diff reviewed in CI.
