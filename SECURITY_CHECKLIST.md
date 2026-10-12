# Security Checklist — M1 baseline (OWASP ASVS 5.0-oriented)

**Legend:** ✅ implemented + tested · 🟡 partial/documented limitation · ⬜ pending.
This checklist is *engineering self-assessment only*. Independent security
review, DPDP/ABDM/CDSCO legal assessment and clinical safety review are
**required before any real patient data** — no compliance claim is made here.

## Authentication
- ✅ scrypt password hashing (N=16384, per-password salt, constant-time compare)
- ✅ Minimum 10-char passwords (length-only policy per ASVS)
- ✅ Generic 401 for unknown e-mail *and* wrong password; timing equalised
  with dummy hash (no account enumeration — tested)
- ✅ Short-lived access JWT (30 min) + rotating refresh tokens (14 d),
  SHA-256 stored, **reuse detection revokes the whole session family** (tested)
- ✅ Logout/revocation effective immediately (session row re-checked per request — tested)
- ✅ JWT signature/issuer/expiry/type validated; wrong-key and tampered tokens 401 (tested)
- 🟡 MFA, password reset, lockout policies → M7 (needs e-mail delivery)

## Authorization & tenancy
- ✅ RBAC permission checks server-side per route (tested incl. negative 403s)
- ✅ Tenant identity only from verified token; forged `X-Org-Id` ignored (tested)
- ✅ PostgreSQL RLS as non-owner role — cross-tenant read/write denied at DB
  layer (raw-SQL proofs: no context → 0 rows; wrong-org INSERT → denied)
- ✅ Cross-tenant identifier manipulation → 404 (IDOR/BOLA, tested)
- ✅ `owner` role cannot be granted through the API (tested)
- 🟡 `users`/`auth_sessions` have no RLS (auth bootstrap) — mitigations in
  ARCHITECTURE.md; no endpoint exposes password hashes (tested)
- ⬜ Per-endpoint purpose-of-use logging; access reviews (later phases)

## Sessions & transport
- ✅ Session-bound tokens; server-side revocation; expiry columns indexed
- ✅ CORS restricted to configured origins; security headers
  (nosniff, DENY framing, no-referrer, no-store) on every response
- ✅ Secrets only via environment/.env (git-ignored); `.env.example` carries
  placeholders; JWT secret length enforced
- 🟡 TLS enforced at ingress in deployment — local dev is plaintext loopback
- 🟡 Web stores tokens in localStorage (M1 scaffold trade-off — move refresh
  token to httpOnly SameSite cookie in M2/M4; documented in code)

## Data protection
- ✅ No PHI in code, logs or error bodies; correlation ids only
- ✅ Synthetic test data only (tests generate their own clinics)
- ✅ Audit trail append-only (policy + grants + trigger; tested for owner and app role)
- ✅ Parameterized SQL everywhere (SQLAlchemy core/text params)
- 🟡 Encryption at rest: delegated to disk/volume encryption at deployment;
  mobile DB will be SQLCipher-encrypted (M4)
- ⬜ Backups + restore drills (DEPLOYMENT.md drafts procedure; execute in M7)
- ⬜ Secrets manager, key rotation, rate limiting (M7)

## Application hardening
- ✅ Input validation via Pydantic (`extra=forbid`, strict types, slug/code regexes)
- ✅ Generic 500 handler (no stack traces to clients; logged server-side)
- ✅ Ruff lint + typecheck +33 automated tests incl. failure paths; CI gates
- ✅ Dependency licence register + SBOM (LICENSES.md); **no AGPL dependencies**
- ⬜ Dependency vulnerability scanning in CI (pip-audit/npm audit step, M7)
- ⬜ OWASP ASVS 5.0 full pass + independent penetration test (pre-launch gate)

## Clinical safety boundaries (enforced by scope at M1)
- ✅ No clinical decision support, no AI prescribing in this codebase
- ⬜ M2+: prescriptions immutable after sign (versioned amendments), only
  authorized roles sign, no auto dose selection, allergy checks — acceptance
  tests defined in REQUIREMENTS.md

## Incident & operations
- ⬜ Incident response plan, patch management, support escalation (M7–M9)
- ⬜ Lost-device response runbook (M4)
