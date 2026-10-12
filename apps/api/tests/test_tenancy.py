"""Cross-tenant isolation: API-level (IDOR) and raw PostgreSQL RLS proofs.

The raw tests connect as the non-owner `hms_app` role — the same role the
API uses — and prove that even a compromised application query cannot read
or write another tenant's rows without the correct transaction-local GUCs.
"""
from __future__ import annotations

import uuid

import psycopg2
import pytest
from tests.conftest import APP_PASSWORD, PG_HOST, PG_PORT, _auth


def _app_conn():
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, user="hms_app", password=APP_PASSWORD, dbname="hms_test"
    )


# ------------------------------- API level ---------------------------------

def test_branch_list_is_tenant_scoped(client, auth_headers_a, org_a, org_b):
    resp = client.get("/api/v1/branches", headers=auth_headers_a)
    assert resp.status_code == 200
    ids = {b["id"] for b in resp.json()}
    assert ids == {org_a["branch"]["id"]}
    assert org_b["branch"]["id"] not in ids


def test_cross_tenant_identifier_manipulation_404(client, auth_headers_a, org_b):
    """Tenant A asking for tenant B's branch id must get 404, not 200."""
    resp = client.get(f"/api/v1/branches/{org_b['branch']['id']}", headers=auth_headers_a)
    assert resp.status_code == 404


def test_forged_org_header_is_ignored(client, auth_headers_a, org_a, org_b):
    """Org identity comes from the token, never from a client header."""
    headers = dict(auth_headers_a)
    headers["X-Org-Id"] = org_b["auth"]["organization"]["id"]
    resp = client.get("/api/v1/branches", headers=headers)
    assert resp.status_code == 200
    assert {b["id"] for b in resp.json()} == {org_a["branch"]["id"]}


def test_members_list_is_tenant_scoped(client, auth_headers_a, org_b):
    resp = client.get("/api/v1/members", headers=auth_headers_a)
    assert resp.status_code == 200
    emails = {m["email"] for m in resp.json()}
    assert "owner@clinic-b.example.com" not in emails
    assert "owner@clinic-a.example.com" in emails


def test_audit_events_are_tenant_scoped(client, auth_headers_a, org_b):
    resp = client.get("/api/v1/audit-events?limit=200", headers=auth_headers_a)
    assert resp.status_code == 200
    b_branch_id = org_b["branch"]["id"]
    for item in resp.json()["items"]:
        assert item.get("resource_id") != b_branch_id
        assert item["organization_id"] != org_b["auth"]["organization"]["id"]


def test_member_create_is_scoped_to_token_org(client, auth_headers_a, auth_headers_b,
                                               org_a, org_b):
    """A member created by tenant A lands in tenant A — never in tenant B —
    because organization_id comes from the verified token, not the request."""
    resp = client.post(
        "/api/v1/members",
        json={"email": "newstaff@clinic-a.example.com", "full_name": "New Staff",
              "password": "Str0ngPassphrase!", "role_key": "receptionist"},
        headers=auth_headers_a,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["membership_id"]

    in_a = {m["email"] for m in client.get("/api/v1/members", headers=auth_headers_a).json()}
    in_b = {m["email"] for m in client.get("/api/v1/members", headers=auth_headers_b).json()}
    assert "newstaff@clinic-a.example.com" in in_a
    assert "newstaff@clinic-a.example.com" not in in_b
    del org_a, org_b


# ------------------------------- raw RLS proofs -----------------------------

def _set_ctx(cur, org_id=None, user_id=None, bootstrap=False):
    cur.execute("SELECT set_config('app.current_org_id', %s, true)",
                (str(org_id) if org_id else "",))
    cur.execute("SELECT set_config('app.current_user_id', %s, true)",
                (str(user_id) if user_id else "",))
    cur.execute("SELECT set_config('app.is_bootstrap', %s, true)",
                ("true" if bootstrap else "false",))


def test_rls_no_context_returns_nothing(org_a, org_b):
    conn = _app_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM branches")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM organizations")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM memberships")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM audit_events")
        assert cur.fetchone()[0] == 0
        conn.rollback()
    finally:
        conn.close()


def test_rls_org_context_isolates_rows(org_a, org_b):
    conn = _app_conn()
    try:
        cur = conn.cursor()
        org_a_id = org_a["auth"]["organization"]["id"]
        org_b_id = org_b["auth"]["organization"]["id"]

        _set_ctx(cur, org_id=org_a_id)
        cur.execute("SELECT id, organization_id FROM branches")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert str(rows[0][1]) == org_a_id

        cur.execute("SELECT id FROM organizations")
        assert [str(r[0]) for r in cur.fetchall()] == [org_a_id]

        conn.commit()

        _set_ctx(cur, org_id=org_b_id)
        cur.execute("SELECT organization_id FROM branches")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert str(rows[0][0]) == org_b_id
        conn.commit()
    finally:
        conn.close()


def test_rls_insert_with_wrong_org_denied(org_a, org_b):
    conn = _app_conn()
    try:
        cur = conn.cursor()
        org_a_id = org_a["auth"]["organization"]["id"]
        org_b_id = org_b["auth"]["organization"]["id"]

        _set_ctx(cur, org_id=org_a_id)
        with pytest.raises(psycopg2.errors.InsufficientPrivilege) as exc:
            cur.execute(
                "INSERT INTO branches (organization_id, name, code) "
                "VALUES (%s, 'Evil Branch', 'EVIL')",
                (org_b_id,),
            )
        assert "row-level security" in str(exc.value)
        conn.rollback()

        # writing into OWN tenant is fine
        _set_ctx(cur, org_id=org_a_id)
        cur.execute(
            "INSERT INTO branches (organization_id, name, code) "
            "VALUES (%s, 'Legit Branch', 'LEGIT')",
            (org_a_id,),
        )
        assert cur.rowcount == 1
        conn.rollback()
    finally:
        conn.close()


def test_rls_org_insert_requires_bootstrap_flag():
    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_ctx(cur, bootstrap=False)
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute(
                "INSERT INTO organizations (name, slug) VALUES ('Rogue', 'rogue-clinic')"
            )
        conn.rollback()

        _set_ctx(cur, bootstrap=True)
        cur.execute(
            "INSERT INTO organizations (name, slug) VALUES ('Bootstrapped', 'boot-clinic')"
        )
        assert cur.rowcount == 1
        conn.rollback()
    finally:
        conn.close()


def test_audit_events_append_only(org_a):
    """UPDATE/DELETE are blocked for everyone: the append-only trigger fires
    even for the table owner, and the app role lacks the grants entirely."""
    from tests.conftest import PG_SUPERUSER

    # --- table owner: trigger raises ---
    admin = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, user=PG_SUPERUSER, dbname="hms_test"
    )
    admin.autocommit = True
    acur = admin.cursor()
    acur.execute(
        "INSERT INTO audit_events (action, organization_id) "
        "VALUES ('test.event', NULL) RETURNING id"
    )
    event_id = acur.fetchone()[0]

    with pytest.raises(psycopg2.DatabaseError) as upd:
        acur.execute("UPDATE audit_events SET action='tampered' WHERE id=%s", (event_id,))
    assert "append-only" in str(upd.value)

    with pytest.raises(psycopg2.DatabaseError) as dele:
        acur.execute("DELETE FROM audit_events WHERE id=%s", (event_id,))
    assert "append-only" in str(dele.value)
    admin.close()

    # --- application role: grants/RLS deny writes it does own + reads NULL-org ---
    conn = _app_conn()
    try:
        cur = conn.cursor()
        # row inserted by another tenant/system is not even readable
        cur.execute("SELECT count(*) FROM audit_events")
        assert cur.fetchone()[0] == 0
        # app role cannot UPDATE or DELETE at all (revoked grants)
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("UPDATE audit_events SET action='x' WHERE id=%s", (event_id,))
        conn.rollback()
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("DELETE FROM audit_events WHERE id=%s", (event_id,))
        conn.rollback()
    finally:
        conn.close()


def test_audit_insert_cross_tenant_denied(org_a, org_b):
    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_ctx(cur, org_id=org_a["auth"]["organization"]["id"])
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute(
                "INSERT INTO audit_events (action, organization_id) VALUES "
                "('forged.event', %s)",
                (org_b["auth"]["organization"]["id"],),
            )
        conn.rollback()
    finally:
        conn.close()


def test_membership_visibility_requires_user_context(client, org_a, org_b):
    me = client.get("/api/v1/auth/me", headers=_auth(org_a["auth"])).json()
    user_a = me["user"]["id"]

    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_ctx(cur, user_id=user_a)
        cur.execute("SELECT organization_id FROM memberships")
        orgs = {str(r[0]) for r in cur.fetchall()}
        assert orgs == {org_a["auth"]["organization"]["id"]}
        assert org_b["auth"]["organization"]["id"] not in orgs

        # without user context: invisible
        _set_ctx(cur)
        cur.execute("SELECT count(*) FROM memberships")
        assert cur.fetchone()[0] == 0
        conn.commit()
    finally:
        conn.close()


def test_users_table_never_leaks_hashes_via_api(client, auth_headers_a):
    """Even though `users` has no RLS, no endpoint may expose password hashes."""
    resp = client.get("/api/v1/members", headers=auth_headers_a)
    assert resp.status_code == 200
    body = resp.text
    assert "password_hash" not in body
    assert "scrypt$" not in body
    assert uuid.UUID(me_id(client, auth_headers_a))  # sanity: ids are uuids


def me_id(client, headers) -> str:
    return client.get("/api/v1/auth/me", headers=headers).json()["user"]["id"]
