"""Test bootstrap: environment, fresh database, shared fixtures.

IMPORTANT: environment variables are set BEFORE any `app.*` import so that
settings/engine pick up the TEST database. The test suite runs all API
requests as the non-owner `hms_app` role, so row-level security is active
exactly as in production.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]  # .../apps/api

PG_HOST = os.environ.get("HMS_TEST_HOST", "127.0.0.1")
PG_PORT = os.environ.get("HMS_TEST_PORT", "5433")
PG_SUPERUSER = os.environ.get("HMS_TEST_SUPERUSER", "hms")
APP_PASSWORD = os.environ.get("HMS_APP_PASSWORD", "change_me")
TEST_DB = "hms_test"

os.environ["DATABASE_URL"] = (
    f"postgresql+psycopg2://hms_app:{APP_PASSWORD}@{PG_HOST}:{PG_PORT}/{TEST_DB}"
)
os.environ["MIGRATION_DATABASE_URL"] = (
    f"postgresql+psycopg2://{PG_SUPERUSER}@{PG_HOST}:{PG_PORT}/{TEST_DB}"
)
os.environ["JWT_SECRET"] = "test-only-secret-not-used-in-production-0123456789"
os.environ["ENVIRONMENT"] = "test"
os.environ["CORS_ORIGINS"] = '["http://testserver"]'

import psycopg2  # noqa: E402


def _reset_database() -> None:
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, user=PG_SUPERUSER, dbname="postgres"
    )
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (TEST_DB,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}"')
        cur.execute(f'CREATE DATABASE "{TEST_DB}" OWNER "{PG_SUPERUSER}"')
    conn.close()

    env = dict(os.environ)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}")


_reset_database()

# ---- shared fixtures -------------------------------------------------------

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def register_org(client, *, slug: str, clinic_name: str | None = None,
                 email: str | None = None, password: str = "Str0ngPassphrase!"):
    payload = {
        "organization_name": clinic_name or slug.replace("-", " ").title(),
        "organization_slug": slug,
        "full_name": "Dr. Test Owner",
        "email": email or f"owner@{slug}.example.com",
        "password": password,
    }
    resp = client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def login(client, *, email: str, password: str, slug: str | None = None):
    payload = {"email": email, "password": password}
    if slug:
        payload["organization_slug"] = slug
    resp = client.post("/api/v1/auth/login", json=payload)
    return resp


@pytest.fixture(scope="session")
def org_a(client):
    """Tenant A: owner account + one branch."""
    data = register_org(client, slug="clinic-a")
    branch = client.post(
        "/api/v1/branches",
        json={"name": "Main Clinic", "code": "MAIN"},
        headers=_auth(data),
    )
    assert branch.status_code == 201, branch.text
    return {"auth": data, "branch": branch.json()}


@pytest.fixture(scope="session")
def org_b(client):
    """Tenant B: owner account + one branch."""
    data = register_org(client, slug="clinic-b")
    branch = client.post(
        "/api/v1/branches",
        json={"name": "City Branch", "code": "CITY"},
        headers=_auth(data),
    )
    assert branch.status_code == 201, branch.text
    return {"auth": data, "branch": branch.json()}


def _auth(data) -> dict:
    return {"Authorization": f"Bearer {data['tokens']['access_token']}"}


@pytest.fixture(scope="session")
def auth_headers_a(org_a):
    return _auth(org_a["auth"])


@pytest.fixture(scope="session")
def auth_headers_b(org_b):
    return _auth(org_b["auth"])
