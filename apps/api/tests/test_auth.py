"""Authentication flows: register, login, refresh rotation, logout, tokens."""
from __future__ import annotations

from tests.conftest import _auth, login, register_org


def test_health(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_register_and_me(client):
    data = register_org(client, slug="reg-clinic")
    assert data["role"] == "owner"
    assert "member.manage" in data["permissions"]
    assert data["organization"]["slug"] == "reg-clinic"
    assert data["tokens"]["access_token"]
    assert data["tokens"]["refresh_token"]

    me = client.get("/api/v1/auth/me", headers=_auth(data))
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["email"] == "owner@reg-clinic.example.com"
    assert body["role"] == "owner"
    assert body["organization"]["slug"] == "reg-clinic"
    # password hash must never appear anywhere in API output
    assert "password" not in me.text and "hash" not in me.text


def test_register_duplicate_email_409(client):
    register_org(client, slug="dup-email-1", email="shared@dup.example.com")
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Other Clinic",
            "organization_slug": "dup-email-2",
            "full_name": "Dr. Two",
            "email": "shared@dup.example.com",
            "password": "Str0ngPassphrase!",
        },
    )
    assert resp.status_code == 409


def test_register_duplicate_slug_409(client):
    register_org(client, slug="dup-slug")
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Copy Clinic",
            "organization_slug": "dup-slug",
            "full_name": "Dr. Copy",
            "email": "copy@dup-slug.example.com",
            "password": "Str0ngPassphrase!",
        },
    )
    assert resp.status_code == 409


def test_register_rejects_weak_password(client):
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Weak Clinic",
            "organization_slug": "weak-clinic",
            "full_name": "Dr. Weak",
            "email": "weak@weak.example.com",
            "password": "short",
        },
    )
    assert resp.status_code == 422


def test_login_success_and_bad_credentials(client):
    register_org(client, slug="login-clinic")

    ok = login(client, email="owner@login-clinic.example.com", password="Str0ngPassphrase!")
    assert ok.status_code == 200
    assert ok.json()["tokens"]["access_token"]

    bad = login(client, email="owner@login-clinic.example.com", password="WrongPassword!99")
    assert bad.status_code == 401
    unknown = login(client, email="nobody@login-clinic.example.com", password="Whatever123!")
    assert unknown.status_code == 401
    # identical bodies: no account enumeration
    assert bad.json() == unknown.json()


def test_login_multiple_orgs_requires_slug(client):
    a = register_org(client, slug="multi-a")
    b = register_org(client, slug="multi-b")
    # owner of A is added as member of B by B's owner
    resp = client.post(
        "/api/v1/members",
        json={
            "email": "owner@multi-a.example.com",
            "full_name": "Dr. Test Owner",
            "password": "Irrelevant123!",
            "role_key": "doctor",
        },
        headers=_auth(b),
    )
    assert resp.status_code == 201, resp.text

    ambiguous = login(client, email="owner@multi-a.example.com", password="Str0ngPassphrase!")
    assert ambiguous.status_code == 409
    assert set(ambiguous.json()["detail"]["organization_slugs"]) == {"multi-a", "multi-b"}

    scoped = login(
        client,
        email="owner@multi-a.example.com",
        password="Str0ngPassphrase!",
        slug="multi-b",
    )
    assert scoped.status_code == 200
    assert scoped.json()["organization"]["slug"] == "multi-b"
    assert scoped.json()["role"] == "doctor"
    del a


def test_refresh_rotation_and_reuse_detection(client):
    data = register_org(client, slug="refresh-clinic")
    refresh1 = data["tokens"]["refresh_token"]

    # rotate
    r1 = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh1})
    assert r1.status_code == 200
    refresh2 = r1.json()["refresh_token"]
    assert refresh2 != refresh1

    # rotated token works
    r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh2})
    assert r2.status_code == 200
    refresh3 = r2.json()["refresh_token"]

    # replaying refresh1 = reuse -> whole family revoked
    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh1})
    assert replay.status_code == 401

    # even the newest token of that family is now dead
    after = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh3})
    assert after.status_code == 401

    # reuse was audited (owner has audit.read).
    # NOTE: reuse-detection revoked this login's whole session family, so the
    # owner's access token is dead too -- re-authenticate (proves that too).
    assert client.get("/api/v1/auth/me", headers=_auth(data)).status_code == 401
    fresh = login(client, email="owner@refresh-clinic.example.com",
                  password="Str0ngPassphrase!")
    assert fresh.status_code == 200
    audit = client.get("/api/v1/audit-events?action=auth.refresh_reuse_detected",
                       headers=_auth(fresh.json()))
    assert audit.status_code == 200
    assert audit.json()["total"] >= 1


def test_logout_revokes_access_token(client):
    data = register_org(client, slug="logout-clinic")
    headers = _auth(data)

    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    out = client.post("/api/v1/auth/logout", headers=headers)
    assert out.status_code == 200

    # access token is tied to the session -> immediately invalid
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    # refresh token of the revoked session is dead too
    ref = client.post("/api/v1/auth/refresh",
                      json={"refresh_token": data["tokens"]["refresh_token"]})
    assert ref.status_code == 401


def test_tampered_and_missing_tokens_rejected(client):
    data = register_org(client, slug="tamper-clinic")
    token = data["tokens"]["access_token"]
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    assert client.get("/api/v1/auth/me",
                      headers={"Authorization": f"Bearer {tampered}"}).status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401
    # a structurally valid JWT signed with the wrong key must fail
    import jwt as pyjwt

    forged = pyjwt.encode(
        {"sub": "x", "sid": "x", "org": "x", "iss": "aditya-bionova-hms",
         "iat": 1, "nbf": 1, "exp": 9_999_999_999, "typ": "access"},
        "wrong-key", algorithm="HS256",
    )
    assert client.get("/api/v1/auth/me",
                      headers={"Authorization": f"Bearer {forged}"}).status_code == 401
