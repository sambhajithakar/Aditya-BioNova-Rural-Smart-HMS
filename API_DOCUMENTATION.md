# API Documentation — `POST /api/v1`, `GET /api/v1`, `PATCH`, `DELETE`

Interactive OpenAPI docs (dev): **http://127.0.0.1:8000/api/v1/docs**
Machine spec: `packages/contracts/openapi.json` (regenerate with
`scripts/generate-contracts.sh`; CI fails if stale). Full URL list below is
generated from the same source — 10 paths at M1.

## Conventions

- JSON in/out; `Content-Type: application/json`; errors as
  `{"detail": string | {…}}` (FastAPI validation → `422` with field list).
- Every response carries `X-Request-ID` (correlation id; also in error bodies).
- Auth: `Authorization: Bearer <access_token>` (1800 s TTL).
- Pagination: `limit`/`offset` with `total` (audit list).
- No endpoint accepts a tenant id from the client — org comes from the token.

## Authentication & session

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | /api/v1/auth/register | — | Onboard clinic: user + organization + owner membership (201). 409 duplicate e-mail/slug |
| POST | /api/v1/auth/login | — | Password login (200). 401 generic (no enumeration); 409 → pass `organization_slug` |
| POST | /api/v1/auth/refresh | refresh token (body) | Rotate token pair. Reuse of rotated token ⇒ family revoked + audited + 401 |
| POST | /api/v1/auth/logout | bearer | Revoke session → access token invalid immediately |
| GET | /api/v1/auth/me | bearer | User + organization + role + permissions |

## Domain (M1)

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | /api/v1/branches | branch.read | Tenant-scoped list |
| POST | /api/v1/branches | branch.manage | 201; 409 duplicate code in tenant |
| GET | /api/v1/branches/{id} | branch.read | **Cross-tenant id → 404** |
| GET | /api/v1/members | member.read | Members + roles |
| POST | /api/v1/members | member.manage | 201; `role_key` must be assignable; **owner is grantable only at register** (403); 409 duplicate membership; 422 unknown role/branch |
| GET | /api/v1/audit-events | audit.read | `?action=&limit=&offset=`; tenant-scoped; org-NULL security events not exposed |
| GET | /api/v1/health | — | Status/version/environment |

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
- Idempotency keys: required for payment/order creation (M3).
- Versioning: URL versioning `/api/v1`; breaking changes bump the segment;
  contract snapshot diff reviewed in CI.
