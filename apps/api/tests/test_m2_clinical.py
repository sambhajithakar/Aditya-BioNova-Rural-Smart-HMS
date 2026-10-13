"""M2: patients, duplicate detection/merge, appointments, OPD queue.

Covers the M2 acceptance chain plus the tenant-isolation and RBAC
negative tests required by the blueprint (cross-tenant identifiers must
return 404; forged org headers are ignored; support staff must not read
patient data; the queue state machine never moves backwards).
"""
from __future__ import annotations

import itertools
import uuid

from .conftest import _auth, login, register_org

_seq = itertools.count(1)

NOW = "2026-10-13T09:00:00+00:00"
LATER = "2026-10-13T09:30:00+00:00"
LATER2 = "2026-10-13T10:00:00+00:00"


def _patient(name=None, phone=None, **kw):
    n = next(_seq)
    body = {
        "full_name": name or f"Synthetic Patient {n}",
        "phone": phone or f"98{str(n).zfill(8)}",
        "gender": "female",
        "date_of_birth": "1990-05-14",
        "address": "Village Kothrud",
        "blood_group": "O+",
        "allergies": "penicillin",
    }
    body.update(kw)
    return body


# --- patients ---------------------------------------------------------------
def test_register_patient_assigns_tenant_uhid(client, org_a):
    h = _auth(org_a["auth"])
    r = client.post("/api/v1/patients", json=_patient(), headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["uhid"] == "P0000001"
    assert body["organization_id"] == org_a["auth"]["organization"]["id"]
    assert body["allergies"] == "penicillin"

    r2 = client.post(
        "/api/v1/patients", json=_patient(name="Ravi Kumar", phone="9123456780"), headers=h
    )
    assert r2.status_code == 201
    assert r2.json()["uhid"] == "P0000002"


def test_duplicate_detection_blocks_then_allows(client, org_a):
    h = _auth(org_a["auth"])
    first = client.post(
        "/api/v1/patients", json=_patient(name="Suresh Raina", phone="9811122233"), headers=h
    )
    assert first.status_code == 201

    # same phone, different name -> 409 with candidates
    dup = client.post(
        "/api/v1/patients",
        json=_patient(name="Suresh R", phone="+919811122233"),
        headers=h,
    )
    assert dup.status_code == 409
    detail = dup.json()["detail"]
    assert "candidates" in detail
    assert detail["candidates"][0]["matched_on"] == ["phone"]
    assert detail["candidates"][0]["uhid"] == first.json()["uhid"]

    # same name+dob, no phone overlap -> also detected
    dup2 = client.post(
        "/api/v1/patients",
        json=_patient(name="suresh raina", phone=None),
        headers=h,
    )
    assert dup2.status_code == 409
    assert "name+dob" in dup2.json()["detail"]["candidates"][0]["matched_on"]

    # supervised override creates the record and is audited
    forced = client.post(
        "/api/v1/patients",
        json=_patient(name="Suresh Raina", phone="9811122233", allow_duplicates=True),
        headers=h,
    )
    assert forced.status_code == 201
    events = client.get("/api/v1/audit-events", params={"action": "patient.created"}, headers=h)
    assert any(
        e["resource_id"] == forced.json()["id"] and e["metadata"]["allow_duplicates"]
        for e in events.json()["items"]
    )


def test_duplicate_detection_is_tenant_scoped(client, org_a, org_b):
    """The SAME demographics in another tenant must not collide."""
    client.post("/api/v1/patients", json=_patient(), headers=_auth(org_a["auth"]))
    rb = client.post("/api/v1/patients", json=_patient(), headers=_auth(org_b["auth"]))
    assert rb.status_code == 201  # no cross-tenant duplicate warning


def test_search_and_pagination(client, org_a):
    h = _auth(org_a["auth"])
    for i in range(5):
        r = client.post(
            "/api/v1/patients",
            json=_patient(name=f"Search Subject {i}", phone=f"975500000{i}"),
            headers=h,
        )
        assert r.status_code == 201

    page = client.get("/api/v1/patients", params={"q": "search subject", "limit": 2}, headers=h)
    assert page.status_code == 200
    body = page.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2

    by_phone = client.get("/api/v1/patients", params={"q": "9755000003"}, headers=h)
    assert by_phone.json()["total"] == 1
    assert by_phone.json()["items"][0]["full_name"] == "Search Subject 3"


def test_supervised_merge(client, org_a):
    h = _auth(org_a["auth"])
    a = client.post(
        "/api/v1/patients", json=_patient(name="Merge Source", phone="9822233344"), headers=h
    ).json()
    b = client.post(
        "/api/v1/patients",
        json=_patient(name="Merge Target", phone="9822233345", allow_duplicates=True),
        headers=h,
    ).json()

    self_merge = client.post(
        f"/api/v1/patients/{a['id']}/merge", json={"target_patient_id": a["id"]}, headers=h
    )
    assert self_merge.status_code == 409

    ok = client.post(
        f"/api/v1/patients/{a['id']}/merge", json={"target_patient_id": b["id"]}, headers=h
    )
    assert ok.status_code == 200
    assert ok.json()["id"] == b["id"]

    # source is retained (audit trail) but read-only and hidden from search
    src = client.get(f"/api/v1/patients/{a['id']}", headers=h)
    assert src.status_code == 200
    assert src.json()["status"] == "merged"
    assert src.json()["merged_into_id"] == b["id"]
    patch = client.patch(
        f"/api/v1/patients/{a['id']}", json={"address": "x"}, headers=h
    )
    assert patch.status_code == 409
    assert "Search Source"[:6].lower() not in [
        p["full_name"].lower() for p in
        client.get("/api/v1/patients", params={"q": "merge"}, headers=h).json()["items"]
    ]

    # merging again is refused
    again = client.post(
        f"/api/v1/patients/{a['id']}/merge", json={"target_patient_id": b["id"]}, headers=h
    )
    assert again.status_code == 409


# --- appointments -----------------------------------------------------------
def _appt(client, h, patient_id, branch_id, **kw):
    body = {
        "patient_id": patient_id,
        "branch_id": branch_id,
        "starts_at": NOW,
        "ends_at": LATER,
        "reason": "fever",
    }
    body.update(kw)
    return client.post("/api/v1/appointments", json=body, headers=h)


def test_appointment_lifecycle_and_conflicts(client, org_a):
    h = _auth(org_a["auth"])
    p = client.post("/api/v1/patients", json=_patient(), headers=h).json()
    br = org_a["branch"]["id"]

    owner_id = client.get("/api/v1/auth/me", headers=h).json()["user"]["id"]
    a = _appt(client, h, p["id"], br, provider_user_id=owner_id)
    assert a.status_code == 201, a.text

    # provider double-booking in an overlapping window is rejected
    conflict = _appt(
        client, h, p["id"], br, provider_user_id=owner_id,
        starts_at="2026-10-13T09:15:00+00:00", ends_at=LATER2,
    )
    assert conflict.status_code == 409

    # non-overlapping for the same provider is fine
    ok = _appt(
        client, h, p["id"], br, provider_user_id=owner_id,
        starts_at=LATER2, ends_at="2026-10-13T10:30:00+00:00",
    )
    assert ok.status_code == 201

    # reschedule to a free slot works; ends<=starts is rejected
    resched = client.patch(
        f"/api/v1/appointments/{a.json()['id']}",
        json={"starts_at": "2026-10-13T11:00:00+00:00",
              "ends_at": "2026-10-13T11:30:00+00:00"},
        headers=h,
    )
    assert resched.status_code == 200
    bad = client.patch(
        f"/api/v1/appointments/{a.json()['id']}",
        json={"starts_at": LATER2, "ends_at": LATER},
        headers=h,
    )
    assert bad.status_code == 422

    # cancel -> completed appointments are frozen
    cancel = client.patch(
        f"/api/v1/appointments/{ok.json()['id']}", json={"status": "cancelled"}, headers=h
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
    frozen = client.patch(
        f"/api/v1/appointments/{ok.json()['id']}", json={"reason": "x"}, headers=h
    )
    assert frozen.status_code == 409

    # listing with filters
    day = client.get(
        "/api/v1/appointments",
        params={"from_date": "2026-10-13T00:00:00+00:00",
                "to_date": "2026-10-14T00:00:00+00:00"},
        headers=h,
    )
    assert day.status_code == 200
    assert day.json()["total"] >= 2


def test_appointment_rejects_foreign_patient_and_branch(client, org_a, org_b):
    """Cross-tenant references 404 — never a foreign-key leak."""
    h_a, h_b = _auth(org_a["auth"]), _auth(org_b["auth"])
    foreign_patient = client.post("/api/v1/patients", json=_patient(), headers=h_b).json()
    r = _appt(client, h_a, foreign_patient["id"], org_a["branch"]["id"])
    assert r.status_code == 404
    own = client.post("/api/v1/patients", json=_patient(), headers=h_a).json()
    r2 = _appt(client, h_a, own["id"], org_b["branch"]["id"])
    assert r2.status_code == 404


# --- OPD queue --------------------------------------------------------------
def test_queue_full_flow(client, org_a):
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    p1 = client.post("/api/v1/patients", json=_patient(name="Queue One"), headers=h).json()
    p2 = client.post(
        "/api/v1/patients", json=_patient(name="Queue Two", phone="9111122233"), headers=h
    ).json()

    e1 = client.post("/api/v1/opd/queue", json={"patient_id": p1["id"], "branch_id": br}, headers=h)
    assert e1.status_code == 201, e1.text
    e2 = client.post(
        "/api/v1/opd/queue",
        json={"patient_id": p2["id"], "branch_id": br, "priority": "urgent"},
        headers=h,
    )
    assert e2.status_code == 201
    assert e1.json()["token_number"] == 1
    assert e2.json()["token_number"] == 2
    assert e2.json()["status"] == "waiting"

    # a patient cannot hold two active entries on the same day
    dup_join = client.post(
        "/api/v1/opd/queue", json={"patient_id": p1["id"], "branch_id": br}, headers=h
    )
    assert dup_join.status_code == 409

    # state machine: waiting -> called -> in_consultation -> completed
    called = client.post(
        f"/api/v1/opd/queue/{e1.json()['id']}/call", json={}, headers=h
    )
    assert called.status_code == 200
    assert called.json()["status"] == "called"
    assert called.json()["called_at"] is not None

    # jumping a still-waiting patient straight into consultation is refused
    back = client.post(f"/api/v1/opd/queue/{e2.json()['id']}/start", json={}, headers=h)
    assert back.status_code == 409

    start = client.post(
        f"/api/v1/opd/queue/{e1.json()['id']}/start", json={}, headers=h
    )
    assert start.status_code == 200
    done = client.post(f"/api/v1/opd/queue/{e1.json()['id']}/complete", json={}, headers=h)
    assert done.status_code == 200
    assert done.json()["completed_at"] is not None
    # terminal state is frozen
    again = client.post(f"/api/v1/opd/queue/{e1.json()['id']}/call", json={}, headers=h)
    assert again.status_code == 409

    board = client.get("/api/v1/opd/queue", headers=h).json()
    assert board["total"] == 2
    assert [i["token_number"] for i in board["items"]] == [1, 2]


def test_queue_idempotency_key(client, org_a):
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    p = client.post("/api/v1/patients", json=_patient(), headers=h).json()
    headers = {**h, "Idempotency-Key": "desk-retry-0001"}
    payload = {"patient_id": p["id"], "branch_id": br}

    first = client.post("/api/v1/opd/queue", json=payload, headers=headers)
    assert first.status_code == 201
    retry = client.post("/api/v1/opd/queue", json=payload, headers=headers)
    assert retry.status_code == 201
    assert retry.json()["id"] == first.json()["id"]  # replay, not duplicate

    # same key + different payload -> 409
    p2 = client.post("/api/v1/patients", json=_patient(name="Other"), headers=h).json()
    mismatch = client.post(
        "/api/v1/opd/queue", json={"patient_id": p2["id"], "branch_id": br}, headers=headers
    )
    assert mismatch.status_code == 409


# --- tenancy + RBAC negative tests ------------------------------------------
def test_cross_tenant_patient_returns_404(client, org_a, org_b):
    p = client.post("/api/v1/patients", json=_patient(), headers=_auth(org_a["auth"])).json()
    r = client.get(f"/api/v1/patients/{p['id']}", headers=_auth(org_b["auth"]))
    assert r.status_code == 404
    patch = client.patch(
        f"/api/v1/patients/{p['id']}", json={"address": "hacked"}, headers=_auth(org_b["auth"])
    )
    assert patch.status_code == 404


def test_cross_tenant_queue_entry_returns_404(client, org_a, org_b):
    h_a = _auth(org_a["auth"])
    p = client.post("/api/v1/patients", json=_patient(), headers=h_a).json()
    e = client.post(
        "/api/v1/opd/queue", json={"patient_id": p["id"], "branch_id": org_a["branch"]["id"]},
        headers=h_a,
    ).json()
    h_b = _auth(org_b["auth"])
    # org B cannot call/complete org A's queue entry
    r = client.post(f"/api/v1/opd/queue/{e['id']}/call", json={}, headers=h_b)
    assert r.status_code == 404
    board_b = client.get("/api/v1/opd/queue", headers=h_b).json()
    assert board_b["total"] == 0  # RLS hides tenant A's queue entirely


def test_forged_org_header_is_ignored(client, org_a, org_b):
    """X-Org-Id must never redirect tenant context."""
    p = client.post("/api/v1/patients", json=_patient(), headers=_auth(org_a["auth"])).json()
    forged = {
        **_auth(org_b["auth"]),
        "X-Org-Id": org_a["auth"]["organization"]["id"],
    }
    r = client.get(f"/api/v1/patients/{p['id']}", headers=forged)
    assert r.status_code == 404


def test_support_role_has_no_patient_access(client):
    """Support staff must not read identifiable patient data."""
    owner = _auth(
        register_org(client, slug="clinic-support-2", email="own2@clinic-support-2.example.com")
    )
    # create a support member (permission check happens server-side)
    member = client.post(
        "/api/v1/members",
        json={"email": "sup1@clinic-support-2.example.com", "full_name": "Support One",
              "password": "Str0ngPassphrase!", "role_key": "support"},
        headers=owner,
    )
    assert member.status_code == 201, member.text
    sup = login(client, email="sup1@clinic-support-2.example.com",
                password="Str0ngPassphrase!", slug="clinic-support-2")
    assert sup.status_code == 200
    sup_h = _auth(sup.json())

    r = client.get("/api/v1/patients", headers=sup_h)
    assert r.status_code == 403
    r2 = client.post("/api/v1/patients", json=_patient(), headers=sup_h)
    assert r2.status_code == 403
    r3 = client.get("/api/v1/opd/queue", headers=sup_h)
    assert r3.status_code == 403


def test_nurse_and_doctor_permission_matrix(client):
    """Doctor: queue yes / register no. Nurse: vitals-tier read only."""
    owner = _auth(
        register_org(client, slug="clinic-rbac", email="own@clinic-rbac.example.com")
    )
    for role, email in (("doctor", "dr1@clinic-rbac.example.com"),
                        ("nurse", "nurse1@clinic-rbac.example.com")):
        r = client.post(
            "/api/v1/members",
            json={"email": email, "full_name": role.title(),
                  "password": "Str0ngPassphrase!", "role_key": role},
            headers=owner,
        )
        assert r.status_code == 201, r.text

    dr = _auth(login(client, email="dr1@clinic-rbac.example.com",
                password="Str0ngPassphrase!", slug="clinic-rbac").json())
    nurse = _auth(login(client, email="nurse1@clinic-rbac.example.com",
                  password="Str0ngPassphrase!", slug="clinic-rbac").json())

    # doctor cannot register patients but can see the queue and manage it
    assert client.post("/api/v1/patients", json=_patient(), headers=dr).status_code == 403
    assert client.get("/api/v1/patients", headers=dr).status_code == 200
    assert client.get("/api/v1/opd/queue", headers=dr).status_code == 200

    # nurse can read patients/appointments but not manage the queue
    assert client.get("/api/v1/patients", headers=nurse).status_code == 200
    assert client.get("/api/v1/opd/queue", headers=nurse).status_code == 200
    assert client.post(
        "/api/v1/opd/queue", json={"patient_id": str(uuid.uuid4()),
                                   "branch_id": str(uuid.uuid4())}, headers=nurse
    ).status_code == 403


# --- raw RLS proofs ---------------------------------------------------------
def test_raw_rls_hides_patients_without_context(client, org_a, org_b):
    """Direct SQL as hms_app with no GUC context sees zero rows."""
    from app.db import SessionLocal
    from sqlalchemy import text

    client.post("/api/v1/patients", json=_patient(), headers=_auth(org_a["auth"]))

    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT count(*) FROM patients")).scalar()
        assert rows == 0  # RLS: no tenant context -> no rows

        db.execute(text("SELECT set_config('app.current_org_id', :o, true)"),
                   {"o": org_b["auth"]["organization"]["id"]})
        rows_b = db.execute(
            text("SELECT count(*) FROM patients WHERE organization_id = :a"),
            {"a": org_a["auth"]["organization"]["id"]},
        ).scalar()
        assert rows_b == 0  # tenant B sees none of tenant A's patients

        # writes to the wrong tenant are refused by WITH CHECK
        denied = False
        try:
            db.execute(text(
                "INSERT INTO patients (organization_id, uhid, full_name, gender) "
                "VALUES (:o, 'P9999999', 'X', 'male')"
            ), {"o": org_a["auth"]["organization"]["id"]})
        except Exception:
            denied = True
        assert denied
    finally:
        db.close()
