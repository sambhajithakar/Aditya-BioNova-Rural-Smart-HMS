"""M3: consultation -> prescription -> billing -> receipt acceptance chain.

Covers the blueprint's non-negotiable clinical rules (§R3.5): only
authorized practitioners sign; no silent post-sign changes (DB-trigger
enforced, proven with raw SQL); allergy check before prescribing; no
free-text medication entry. Plus financial integrity: server-computed
totals, append-only ledger, idempotent payments, refund bounds, and
cross-tenant isolation for every new table.
"""
from __future__ import annotations

import itertools
import uuid

import psycopg2
from tests.conftest import APP_PASSWORD, PG_HOST, PG_PORT, _auth

_seq = itertools.count(1)

NOW = "2026-10-13T11:00:00+00:00"


def _app_conn():
    """Non-owner connection. NOT autocommit: the RLS GUCs are transaction-
    local, and trigger errors need SAVEPOINT recovery inside one txn."""
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, user="hms_app", password=APP_PASSWORD,
        dbname="hms_test",
    )


def _set_org(cur, org_id):
    cur.execute("SELECT set_config('app.current_org_id', %s, true)",
                (str(org_id),))


def _expect_blocked(cur, sql, params, needles):
    """Run a statement that a DB guard must reject, then recover the txn."""
    if isinstance(needles, str):
        needles = (needles,)
    cur.execute("SAVEPOINT sp_guard")
    try:
        cur.execute(sql, params)
    except psycopg2.Error as exc:
        cur.execute("ROLLBACK TO SAVEPOINT sp_guard")
        msg = str(exc).lower()
        assert any(n in msg for n in needles), msg
        return
    cur.execute("ROLLBACK TO SAVEPOINT sp_guard")
    raise AssertionError(f"expected DB guard to block: {sql}")


def _patient(client, h, *, allergies="", name=None):
    n = next(_seq)
    body = {
        "full_name": name or f"M3 Patient {n}",
        "phone": f"97{str(n).zfill(8)}",
        "gender": "male",
        "date_of_birth": "1985-02-10",
        "address": "Village M3",
        "blood_group": "B+",
        "allergies": allergies,
        "allow_duplicates": True,
    }
    r = client.post("/api/v1/patients", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _drug_id(client, h, name="Paracetamol"):
    r = client.get(f"/api/v1/prescriptions/drugs?q={name}", headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert rows, f"drug {name} not in catalog"
    return rows[0]["id"]


def _start_encounter(client, h, patient, branch, **kw):
    body = {"patient_id": patient["id"], "branch_id": branch["id"],
            "chief_complaint": "Fever 3 days"}
    body.update(kw)
    r = client.post("/api/v1/encounters", json=body, headers=h)
    return r


# --- encounters -------------------------------------------------------------
def test_encounter_flow_vitals_diagnosis_complete(client, org_a):
    h = _auth(org_a["auth"])
    p = _patient(client, h)

    r = _start_encounter(client, h, p, org_a["branch"])
    assert r.status_code == 201, r.text
    enc = r.json()
    assert enc["status"] == "open"
    assert enc["provider_user_id"] is not None

    # one open encounter per patient — second attempt 409
    again = _start_encounter(client, h, p, org_a["branch"])
    assert again.status_code == 409

    # vitals with out-of-range values rejected by schema
    bad = client.post(f"/api/v1/encounters/{enc['id']}/vitals",
                      json={"spo2": 120}, headers=h)
    assert bad.status_code == 422
    bad2 = client.post(f"/api/v1/encounters/{enc['id']}/vitals",
                       json={"bp_systolic": 80, "bp_diastolic": 120}, headers=h)
    assert bad2.status_code == 422  # systolic must exceed diastolic

    ok = client.post(
        f"/api/v1/encounters/{enc['id']}/vitals",
        json={"bp_systolic": 128, "bp_diastolic": 84, "pulse": 76,
              "temperature_c": 38.6, "spo2": 97, "weight_kg": 62.5},
        headers=h,
    )
    assert ok.status_code == 201, ok.text

    dx = client.post(
        f"/api/v1/encounters/{enc['id']}/diagnoses",
        json={"code": "A09", "label": "Fever, unspecified", "kind": "primary"},
        headers=h,
    )
    assert dx.status_code == 201

    # detail aggregates vitals + diagnoses
    detail = client.get(f"/api/v1/encounters/{enc['id']}", headers=h)
    assert detail.status_code == 200
    d = detail.json()
    assert len(d["vitals"]) == 1 and d["vitals"][0]["spo2"] == 97
    assert len(d["diagnoses"]) == 1 and d["diagnoses"][0]["code"] == "A09"

    done = client.post(f"/api/v1/encounters/{enc['id']}/complete", headers=h)
    assert done.status_code == 200
    assert done.json()["status"] == "completed"
    assert done.json()["ended_at"] is not None

    # completed encounter is frozen — edits rejected by DB trigger
    frozen = client.patch(f"/api/v1/encounters/{enc['id']}",
                          json={"assessment": "sneaky edit"}, headers=h)
    assert frozen.status_code == 409
    # vitals after completion also rejected
    after = client.post(f"/api/v1/encounters/{enc['id']}/vitals",
                        json={"pulse": 70}, headers=h)
    assert after.status_code == 409


def test_encounter_cross_tenant_isolation(client, org_a, org_b):
    ha, hb = _auth(org_a["auth"]), _auth(org_b["auth"])
    p = _patient(client, ha)
    enc = _start_encounter(client, ha, p, org_a["branch"]).json()

    # B cannot read, edit or complete A's encounter
    assert client.get(f"/api/v1/encounters/{enc['id']}", headers=hb).status_code == 404
    assert client.patch(f"/api/v1/encounters/{enc['id']}",
                        json={"history": "x"}, headers=hb).status_code == 404
    assert client.post(f"/api/v1/encounters/{enc['id']}/complete",
                       headers=hb).status_code == 404
    # B's list never contains A's encounter
    rows = client.get("/api/v1/encounters", headers=hb).json()
    assert enc["id"] not in {i["id"] for i in rows["items"]}


def test_rls_no_context_returns_no_rows(client, org_a):
    """Raw proof: without app.current_org_id, M3 tenant tables are empty."""
    p = _patient(client, _auth(org_a["auth"]))
    enc = _start_encounter(client, _auth(org_a["auth"]), p, org_a["branch"]).json()
    conn = _app_conn()
    try:
        cur = conn.cursor()
        for table in ("encounters", "encounter_vitals", "prescriptions",
                      "invoices", "payments", "ledger_entries"):
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0] == 0, f"{table} leaked without org context"
        # and with the WRONG org context, A's encounter is invisible
        _set_org(cur, uuid.uuid4())
        cur.execute("SELECT count(*) FROM encounters WHERE id = %s", (enc["id"],))
        assert cur.fetchone()[0] == 0
        conn.rollback()
    finally:
        conn.close()


# --- prescriptions ----------------------------------------------------------
def _draft(client, h, patient, drugs, **kw):
    body = {
        "patient_id": patient["id"],
        "diagnosis_text": "Acute viral fever",
        "advice": "Plenty of fluids",
        "follow_up": "Review after 3 days",
        "items": [
            {"drug_id": d, "dose": "500 mg", "route": "oral",
             "frequency": "TDS", "duration_days": 5, "quantity": "15 tablets"}
            for d in drugs
        ],
    }
    body.update(kw)
    r = client.post("/api/v1/prescriptions", json=body, headers=h)
    return r


def test_sign_requires_practitioner_permission(client, org_a):
    """Receptionist cannot draft or sign; only prescription.sign holders."""
    owner_headers = _auth(org_a["auth"])
    client.post("/api/v1/members",
                json={"email": "recep-m3@clinic-a.example.com",
                      "full_name": "Recep M3", "password": "Str0ngPassphrase!",
                      "role_key": "receptionist"},
                headers=owner_headers)
    from tests.conftest import login
    r = login(client, email="recep-m3@clinic-a.example.com",
              password="Str0ngPassphrase!", slug="clinic-a")
    rh = _auth(r.json())

    p = _patient(client, owner_headers)
    drug = _drug_id(client, owner_headers)
    denied = _draft(client, rh, p, [drug])
    assert denied.status_code == 403
    assert "prescription.sign" in denied.json()["detail"]


def test_allergy_check_blocks_sign_until_acknowledged(client, org_a):
    h = _auth(org_a["auth"])
    # patient allergic to penicillin; drug catalog maps amoxicillin -> penicillin
    p = _patient(client, h, allergies="penicillin, sulfa")
    amox = _drug_id(client, h, "Amoxicillin")
    para = _drug_id(client, h, "Paracetamol")

    # pure paracetamol Rx: no warnings, signs cleanly
    safe = _draft(client, h, p, [para]).json()
    warn = client.post(f"/api/v1/prescriptions/{safe['id']}/warnings", headers=h)
    assert warn.status_code == 200
    assert warn.json()["warnings"] == []
    signed = client.post(f"/api/v1/prescriptions/{safe['id']}/sign",
                         json={"acknowledge_allergies": False}, headers=h)
    assert signed.status_code == 200
    assert signed.json()["status"] == "signed"

    # amoxicillin for a penicillin-allergic patient: blocking warning
    risky = _draft(client, h, p, [amox]).json()
    warn2 = client.post(f"/api/v1/prescriptions/{risky['id']}/warnings", headers=h)
    w = warn2.json()
    assert w["blocking"] is True
    assert w["warnings"][0]["kind"] == "allergy"

    blocked = client.post(f"/api/v1/prescriptions/{risky['id']}/sign",
                          json={"acknowledge_allergies": False}, headers=h)
    assert blocked.status_code == 409
    assert "allergy" in str(blocked.json()["detail"]).lower()

    # explicit acknowledgement signs it (audited as acknowledged)
    acked = client.post(f"/api/v1/prescriptions/{risky['id']}/sign",
                        json={"acknowledge_allergies": True}, headers=h)
    assert acked.status_code == 200
    body = acked.json()
    assert body["status"] == "signed"
    assert body["allergy_acknowledged"] is True


def test_duplicate_drug_warning_is_advisory(client, org_a):
    h = _auth(org_a["auth"])
    p = _patient(client, h)
    para = _drug_id(client, h, "Paracetamol")
    rx = _draft(client, h, p, [para], items=[
        {"drug_id": para, "dose": "500 mg", "route": "oral",
         "frequency": "TDS", "duration_days": 5},
        {"drug_id": para, "dose": "650 mg", "route": "oral",
         "frequency": "QDS", "duration_days": 3},
    ]).json()
    w = client.post(f"/api/v1/prescriptions/{rx['id']}/warnings", headers=h).json()
    kinds = [x["kind"] for x in w["warnings"]]
    assert "duplicate_drug" in kinds
    assert w["blocking"] is False  # duplicates warn, allergies block
    # still signable
    signed = client.post(f"/api/v1/prescriptions/{rx['id']}/sign",
                         json={"acknowledge_allergies": False}, headers=h)
    assert signed.status_code == 200


def test_unknown_drug_rejected(client, org_a):
    """Validated data: items must reference the drug catalog (no free text)."""
    h = _auth(org_a["auth"])
    p = _patient(client, h)
    r = _draft(client, h, p, [str(uuid.uuid4())])
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["message"] == "Unknown or inactive drug(s)"


def test_signed_prescription_immutable_raw_sql(client, org_a):
    """DB trigger: signed Rx content cannot change; DELETE blocked;
    only signed -> superseded is legal (amendment path)."""
    h = _auth(org_a["auth"])
    p = _patient(client, h)
    para = _drug_id(client, h, "Paracetamol")
    rx = _draft(client, h, p, [para]).json()
    signed = client.post(f"/api/v1/prescriptions/{rx['id']}/sign",
                         json={"acknowledge_allergies": False}, headers=h)
    assert signed.status_code == 200

    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_org(cur, org_a["auth"]["organization"]["id"])
        # content UPDATE blocked by the trigger
        _expect_blocked(
            cur, "UPDATE prescriptions SET advice = 'tampered' WHERE id = %s",
            (rx["id"],), "immutable")
        # item UPDATE blocked
        _expect_blocked(
            cur, "UPDATE prescription_items SET dose = '1 g' "
                 "WHERE prescription_id = %s",
            (rx["id"],), "immutable")
        # DELETE blocked
        _expect_blocked(cur, "DELETE FROM prescriptions WHERE id = %s",
                        (rx["id"],), "immutable")
        # no rows actually changed
        cur.execute("SELECT advice FROM prescriptions WHERE id = %s", (rx["id"],))
        assert cur.fetchone()[0] != "tampered"
        conn.rollback()
    finally:
        conn.close()


def test_amendment_creates_new_version_and_supersedes(client, org_a):
    h = _auth(org_a["auth"])
    p = _patient(client, h)
    para = _drug_id(client, h, "Paracetamol")
    ibu = _drug_id(client, h, "Ibuprofen")
    rx = _draft(client, h, p, [para]).json()
    signed = client.post(f"/api/v1/prescriptions/{rx['id']}/sign",
                         json={"acknowledge_allergies": False}, headers=h)
    assert signed.status_code == 200

    amended = client.post(
        f"/api/v1/prescriptions/{rx['id']}/amend",
        json={"reason": "Patient still febrile on day 3",
              "items": [{"drug_id": ibu, "dose": "400 mg", "route": "oral",
                         "frequency": "TDS", "duration_days": 3}],
              "acknowledge_allergies": False},
        headers=h,
    )
    assert amended.status_code == 201, amended.text
    new_rx = amended.json()
    assert new_rx["version"] == 2
    assert new_rx["supersedes_id"] == rx["id"]
    assert new_rx["status"] == "signed"
    assert new_rx["amendment_reason"] == "Patient still febrile on day 3"

    # old row is superseded, both versions retained (nothing deleted)
    old = client.get(f"/api/v1/prescriptions/{rx['id']}", headers=h).json()
    assert old["status"] == "superseded"
    assert old["items"][0]["drug_id"] == para  # original content untouched

    # amending a superseded row is refused (amend the latest)
    again = client.post(
        f"/api/v1/prescriptions/{rx['id']}/amend",
        json={"reason": "nope", "items": [
            {"drug_id": para, "dose": "500", "route": "oral",
             "frequency": "BD", "duration_days": 2}]},
        headers=h,
    )
    assert again.status_code == 409

    # audit recorded both events
    events = client.get("/api/v1/audit-events?limit=100", headers=h).json()
    actions = [i["action"] for i in events["items"]]
    assert "prescription.signed" in actions
    assert "prescription.amended" in actions


def test_draft_must_have_items(client, org_a):
    h = _auth(org_a["auth"])
    p = _patient(client, h)
    r = client.post("/api/v1/prescriptions",
                    json={"patient_id": p["id"], "items": []}, headers=h)
    assert r.status_code == 422


def test_signing_twice_rejected(client, org_a):
    h = _auth(org_a["auth"])
    p = _patient(client, h)
    para = _drug_id(client, h, "Paracetamol")
    rx = _draft(client, h, p, [para]).json()
    first = client.post(f"/api/v1/prescriptions/{rx['id']}/sign",
                        json={"acknowledge_allergies": False}, headers=h)
    assert first.status_code == 200
    second = client.post(f"/api/v1/prescriptions/{rx['id']}/sign",
                         json={"acknowledge_allergies": False}, headers=h)
    assert second.status_code == 409


# --- billing ----------------------------------------------------------------
def _invoice(client, h, patient, branch, *, discount=0, items=None, encounter=None):
    body = {
        "patient_id": patient["id"],
        "branch_id": branch["id"],
        "discount": discount,
        "items": items or [
            {"item_type": "consultation", "description": "OPD consultation",
             "quantity": 1, "unit_price": 200, "tax_rate": 0},
            {"item_type": "pharmacy", "description": "Paracetamol x15",
             "quantity": 15, "unit_price": 4.5, "tax_rate": 12},
        ],
    }
    if encounter:
        body["encounter_id"] = encounter
    return client.post("/api/v1/billing/invoices", json=body, headers=h)


def test_invoice_totals_computed_server_side(client, org_a):
    h = _auth(org_a["auth"])
    p = _patient(client, h)
    r = _invoice(client, h, p, org_a["branch"], discount=50)
    assert r.status_code == 201, r.text
    inv = r.json()
    # subtotal = 200 + 67.50 = 267.50; tax = 8.10 (12% of 67.50)
    assert float(inv["subtotal"]) == 267.50
    assert float(inv["tax_total"]) == 8.10
    assert float(inv["total"]) == 267.50 - 50 + 8.10
    assert inv["status"] == "draft"
    assert inv["invoice_number"] == "INV-000001"
    assert len(inv["items"]) == 2

    # discount > subtotal rejected
    bad = _invoice(client, h, p, org_a["branch"], discount=99999)
    assert bad.status_code == 422


def test_invoice_issue_freezes_financials(client, org_a):
    h = _auth(org_a["auth"])
    p = _patient(client, h)
    inv = _invoice(client, h, p, org_a["branch"]).json()

    issued = client.post(f"/api/v1/billing/invoices/{inv['id']}/issue", headers=h)
    assert issued.status_code == 200
    assert issued.json()["status"] == "issued"
    assert issued.json()["issued_at"] is not None

    # issue twice -> 409
    assert client.post(f"/api/v1/billing/invoices/{inv['id']}/issue",
                       headers=h).status_code == 409

    # raw DB proof: financial fields frozen + items immutable after issue
    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_org(cur, org_a["auth"]["organization"]["id"])
        _expect_blocked(cur, "UPDATE invoices SET total = 1 WHERE id = %s",
                        (inv["id"],), "frozen")
        _expect_blocked(
            cur, "UPDATE invoice_items SET unit_price = 0 WHERE invoice_id = %s",
            (inv["id"],), "immutable")
        conn.rollback()
    finally:
        conn.close()


def test_payment_idempotency_replay_and_conflict(client, org_a):
    h = _auth(org_a["auth"])
    p = _patient(client, h)
    inv = _invoice(client, h, p, org_a["branch"]).json()
    assert client.post(f"/api/v1/billing/invoices/{inv['id']}/issue",
                       headers=h).status_code == 200

    key = "pay-key-m3-001"
    payload = {"amount": 100, "method": "cash"}
    r1 = client.post(f"/api/v1/billing/invoices/{inv['id']}/payments",
                     json=payload, headers={**h, "Idempotency-Key": key})
    assert r1.status_code == 201, r1.text
    pay1 = r1.json()
    assert pay1["status"] == "succeeded"  # manual gateway settles immediately

    # exact retry: same key + same payload -> replay, no second payment
    r2 = client.post(f"/api/v1/billing/invoices/{inv['id']}/payments",
                     json=payload, headers={**h, "Idempotency-Key": key})
    assert r2.status_code == 201
    assert r2.json()["id"] == pay1["id"]

    # same key + DIFFERENT payload -> 409
    r3 = client.post(f"/api/v1/billing/invoices/{inv['id']}/payments",
                     json={"amount": 50, "method": "cash"},
                     headers={**h, "Idempotency-Key": key})
    assert r3.status_code == 409

    # ledger has exactly ONE credit for this payment despite the retry
    ledger = client.get(f"/api/v1/billing/invoices/{inv['id']}/ledger",
                        headers=h).json()
    credits = [e for e in ledger if e["entry_type"] == "payment"]
    assert len(credits) == 1
    # and invoice amount_paid counted once
    detail = client.get(f"/api/v1/billing/invoices/{inv['id']}", headers=h).json()
    assert float(detail["amount_paid"]) == 100


def test_payment_cannot_exceed_balance(client, org_a):
    h = _auth(org_a["auth"])
    p = _patient(client, h)
    inv = _invoice(client, h, p, org_a["branch"]).json()
    client.post(f"/api/v1/billing/invoices/{inv['id']}/issue", headers=h)

    over = client.post(f"/api/v1/billing/invoices/{inv['id']}/payments",
                       json={"amount": 99999, "method": "cash"}, headers=h)
    assert over.status_code == 422

    # paying a draft invoice is refused
    draft = _invoice(client, h, p, org_a["branch"]).json()
    early = client.post(f"/api/v1/billing/invoices/{draft['id']}/payments",
                        json={"amount": 10, "method": "cash"}, headers=h)
    assert early.status_code == 409


def test_sandbox_gateway_pending_then_confirm(client, org_a):
    h = _auth(org_a["auth"])
    p = _patient(client, h)
    inv = _invoice(client, h, p, org_a["branch"]).json()
    client.post(f"/api/v1/billing/invoices/{inv['id']}/issue", headers=h)

    full = float(inv["total"])  # 275.60 — settle in one go
    r = client.post(f"/api/v1/billing/invoices/{inv['id']}/payments",
                    json={"amount": full, "method": "upi", "gateway": "sandbox"},
                    headers=h)
    assert r.status_code == 201, r.text
    pay = r.json()
    assert pay["status"] == "pending"
    assert pay["gateway_ref"].startswith("sbox_")

    # no ledger credit while pending; balance unchanged
    ledger = client.get(f"/api/v1/billing/invoices/{inv['id']}/ledger",
                        headers=h).json()
    assert [e for e in ledger if e["entry_type"] == "payment"] == []

    confirmed = client.post(f"/api/v1/billing/payments/{pay['id']}/confirm",
                            headers=h)
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "succeeded"

    # double confirm -> 409 (state machine, and unique ledger index)
    assert client.post(f"/api/v1/billing/payments/{pay['id']}/confirm",
                       headers=h).status_code == 409

    detail = client.get(f"/api/v1/billing/invoices/{inv['id']}", headers=h).json()
    assert float(detail["amount_paid"]) == 275.60
    # fully settled -> invoice flips to paid
    assert detail["status"] == "paid"


def test_sandbox_gateway_failure_leaves_balance(client, org_a):
    h = _auth(org_a["auth"])
    p = _patient(client, h)
    inv = _invoice(client, h, p, org_a["branch"]).json()
    client.post(f"/api/v1/billing/invoices/{inv['id']}/issue", headers=h)

    r = client.post(f"/api/v1/billing/invoices/{inv['id']}/payments",
                    json={"amount": 50, "method": "upi", "gateway": "sandbox"},
                    headers=h)
    pay = r.json()
    failed = client.post(f"/api/v1/billing/payments/{pay['id']}/fail", headers=h)
    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert failed.json()["failure_reason"]

    detail = client.get(f"/api/v1/billing/invoices/{inv['id']}", headers=h).json()
    assert float(detail["amount_paid"]) == 0
    assert detail["status"] == "issued"


def test_refund_bounds_and_ledger_debit(client, org_a):
    h = _auth(org_a["auth"])
    p = _patient(client, h)
    # total 500 (fits the 300 payment used below)
    inv = _invoice(client, h, p, org_a["branch"], items=[
        {"item_type": "consultation", "description": "Day care procedure",
         "quantity": 1, "unit_price": 500, "tax_rate": 0},
    ]).json()
    client.post(f"/api/v1/billing/invoices/{inv['id']}/issue", headers=h)
    pay = client.post(f"/api/v1/billing/invoices/{inv['id']}/payments",
                      json={"amount": 300, "method": "upi"}, headers=h).json()
    assert pay["status"] == "succeeded"

    # over-refund rejected
    over = client.post(f"/api/v1/billing/payments/{pay['id']}/refund",
                       json={"amount": 500, "reason": "too much"}, headers=h)
    assert over.status_code == 422

    # partial refund
    r1 = client.post(f"/api/v1/billing/payments/{pay['id']}/refund",
                     json={"amount": 100, "reason": "Service not delivered"},
                     headers=h)
    assert r1.status_code == 201, r1.text
    part = client.get(f"/api/v1/billing/invoices/{inv['id']}", headers=h).json()
    assert float(part["amount_paid"]) == 200

    # remaining refund + one cent over -> rejected, then exact -> fully refunded
    assert client.post(f"/api/v1/billing/payments/{pay['id']}/refund",
                       json={"amount": 100.01, "reason": "x"},
                       headers=h).status_code == 422
    r2 = client.post(f"/api/v1/billing/payments/{pay['id']}/refund",
                     json={"amount": 200, "reason": "Full reversal"}, headers=h)
    assert r2.status_code == 201
    pay_after = client.get(f"/api/v1/billing/invoices/{inv['id']}",
                           headers=h).json()["payments"][0]
    assert pay_after["status"] == "refunded"
    # a third refund is now impossible
    assert client.post(f"/api/v1/billing/payments/{pay['id']}/refund",
                       json={"amount": 1, "reason": "again"},
                       headers=h).status_code in (409, 422)

    ledger = client.get(f"/api/v1/billing/invoices/{inv['id']}/ledger",
                        headers=h).json()
    types = [e["entry_type"] for e in ledger]
    assert types.count("refund") == 2
    # balance back to full: charge(275.60) - paid(300)... receipt must reconcile
    receipt = client.get(f"/api/v1/billing/invoices/{inv['id']}/receipt",
                         headers=h)
    assert receipt.status_code == 200
    rec = receipt.json()
    assert rec["patient_uhid"] == p["uhid"]
    assert rec["org_name"]


def test_ledger_append_only_raw_sql(client, org_a):
    h = _auth(org_a["auth"])
    p = _patient(client, h)
    inv = _invoice(client, h, p, org_a["branch"]).json()
    client.post(f"/api/v1/billing/invoices/{inv['id']}/issue", headers=h)

    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_org(cur, org_a["auth"]["organization"]["id"])
        cur.execute("SELECT id FROM ledger_entries WHERE invoice_id = %s",
                    (inv["id"],))
        row = cur.fetchone()
        assert row, "charge entry missing after issue"
        # either the grant REVOKE or the append-only trigger may fire first;
        # both prove the row cannot be mutated
        _expect_blocked(cur, "UPDATE ledger_entries SET amount = 0.01 WHERE id = %s",
                        (row[0],), ("append-only", "permission denied"))
        _expect_blocked(cur, "DELETE FROM ledger_entries WHERE id = %s",
                        (row[0],), ("append-only", "permission denied"))
        conn.rollback()
    finally:
        conn.close()


def test_ledger_balance_equals_invoice_position(client, org_a):
    """Double-entry invariant: charges - credits == total - amount_paid."""
    h = _auth(org_a["auth"])
    p = _patient(client, h)
    inv = _invoice(client, h, p, org_a["branch"], discount=25).json()
    client.post(f"/api/v1/billing/invoices/{inv['id']}/issue", headers=h)
    pay = client.post(f"/api/v1/billing/invoices/{inv['id']}/payments",
                      json={"amount": 150, "method": "card"}, headers=h).json()
    client.post(f"/api/v1/billing/payments/{pay['id']}/refund",
                json={"amount": 50, "reason": "Overcharge corrected"}, headers=h)

    receipt = client.get(f"/api/v1/billing/invoices/{inv['id']}/receipt",
                         headers=h).json()
    debits = sum(float(e["amount"]) for e in receipt["ledger"]
                 if e["direction"] == "debit")
    credits = sum(float(e["amount"]) for e in receipt["ledger"]
                  if e["direction"] == "credit")
    assert round(debits - credits, 2) == round(receipt["balance_due"], 2)
    assert round(receipt["balance_due"], 2) == round(
        float(receipt["invoice"]["total"]) - float(receipt["invoice"]["amount_paid"]),
        2,
    )
    assert float(receipt["invoice"]["amount_paid"]) == 100


def test_void_requires_no_settled_payments(client, org_a):
    h = _auth(org_a["auth"])
    p = _patient(client, h)
    inv = _invoice(client, h, p, org_a["branch"]).json()
    client.post(f"/api/v1/billing/invoices/{inv['id']}/issue", headers=h)

    # paid invoice cannot be voided
    pay_r = client.post(f"/api/v1/billing/invoices/{inv['id']}/payments",
                        json={"amount": 100, "method": "cash"}, headers=h)
    assert pay_r.status_code == 201, pay_r.text
    assert client.post(f"/api/v1/billing/invoices/{inv['id']}/void",
                       headers=h).status_code == 409

    # unpaid issued invoice voids with a reversing adjustment
    inv2 = _invoice(client, h, p, org_a["branch"]).json()
    client.post(f"/api/v1/billing/invoices/{inv2['id']}/issue", headers=h)
    voided = client.post(f"/api/v1/billing/invoices/{inv2['id']}/void", headers=h)
    assert voided.status_code == 200
    assert voided.json()["status"] == "void"
    ledger = client.get(f"/api/v1/billing/invoices/{inv2['id']}/ledger",
                        headers=h).json()
    assert [e["entry_type"] for e in ledger] == ["charge", "adjustment"]
    receipt = client.get(f"/api/v1/billing/invoices/{inv2['id']}/receipt",
                         headers=h)
    assert receipt.status_code == 200
    assert receipt.json()["balance_due"] == 0


def test_billing_cross_tenant_isolation(client, org_a, org_b):
    ha, hb = _auth(org_a["auth"]), _auth(org_b["auth"])
    pa = _patient(client, ha)
    pb = _patient(client, hb)
    inv_a = _invoice(client, ha, pa, org_a["branch"]).json()
    client.post(f"/api/v1/billing/invoices/{inv_a['id']}/issue", headers=ha)
    pay_a = client.post(f"/api/v1/billing/invoices/{inv_a['id']}/payments",
                        json={"amount": 100, "method": "cash"}, headers=ha).json()

    # B cannot see A's invoice/payment/receipt/ledger
    assert client.get(f"/api/v1/billing/invoices/{inv_a['id']}",
                      headers=hb).status_code == 404
    assert client.get(f"/api/v1/billing/invoices/{inv_a['id']}/receipt",
                      headers=hb).status_code == 404
    assert client.get(f"/api/v1/billing/invoices/{inv_a['id']}/ledger",
                      headers=hb).status_code == 404
    assert client.post(f"/api/v1/billing/payments/{pay_a['id']}/refund",
                       json={"amount": 1, "reason": "cross tenant"},
                       headers=hb).status_code == 404
    assert client.post(f"/api/v1/billing/invoices/{inv_a['id']}/issue",
                       headers=hb).status_code == 404

    # B's own invoice numbers start at 1 (per-tenant numbering)
    inv_b = _invoice(client, hb, pb, org_b["branch"]).json()
    assert inv_b["invoice_number"] == "INV-000001"


def test_billing_rbac_matrix(client, org_a):
    """Receptionist invoices but cannot refund; support/auditor see nothing."""
    oh = _auth(org_a["auth"])
    for email, role in (("recv-m3@clinic-a.example.com", "receptionist"),
                        ("bill-m3@clinic-a.example.com", "billing"),
                        ("supp-m3@clinic-a.example.com", "support"),
                        ("aud-m3@clinic-a.example.com", "auditor"),
                        ("doct-m3@clinic-a.example.com", "doctor")):
        client.post("/api/v1/members",
                    json={"email": email, "full_name": f"{role} m3",
                          "password": "Str0ngPassphrase!", "role_key": role},
                    headers=oh)
    from tests.conftest import login

    def h_for(email):
        r = login(client, email=email, password="Str0ngPassphrase!",
                  slug="clinic-a")
        assert r.status_code == 200, r.text
        return _auth(r.json())

    p = _patient(client, oh)
    recv = h_for("recv-m3@clinic-a.example.com")
    bill = h_for("bill-m3@clinic-a.example.com")
    supp = h_for("supp-m3@clinic-a.example.com")
    aud = h_for("aud-m3@clinic-a.example.com")
    doct = h_for("doct-m3@clinic-a.example.com")

    inv = _invoice(client, recv, p, org_a["branch"])
    assert inv.status_code == 201  # receptionist may invoice
    inv_id = inv.json()["id"]
    client.post(f"/api/v1/billing/invoices/{inv_id}/issue", headers=recv)

    # receptionist: payment yes, refund NO
    pay = client.post(f"/api/v1/billing/invoices/{inv_id}/payments",
                      json={"amount": 50, "method": "cash"}, headers=recv)
    assert pay.status_code == 201
    assert client.post(
        f"/api/v1/billing/payments/{pay.json()['id']}/refund",
        json={"amount": 10, "reason": "nope"}, headers=recv).status_code == 403

    # billing role: may refund; may NOT touch encounters
    assert client.post(
        f"/api/v1/billing/payments/{pay.json()['id']}/refund",
        json={"amount": 10, "reason": "goodwill"}, headers=bill).status_code == 201
    assert client.get("/api/v1/encounters", headers=bill).status_code == 403

    # support & auditor: zero financial/clinical reads
    for h in (supp, aud):
        assert client.get("/api/v1/billing/invoices", headers=h).status_code == 403
        assert client.get("/api/v1/encounters", headers=h).status_code == 403
        assert client.get("/api/v1/prescriptions", headers=h).status_code == 403

    # doctor: clinical yes, invoicing NO
    assert client.get("/api/v1/encounters", headers=doct).status_code == 200
    assert _invoice(client, doct, p, org_a["branch"]).status_code == 403
    # doctor CAN sign (checked in dedicated test via owner)


# --- full acceptance chain --------------------------------------------------
def test_e2e_registration_to_receipt(client, org_a):
    """Blueprint acceptance chain (§M3): register -> queue -> consultation
    -> vitals -> diagnosis -> signed Rx -> invoice -> payment -> receipt."""
    h = _auth(org_a["auth"])
    p = _patient(client, h, allergies="sulfa")

    # 1. front desk issues a token
    q = client.post("/api/v1/opd/queue",
                    json={"patient_id": p["id"], "branch_id": org_a["branch"]["id"]},
                    headers=h)
    assert q.status_code == 201, q.text
    entry = q.json()
    assert entry["token_number"] >= 1

    # 2. doctor calls the patient, starts consultation
    assert client.post(f"/api/v1/opd/queue/{entry['id']}/call",
                       json={"doctor_user_id": None}, headers=h).status_code == 200

    # 3. open encounter from the queue entry
    enc_r = _start_encounter(client, h, p, org_a["branch"],
                             queue_entry_id=entry["id"])
    assert enc_r.status_code == 201, enc_r.text
    enc = enc_r.json()

    # 4. record vitals + structured diagnosis
    assert client.post(f"/api/v1/encounters/{enc['id']}/vitals",
                       json={"bp_systolic": 138, "bp_diastolic": 90, "pulse": 96,
                             "temperature_c": 39.1, "spo2": 96},
                       headers=h).status_code == 201
    assert client.post(f"/api/v1/encounters/{enc['id']}/diagnoses",
                       json={"code": "J06.9", "label": "Acute URI",
                             "kind": "primary"},
                       headers=h).status_code == 201

    # 5. draft Rx -> safety warnings (sulfa patient + Septran blocks)
    para = _drug_id(client, h, "Paracetamol")
    septran = _drug_id(client, h, "Sulfamethoxazole")
    draft = _draft(client, h, p, [para, septran], encounter_id=enc["id"])
    assert draft.status_code == 201, draft.text
    rx = draft.json()
    warn = client.post(f"/api/v1/prescriptions/{rx['id']}/warnings", headers=h).json()
    assert warn["blocking"] is True
    assert client.post(f"/api/v1/prescriptions/{rx['id']}/sign",
                       json={"acknowledge_allergies": False},
                       headers=h).status_code == 409
    assert client.post(f"/api/v1/prescriptions/{rx['id']}/sign",
                       json={"acknowledge_allergies": True},
                       headers=h).status_code == 200

    # 6. close the consultation (queue advances too)
    done = client.post(f"/api/v1/encounters/{enc['id']}/complete", headers=h)
    assert done.status_code == 200
    board = client.get("/api/v1/opd/queue", headers=h).json()
    row = next(i for i in board["items"] if i["id"] == entry["id"])
    assert row["status"] == "completed"

    # 7. billing: invoice -> issue -> pay -> receipt
    inv = _invoice(client, h, p, org_a["branch"], encounter=enc["id"]).json()
    client.post(f"/api/v1/billing/invoices/{inv['id']}/issue", headers=h)
    pay = client.post(f"/api/v1/billing/invoices/{inv['id']}/payments",
                      json={"amount": float(inv["total"]), "method": "upi",
                            "gateway": "sandbox"},
                      headers=h).json()
    client.post(f"/api/v1/billing/payments/{pay['id']}/confirm", headers=h)

    receipt = client.get(f"/api/v1/billing/invoices/{inv['id']}/receipt", headers=h)
    assert receipt.status_code == 200
    rec = receipt.json()
    assert rec["invoice"]["status"] == "paid"
    assert rec["balance_due"] == 0
    assert rec["patient_uhid"] == p["uhid"]
    assert rec["patient_name"] == p["full_name"]
    types = [e["entry_type"] for e in rec["ledger"]]
    assert types == ["charge", "payment"]

    # 8. audit trail recorded the whole journey
    events = client.get("/api/v1/audit-events?limit=200", headers=h).json()
    actions = {i["action"] for i in events["items"]}
    assert {"encounter.started", "encounter.vitals_recorded",
            "encounter.diagnosis_recorded", "encounter.completed",
            "prescription.signed", "invoice.created", "invoice.issued",
            "payment.created", "payment.succeeded"} <= actions

    # 9. encounter detail shows the linked prescription
    detail = client.get(f"/api/v1/encounters/{enc['id']}", headers=h).json()
    assert rx["id"] in detail["prescription_ids"]
    assert detail["prescription_ids"]  # linked
