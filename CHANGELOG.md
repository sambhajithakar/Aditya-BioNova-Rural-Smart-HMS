# Changelog

All notable changes to Aditya BioNova Rural Smart HMS.
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
