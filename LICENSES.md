# Licences & Software Bill of Materials (SBOM)

Product licence: proprietary — owned by the Aditya BioNova company; this
repository's source is delivered to and controlled by the customer.
Third-party components below remain under their own terms.

**Policy:** permissive licences preferred; **no AGPL components** (see the
Bahmni note). Verify again before each release: `pip-licenses` /
`npm ls --all` (commands at the bottom).

## Python — API runtime (versions as installed 2026-10-12)

| Package | Version | Licence | Commercial use |
|---|---|---|---|
| fastapi | 0.143.0 | MIT | ✅ |
| starlette | 1.7.0 | BSD-3-Clause | ✅ |
| uvicorn | 0.54.0 | BSD-3-Clause | ✅ |
| sqlalchemy | 2.1.4 | MIT | ✅ |
| alembic | 1.20.0 | MIT | ✅ |
| psycopg2-binary | 2.9.13 | **LGPL w/ exceptions** | ✅ dynamically linked, unmodified — compliant; re-evaluate only if statically bundling |
| pyjwt | 2.15.1 | MIT | ✅ |
| pydantic / pydantic-core | 2.14.1 / 2.50.1 | MIT | ✅ |
| pydantic-settings | 2.15.0 | MIT | ✅ |
| email-validator | 2.3.0 | Unlicense (public domain) | ✅ |
| certifi (transitive) | 2026.7.22 | MPL-2.0 | ✅ unmodified; file-level copyleft only |
| typing-extensions | 4.16.0 | PSF-2.0 | ✅ |
| anyio / click / h11 / httpx / httpcore / idna / sniffio | — | MIT / BSD-3 | ✅ |
| Mako / MarkupSafe (alembic) | 1.4.3 / 3.0.4 | MIT / BSD-3 | ✅ |
| uvloop / watchfiles / websockets (uvicorn) | — | MIT / BSD-3 | ✅ |

## Python — development only

| Package | Version | Licence |
|---|---|---|
| pytest | 8.4.2 | MIT |
| ruff | 0.17.0 | MIT |

## Node — web runtime (from package-lock.json)

| Package | Version | Licence |
|---|---|---|
| next | 15.5.27 | MIT |
| react / react-dom | 19.3.0 | MIT |
| styled-jsx / @next/env / nanoid | — | MIT |
| caniuse-lite | 1.0.30001815 | CC-BY-4.0 (data; attribution retained) |
| typescript | 5.9.3 | Apache-2.0 |

## System / infrastructure
- PostgreSQL 16 (database): PostgreSQL Licence ✅
- Docker images (`postgres:16-alpine`, `python:3.12-slim`, `node:22-alpine`): upstream licences ✅

## Competitor-research provenance
No competitor source code, graphics or interface designs were copied.
Competitor study limited to publicly documented features (see
REQUIREMENTS.md and the source PDFs).

## Open-source note re: Bahmni (research finding)
Bahmni is a mixed-licence system (AGPL-3.0 / MPL-2.0 / MIT / LGPL across
components, with an in-progress licence migration — bahmni.org/license).
It is used **only as a workflow benchmark**. Copying any Bahmni/OpenMRS
code into this product is prohibited without a dedicated legal review; AGPL
components must never enter this dependency tree.

## Obligations checklist
- ✅ Keep copyright notices and licence texts of dependencies (auto-collected
  on release with the commands below).
- ✅ Deliver source of *our* product to the customer (this repository).
- 🟡 LGPL psycopg2: keep it dynamically linked (default pip install); do not
  patch it.
- ⬜ Per release: run the collectors, archive `pip-licenses --format=json` and
  `npm ls --all` output into `docs/compliance/`.

```bash
# release-time SBOM commands
.venv/bin/pip install pip-licenses && .venv/bin/pip-licenses --format=markdown
cd apps/web && npm ls --all --json > ../../docs/compliance/npm-sbom.json
```
