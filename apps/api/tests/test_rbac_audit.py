"""Role-based access control, member management and audit trail contents."""
from __future__ import annotations

from tests.conftest import _auth, login, register_org


def _create_member(client, headers, *, email, role_key, password="Str0ngPassphrase!"):
    return client.post(
        "/api/v1/members",
        json={"email": email, "full_name": f"User {email}", "password": password,
              "role_key": role_key},
        headers=headers,
    )


def _login_and_headers(client, email, password="Str0ngPassphrase!"):
    resp = login(client, email=email, password=password)
    assert resp.status_code == 200, resp.text
    return _auth(resp.json())


def test_receptionist_permissions(client):
    owner = register_org(client, slug="rbac-reception")
    headers = _auth(owner)

    created = _create_member(client, headers, email="recep@rbac-reception.example.com",
                             role_key="receptionist")
    assert created.status_code == 201, created.text

    rh = _login_and_headers(client, "recep@rbac-reception.example.com")

    # allowed: read branches and members
    assert client.get("/api/v1/branches", headers=rh).status_code == 200
    assert client.get("/api/v1/members", headers=rh).status_code == 200

    # denied: manage branches, manage members, read audit
    denied = client.post("/api/v1/branches",
                         json={"name": "Sneaky", "code": "SNK"}, headers=rh)
    assert denied.status_code == 403
    assert "branch.manage" in denied.json()["detail"]

    denied_members = client.post(
        "/api/v1/members",
        json={"email": "x@y.example.com", "full_name": "X", "password": "Str0ngPassphrase!",
              "role_key": "admin"},
        headers=rh,
    )
    assert denied_members.status_code == 403

    assert client.get("/api/v1/audit-events", headers=rh).status_code == 403


def test_auditor_can_read_audit_only(client):
    owner = register_org(client, slug="rbac-auditor")
    headers = _auth(owner)
    assert _create_member(client, headers, email="audit@rbac-auditor.example.com",
                          role_key="auditor").status_code == 201
    ah = _login_and_headers(client, "audit@rbac-auditor.example.com")

    assert client.get("/api/v1/audit-events", headers=ah).status_code == 200
    # auditor must not manage members or branches
    assert client.post(
        "/api/v1/members",
        json={"email": "z@z.example.com", "full_name": "Zed Zee",
              "password": "Str0ngPassphrase!", "role_key": "doctor"},
        headers=ah,
    ).status_code == 403
    assert client.post("/api/v1/branches", json={"name": "A", "code": "AA"},
                       headers=ah).status_code == 403


def test_support_role_cannot_see_branches(client):
    owner = register_org(client, slug="rbac-support")
    headers = _auth(owner)
    assert _create_member(client, headers, email="support@rbac-support.example.com",
                          role_key="support").status_code == 201
    sh = _login_and_headers(client, "support@rbac-support.example.com")
    assert client.get("/api/v1/branches", headers=sh).status_code == 403
    assert client.get("/api/v1/auth/me", headers=sh).json()["role"] == "support"


def test_owner_role_cannot_be_granted(client):
    owner = register_org(client, slug="rbac-owner-grant")
    resp = _create_member(client, _auth(owner), email="mini@rbac-owner-grant.example.com",
                          role_key="owner")
    assert resp.status_code == 403
    assert "owner" in resp.json()["detail"].lower()


def test_unknown_role_rejected(client):
    owner = register_org(client, slug="rbac-badrole")
    resp = _create_member(client, _auth(owner), email="bad@rbac-badrole.example.com",
                          role_key="wizard")
    assert resp.status_code == 422


def test_duplicate_membership_rejected(client):
    owner = register_org(client, slug="rbac-dup")
    headers = _auth(owner)
    assert _create_member(client, headers, email="dup@rbac-dup.example.com",
                          role_key="nurse").status_code == 201
    again = _create_member(client, headers, email="dup@rbac-dup.example.com",
                           role_key="doctor")
    assert again.status_code == 409


def test_branch_code_unique_per_tenant_only(client, auth_headers_a):
    # same code in another tenant must succeed
    register_org(client, slug="rbac-branchcode")
    token = _login_token(client, "owner@rbac-branchcode.example.com")
    b = client.post(
        "/api/v1/branches",
        json={"name": "Other Main", "code": "MAIN"},
        headers=_auth({"tokens": {"access_token": token}}),
    )
    assert b.status_code == 201

    # duplicate within tenant A is rejected
    dup = client.post("/api/v1/branches",
                      json={"name": "Dup Main", "code": "MAIN"},
                      headers=auth_headers_a)
    assert dup.status_code == 409


def _login_token(client, email: str) -> str:
    resp = login(client, email=email, password="Str0ngPassphrase!")
    assert resp.status_code == 200, resp.text
    return resp.json()["tokens"]["access_token"]


def test_audit_trail_records_sensitive_actions(client):
    owner = register_org(client, slug="rbac-audit-trail")
    headers = _auth(owner)
    _create_member(client, headers, email="nurse@rbac-audit-trail.example.com", role_key="nurse")
    client.post("/api/v1/branches",
                json={"name": "Annex", "code": "ANX"}, headers=headers)
    login(client, email="nurse@rbac-audit-trail.example.com",
          password="Str0ngPassphrase!")

    actions = set()
    offset = 0
    while True:
        page = client.get(f"/api/v1/audit-events?limit=100&offset={offset}",
                          headers=headers).json()
        actions.update(i["action"] for i in page["items"])
        offset += 100
        if offset >= page["total"]:
            break

    assert {"auth.register", "auth.login", "branch.created", "member.created"} <= actions


def test_failed_logins_are_audited(client):
    """Failed logins are recorded (org-unattributed: an unknown attacker email
    belongs to no tenant, so only operators/DBAs can review these)."""
    import psycopg2
    from tests.conftest import PG_HOST, PG_PORT, PG_SUPERUSER

    conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, user=PG_SUPERUSER, dbname="hms_test")
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM audit_events WHERE action='auth.login_failed'")
        before = cur.fetchone()[0]

        register_org(client, slug="rbac-failaudit")
        login(client, email="owner@rbac-failaudit.example.com", password="WrongPassword!99")
        login(client, email="ghost@rbac-failaudit.example.com", password="WrongPassword!99")

        cur.execute("SELECT count(*) FROM audit_events WHERE action='auth.login_failed'")
        assert cur.fetchone()[0] == before + 2
        cur.execute(
            "SELECT metadata->>'reason' FROM audit_events "
            "WHERE action='auth.login_failed' ORDER BY id DESC LIMIT 2"
        )
        reasons = [r[0] for r in cur.fetchall()]
        assert reasons == ["unknown_email", "bad_password"]
    finally:
        conn.close()
