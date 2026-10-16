"""M5: pharmacy (validated stock, FEFO dispensing) and laboratory workflow.

Covers the blueprint's M5 non-negotiables (§R4): stock lives in batches with
explicit expiry (no free-text stock), FEFO allocation, expired/recalled stock
frozen by DB trigger, append-only movement ledger with derived on-hand,
dispensing only against signed prescriptions, lab results following
imported -> verified -> released with four-eyes verification and
column-level freezing, supersession-only amendments, plus the M5 RBAC
matrix and cross-tenant isolation for every new table.
"""
from __future__ import annotations

import itertools
import uuid
from datetime import date, timedelta

from tests.conftest import _auth, login, register_org
from tests.test_m3_clinical_billing import (
    _app_conn,
    _expect_blocked,
    _patient,
    _set_org,
)

_seq = itertools.count(1)


# --- helpers ----------------------------------------------------------------
def _expiry(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _drug_id_exact(client, h, generic_name: str) -> str:
    r = client.get("/api/v1/prescriptions/drugs", params={"q": generic_name},
                   headers=h)
    assert r.status_code == 200, r.text
    for row in r.json():
        if row["generic_name"].lower().startswith(generic_name.lower()):
            return row["id"]
    raise AssertionError(f"drug {generic_name!r} not in catalog")


def _receive(client, h, branch_id, drug_id, batch_number, quantity,
             *, days=60, cost=4.5):
    r = client.post("/api/v1/pharmacy/stock/batches", json={
        "branch_id": branch_id, "drug_id": drug_id,
        "batch_number": batch_number, "expiry_date": _expiry(days),
        "quantity": quantity, "unit_cost": cost, "supplier": "MediCorp",
    }, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _batch(client, h, drug_id, batch_number):
    r = client.get("/api/v1/pharmacy/stock/batches",
                   params={"drug_id": drug_id, "limit": 100}, headers=h)
    assert r.status_code == 200, r.text
    return next(b for b in r.json()["items"] if b["batch_number"] == batch_number)


def _dispense(client, h, items, **kw):
    body = {"items": [{"drug_id": d, "quantity": q} for d, q in items]}
    body.update(kw)
    return client.post("/api/v1/pharmacy/dispenses", json=body, headers=h)


def _member(client, owner_h, email, role, slug="clinic-a"):
    r = client.post("/api/v1/members", json={
        "email": email, "full_name": role.replace("_", " ").title(),
        "password": "Str0ngPassphrase!", "role_key": role,
    }, headers=owner_h)
    assert r.status_code == 201, r.text
    resp = login(client, email=email, password="Str0ngPassphrase!", slug=slug)
    assert resp.status_code == 200, resp.text
    return _auth(resp.json())


def _lab_test_id(client, h, code):
    r = client.get("/api/v1/lab/tests", params={"q": code}, headers=h)
    assert r.status_code == 200, r.text
    return next(t["id"] for t in r.json() if t["code"] == code)


def _lab_order(client, h, patient_id, branch_id, test_ids, **kw):
    body = {"patient_id": patient_id, "branch_id": branch_id,
            "test_ids": test_ids}
    body.update(kw)
    return client.post("/api/v1/lab/orders", json=body, headers=h)


# --- pharmacy: receiving ----------------------------------------------------
def test_receive_stock_batch_ledger_and_idempotency(client, org_a):
    """Receiving creates a batch AND its opening ledger row (one movement);
    duplicate batch numbers are refused; retries with a key replay."""
    h = _auth(org_a["auth"])
    para = _drug_id_exact(client, h, "Paracetamol")
    body = {"branch_id": org_a["branch"]["id"], "drug_id": para,
            "batch_number": "M5-RCV-001", "expiry_date": _expiry(60),
            "quantity": 10, "unit_cost": 4.5, "supplier": "MediCorp"}

    r1 = client.post("/api/v1/pharmacy/stock/batches", json=body,
                     headers={**h, "Idempotency-Key": "m5-recv-1"})
    assert r1.status_code == 201, r1.text
    batch = r1.json()
    assert batch["quantity_on_hand"] == 10
    assert batch["quantity_received"] == 10
    assert batch["status"] == "active"
    assert batch["generic_name"].lower().startswith("paracetamol")

    # exact retry replays (no second batch, no second movement)
    r2 = client.post("/api/v1/pharmacy/stock/batches", json=body,
                     headers={**h, "Idempotency-Key": "m5-recv-1"})
    assert r2.status_code == 201 and r2.json()["id"] == batch["id"]

    # same key, different payload -> 409
    mismatch = {**body, "quantity": 99}
    r3 = client.post("/api/v1/pharmacy/stock/batches", json=mismatch,
                     headers={**h, "Idempotency-Key": "m5-recv-1"})
    assert r3.status_code == 409

    # same batch number again (new key) -> 409 unique per drug/branch
    dup = client.post("/api/v1/pharmacy/stock/batches", json=body, headers=h)
    assert dup.status_code == 409 and "already exists" in dup.json()["detail"]

    # expiry must be in the future; unknown drug -> 404 (validated catalog)
    past = {**body, "batch_number": "M5-RCV-PAST", "expiry_date": _expiry(-1)}
    assert client.post("/api/v1/pharmacy/stock/batches", json=past,
                       headers=h).status_code == 422
    ghost = {**body, "batch_number": "M5-RCV-GHOST",
             "drug_id": str(uuid.uuid4())}
    missing = client.post("/api/v1/pharmacy/stock/batches", json=ghost,
                          headers=h)
    assert missing.status_code == 404

    # exactly ONE 'receive' movement opened the ledger for this batch
    moves = client.get("/api/v1/pharmacy/stock/movements",
                       params={"batch_id": batch["id"]}, headers=h).json()
    assert moves["total"] == 1
    m = moves["items"][0]
    assert (m["movement_type"], m["direction"], m["quantity"]) == ("receive", "in", 10)
    assert "M5-RCV-001" in m["reason"]

    # and it is audited
    events = client.get("/api/v1/audit-events", params={"limit": 200},
                        headers=h).json()
    assert "pharmacy.stock_received" in {e["action"] for e in events["items"]}


# --- pharmacy: FEFO ---------------------------------------------------------
def test_fefo_dispense_consumes_earliest_expiry_first(client, org_a):
    """Two batches of the same drug: dispensing must drain the EARLIEST
    expiry first and split across batches when needed."""
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    # Augmentin is untouched by the other tests -> allocation is deterministic
    aug = _drug_id_exact(client, h, "Amoxicillin-Clavulanate")
    p = _patient(client, h, name="FEFO Patient")

    early = _receive(client, h, br, aug, "M5-FEFO-EARLY", 5, days=30)
    late = _receive(client, h, br, aug, "M5-FEFO-LATE", 5, days=120)

    r = _dispense(client, h, [(aug, 7)], branch_id=br, patient_id=p["id"])
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["branch_id"] == br
    assert len(out["items"]) == 2  # split across the two batches
    first, second = out["items"]
    assert first["batch_id"] == early["id"] and first["quantity"] == 5
    assert second["batch_id"] == late["id"] and second["quantity"] == 2

    # on-hand follows the ledger: early batch depleted, late partially used
    b_early = _batch(client, h, aug, "M5-FEFO-EARLY")
    b_late = _batch(client, h, aug, "M5-FEFO-LATE")
    assert (b_early["quantity_on_hand"], b_early["status"]) == (0, "depleted")
    assert (b_late["quantity_on_hand"], b_late["status"]) == (3, "active")

    # movements recorded the split
    for batch_id, qty in ((early["id"], 5), (late["id"], 2)):
        moves = client.get("/api/v1/pharmacy/stock/movements",
                           params={"batch_id": batch_id}, headers=h).json()
        outs = [m for m in moves["items"] if m["direction"] == "out"]
        assert len(outs) == 1 and outs[0]["quantity"] == qty
        assert outs[0]["movement_type"] == "dispense"


def test_otc_dispense_infers_branch_and_needs_a_subject(client, org_a):
    """OTC dispensing against a registered patient; branch defaults to where
    the stock lives; a dispense without any subject is refused."""
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    cet = _drug_id_exact(client, h, "Cetirizine")
    p = _patient(client, h, name="OTC Patient")
    _receive(client, h, br, cet, "M5-OTC-001", 20, days=90)

    r = _dispense(client, h, [(cet, 2)], patient_id=p["id"])  # no branch_id
    assert r.status_code == 201, r.text
    assert r.json()["branch_id"] == br  # inferred from the stock location
    assert r.json()["prescription_id"] is None

    neither = _dispense(client, h, [(cet, 1)])
    assert neither.status_code == 422


def test_dispense_requires_signed_rx_and_stays_within_it(client, org_a, org_b):
    """Against a prescription: it must be SIGNED, and only drugs written on
    it may leave the shelf; foreign prescriptions are invisible."""
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    para = _drug_id_exact(client, h, "Paracetamol")
    ibu = _drug_id_exact(client, h, "Ibuprofen")
    _receive(client, h, br, para, "M5-RX-001", 30, days=60)
    _receive(client, h, br, ibu, "M5-RX-002", 30, days=60)
    p = _patient(client, h, name="Rx Patient")

    draft = client.post("/api/v1/prescriptions", json={
        "patient_id": p["id"], "diagnosis_text": "Fever",
        "items": [{"drug_id": para, "dose": "500 mg", "route": "oral",
                   "frequency": "TDS", "duration_days": 5}],
    }, headers=h)
    assert draft.status_code == 201, draft.text
    rx = draft.json()

    # unsigned: the pharmacist cannot dispense it yet
    early = _dispense(client, h, [(para, 5)], prescription_id=rx["id"])
    assert early.status_code == 409 and "signed" in early.json()["detail"]

    assert client.post(f"/api/v1/prescriptions/{rx['id']}/sign",
                       json={"acknowledge_allergies": False},
                       headers=h).status_code == 200

    # a drug NOT on the prescription is refused (no free substitution)
    off_rx = _dispense(client, h, [(ibu, 5)], prescription_id=rx["id"])
    assert off_rx.status_code == 422 and "not on prescription" in off_rx.json()["detail"]

    # wrong patient id on the same prescription -> 422
    other = _patient(client, h, name="Not The Rx Patient")
    mismatch = _dispense(client, h, [(para, 1)], prescription_id=rx["id"],
                         patient_id=other["id"])
    assert mismatch.status_code == 422

    # the correct dispense works and is linked to the prescription
    ok = _dispense(client, h, [(para, 5)], prescription_id=rx["id"])
    assert ok.status_code == 201, ok.text
    assert ok.json()["prescription_id"] == rx["id"]
    assert ok.json()["patient_id"] == p["id"]

    # cross-tenant: org B cannot dispense against A's prescription
    hb = _auth(org_b["auth"])
    cross = _dispense(client, hb, [(para, 1)], prescription_id=rx["id"])
    assert cross.status_code == 404


def test_dispense_insufficient_stock_no_partial_write(client, org_a):
    """Unexpired, un-recalled stock only: shortage is a clean 409 and leaves
    no dispense document, no movement and unchanged on-hand."""
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    azi = _drug_id_exact(client, h, "Azithromycin")
    p = _patient(client, h, name="Short Patient")
    batch = _receive(client, h, br, azi, "M5-SHORT-001", 5, days=45)

    before = client.get("/api/v1/pharmacy/dispenses", headers=h).json()["total"]
    short = _dispense(client, h, [(azi, 6)], branch_id=br, patient_id=p["id"])
    assert short.status_code == 409
    assert "insufficient" in short.json()["detail"].lower()

    assert client.get("/api/v1/pharmacy/dispenses",
                      headers=h).json()["total"] == before
    moves = client.get("/api/v1/pharmacy/stock/movements",
                       params={"batch_id": batch["id"]}, headers=h).json()
    assert [m["movement_type"] for m in moves["items"]] == ["receive"]
    assert _batch(client, h, azi, "M5-SHORT-001")["quantity_on_hand"] == 5

    # with the Idempotency-Key, a successful dispense replays instead of
    # double-decrementing the stock
    headers = {**h, "Idempotency-Key": "m5-dispense-1"}
    body_items = [(azi, 3)]
    first = _dispense(client, headers, body_items, branch_id=br, patient_id=p["id"])
    assert first.status_code == 201, first.text
    retry = _dispense(client, headers, body_items, branch_id=br, patient_id=p["id"])
    assert retry.status_code == 201 and retry.json()["id"] == first.json()["id"]
    assert _batch(client, h, azi, "M5-SHORT-001")["quantity_on_hand"] == 2


# --- pharmacy: DB backstops -------------------------------------------------
def test_expired_and_recalled_stock_frozen_by_triggers(client, org_a):
    """Raw-SQL proofs: expired stock cannot be dispensed (only written off),
    recalled batches are frozen and admit no movements at all."""
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    org_id = org_a["auth"]["organization"]["id"]
    amox = _drug_id_exact(client, h, "Amoxicillin")
    p = _patient(client, h, name="Expiry Patient")

    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_org(cur, org_id)
        # an EXPIRING-OUT batch: created expired (as a physical count correction)
        cur.execute(
            "INSERT INTO stock_batches (organization_id, branch_id, drug_id,"
            " batch_number, expiry_date, quantity_received, unit_cost, supplier)"
            " VALUES (%s, %s, %s, %s, CURRENT_DATE - 3, 10, 4.5, 'MediCorp')"
            " RETURNING id",
            (org_id, br, amox, "M5-EXP-RAW"))
        expired_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO stock_movements (organization_id, batch_id,"
            " movement_type, direction, quantity, reason)"
            " VALUES (%s, %s, 'receive', 'in', 10, 'count correction')",
            (org_id, expired_id))
        conn.commit()
    finally:
        conn.close()

    # the FEFO service never picks expired batches: shortage, not expiry
    disp = _dispense(client, h, [(amox, 1)], branch_id=br, patient_id=p["id"])
    assert disp.status_code == 409

    # DB backstop: a raw 'dispense' movement OUT of an expired batch fails
    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_org(cur, org_id)
        _expect_blocked(
            cur,
            "INSERT INTO stock_movements (organization_id, batch_id,"
            " movement_type, direction, quantity, reason)"
            " VALUES (%s, %s, 'dispense', 'out', 1, 'tamper')",
            (org_id, expired_id), "expired")
        conn.rollback()
    finally:
        conn.close()

    # ...but writing the expired stock OFF is allowed (spoilage)
    wa = client.post(f"/api/v1/pharmacy/stock/batches/{expired_id}/adjust",
                     json={"quantity": 10, "write_off": True,
                           "reason": "Expired stock write-off"},
                     headers=h)
    assert wa.status_code == 201, wa.text
    assert wa.json()["direction"] == "out"

    # recall a fresh batch: every subsequent movement is blocked and the
    # batch header is frozen
    live = _receive(client, h, br, amox, "M5-RECALL-001", 8, days=30)
    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_org(cur, org_id)
        cur.execute("UPDATE stock_batches SET status = 'recalled' WHERE id = %s",
                    (live["id"],))
        conn.commit()
        _set_org(cur, org_id)  # the GUC is transaction-local: re-arm it
        _expect_blocked(
            cur,
            "INSERT INTO stock_movements (organization_id, batch_id,"
            " movement_type, direction, quantity, reason)"
            " VALUES (%s, %s, 'adjustment', 'in', 5, 'sneaky restock')",
            (org_id, live["id"]), "recalled")
        _expect_blocked(
            cur, "UPDATE stock_batches SET batch_number = 'X' WHERE id = %s",
            (live["id"],), "frozen")
        conn.rollback()
    finally:
        conn.close()

    # API refuses to adjust a recalled batch too
    adj = client.post(f"/api/v1/pharmacy/stock/batches/{live['id']}/adjust",
                      json={"quantity": 5, "reason": "Restock attempt"},
                      headers=h)
    assert adj.status_code == 409 and "frozen" in adj.json()["detail"]

    # and the recalled batch is invisible to FEFO: still a clean 409
    disp2 = _dispense(client, h, [(amox, 1)], branch_id=br, patient_id=p["id"])
    assert disp2.status_code == 409


def test_stock_adjustments_and_ledger_invariant(client, org_a):
    """Adjustments are reason-coded movements; on-hand is DERIVED, never
    hand-written: SUM(in) - SUM(out) == quantity_on_hand."""
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    org_id = org_a["auth"]["organization"]["id"]
    para = _drug_id_exact(client, h, "Paracetamol")
    batch = _receive(client, h, br, para, "M5-ADJ-001", 10, days=75)

    # found stock: IN movement
    found = client.post(f"/api/v1/pharmacy/stock/batches/{batch['id']}/adjust",
                        json={"quantity": 5, "reason": "Found 5 units in store"},
                        headers=h)
    assert found.status_code == 201 and found.json()["direction"] == "in"
    # spoilage: OUT movement
    spoiled = client.post(f"/api/v1/pharmacy/stock/batches/{batch['id']}/adjust",
                          json={"quantity": 4, "write_off": True,
                                "reason": "Strip damaged in transit"},
                          headers=h)
    assert spoiled.status_code == 201 and spoiled.json()["direction"] == "out"
    assert _batch(client, h, para, "M5-ADJ-001")["quantity_on_hand"] == 11

    # zero and reasonless adjustments are rejected
    assert client.post(f"/api/v1/pharmacy/stock/batches/{batch['id']}/adjust",
                       json={"quantity": 0, "reason": "noop"},
                       headers=h).status_code == 422
    assert client.post(f"/api/v1/pharmacy/stock/batches/{batch['id']}/adjust",
                       json={"quantity": 1, "reason": "x"},
                       headers=h).status_code == 422

    events = client.get("/api/v1/audit-events", params={"limit": 200},
                        headers=h).json()
    assert "pharmacy.stock_adjusted" in {e["action"] for e in events["items"]}

    # raw: the ledger is append-only and reconciles with the cached on-hand
    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_org(cur, org_id)
        cur.execute(
            "SELECT COALESCE(SUM(CASE WHEN direction='in' THEN quantity"
            " ELSE -quantity END), 0) FROM stock_movements WHERE batch_id = %s",
            (batch["id"],))
        derived = cur.fetchone()[0]
        cur.execute("SELECT quantity_on_hand FROM stock_batches WHERE id = %s",
                    (batch["id"],))
        assert derived == cur.fetchone()[0] == 11
        _expect_blocked(
            cur, "UPDATE stock_movements SET quantity = 1 WHERE batch_id = %s",
            (batch["id"],), ("append-only", "permission denied"))
        _expect_blocked(
            cur, "DELETE FROM stock_movements WHERE batch_id = %s",
            (batch["id"],), ("append-only", "permission denied"))
        conn.rollback()
    finally:
        conn.close()


def test_dispense_documents_are_immutable_raw_sql(client, org_a):
    """Dispense documents are corrections-by-return: UPDATE/DELETE blocked."""
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    org_id = org_a["auth"]["organization"]["id"]
    cet = _drug_id_exact(client, h, "Cetirizine")
    p = _patient(client, h, name="Immutable Dispense Patient")
    _receive(client, h, br, cet, "M5-IMM-001", 10, days=50)
    disp = _dispense(client, h, [(cet, 3)], branch_id=br, patient_id=p["id"])
    assert disp.status_code == 201, disp.text

    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_org(cur, org_id)
        # dispenses are immutable: the REVOKE (or the trigger) rejects changes
        _expect_blocked(cur, "DELETE FROM dispenses WHERE id = %s",
                        (disp.json()["id"],),
                        ("immutable", "permission denied"))
        _expect_blocked(cur, "UPDATE dispense_items SET quantity = 999 "
                             "WHERE dispense_id = %s",
                        (disp.json()["id"],),
                        ("immutable", "permission denied"))
        conn.rollback()
    finally:
        conn.close()


# --- pharmacy: expiry monitoring --------------------------------------------
def test_stock_alerts_expiring_expired_and_low(client, org_a):
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    org_id = org_a["auth"]["organization"]["id"]
    ors = _drug_id_exact(client, h, "ORS")
    septran = _drug_id_exact(client, h, "Trimethoprim")

    expiring = _receive(client, h, br, ors, "M5-ALERT-SOON", 7, days=20)
    low = _receive(client, h, br, septran, "M5-ALERT-LOW", 4, days=100)

    conn = _app_conn()  # an already-expired batch still on the shelf
    try:
        cur = conn.cursor()
        _set_org(cur, org_id)
        cur.execute(
            "INSERT INTO stock_batches (organization_id, branch_id, drug_id,"
            " batch_number, expiry_date, quantity_received, supplier)"
            " VALUES (%s, %s, %s, 'M5-ALERT-OLD', CURRENT_DATE - 10, 12,"
            " 'MediCorp') RETURNING id", (org_id, br, ors))
        old_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO stock_movements (organization_id, batch_id,"
            " movement_type, direction, quantity, reason)"
            " VALUES (%s, %s, 'receive', 'in', 12, 'old stock')",
            (org_id, old_id))
        conn.commit()
    finally:
        conn.close()

    alerts = client.get("/api/v1/pharmacy/stock/alerts", headers=h)
    assert alerts.status_code == 200, alerts.text
    body = alerts.json()
    soon_ids = {b["id"] for b in body["expiring_soon"]}
    assert expiring["id"] in soon_ids and low["id"] not in soon_ids
    assert low["id"] not in {b["id"] for b in body["expired"]}
    assert any(b["batch_number"] == "M5-ALERT-OLD" for b in body["expired"])
    cet_entry = next(e for e in body["low_stock"] if e["drug_id"] == septran)
    assert cet_entry["on_hand"] == 4


# --- pharmacy: RBAC ---------------------------------------------------------
def test_pharmacy_rbac_matrix(client):
    """Pharmacist stocks & dispenses; front desk only reads; support/auditor
    nothing; doctors stay out of the dispensary."""
    owner_h, br = _fresh_org(client, "clinic-m5-rx")
    para = _drug_id_exact(client, owner_h, "Paracetamol")
    p = _patient(client, owner_h, name="RBAC Rx Patient")
    _receive(client, owner_h, br, para, "M5-RBAC-001", 20, days=60)

    pharm = _member(client, owner_h, "pharm-m5@clinic-m5-rx.example.com",
                    "pharmacist", slug="clinic-m5-rx")
    recep = _member(client, owner_h, "recep-m5@clinic-m5-rx.example.com",
                    "receptionist", slug="clinic-m5-rx")
    doct = _member(client, owner_h, "doct-m5@clinic-m5-rx.example.com",
                   "doctor", slug="clinic-m5-rx")
    supp = _member(client, owner_h, "supp-m5@clinic-m5-rx.example.com",
                   "support", slug="clinic-m5-rx")
    aud = _member(client, owner_h, "aud-m5@clinic-m5-rx.example.com",
                  "auditor", slug="clinic-m5-rx")

    # pharmacist: may receive and dispense
    got = client.post("/api/v1/pharmacy/stock/batches", json={
        "branch_id": br, "drug_id": para, "batch_number": "M5-RBAC-002",
        "expiry_date": _expiry(90), "quantity": 10, "unit_cost": 3,
        "supplier": "MediCorp"}, headers=pharm)
    assert got.status_code == 201, got.text
    disp = _dispense(client, pharm, [(para, 2)], branch_id=br, patient_id=p["id"])
    assert disp.status_code == 201, disp.text

    # receptionist: reads stock, cannot receive or dispense
    assert client.get("/api/v1/pharmacy/stock/batches", headers=recep).status_code == 200
    assert client.post("/api/v1/pharmacy/stock/batches", json={
        "branch_id": br, "drug_id": para, "batch_number": "M5-RBAC-003",
        "expiry_date": _expiry(90), "quantity": 5, "unit_cost": 3,
        "supplier": "X"}, headers=recep).status_code == 403
    assert _dispense(client, recep, [(para, 1)], branch_id=br,
                     patient_id=p["id"]).status_code == 403

    # doctor: clinical yes, pharmacy writes NO
    assert client.get("/api/v1/pharmacy/stock/batches", headers=doct).status_code == 200
    assert _dispense(client, doct, [(para, 1)], branch_id=br,
                     patient_id=p["id"]).status_code == 403

    # support & auditor: zero pharmacy access (least privilege)
    for hh in (supp, aud):
        assert client.get("/api/v1/pharmacy/stock/batches", headers=hh).status_code == 403
        assert client.get("/api/v1/pharmacy/stock/alerts", headers=hh).status_code == 403


def _fresh_org(client, slug):
    """Register a tenant + branch and return (owner headers, branch id)."""
    data = register_org(client, slug=slug, email=f"owner@{slug}.example.com")
    h = _auth(data)
    branch = client.post("/api/v1/branches",
                         json={"name": "Main", "code": "MAIN"}, headers=h)
    assert branch.status_code == 201, branch.text
    return h, branch.json()["id"]


# --- laboratory -------------------------------------------------------------
def test_lab_catalog_is_global_read_only_reference(client, org_a, org_b):
    h = _auth(org_a["auth"])
    hb = _auth(org_b["auth"])
    cbc_a = _lab_test_id(client, h, "CBC")
    cbc_b = _lab_test_id(client, hb, "CBC")  # same global catalog for tenant B
    assert cbc_a == cbc_b

    p = _patient(client, h, name="Catalog Patient")
    unknown = _lab_order(client, h, p["id"], org_a["branch"]["id"],
                         [str(uuid.uuid4())])
    assert unknown.status_code == 422
    assert "Unknown lab test" in unknown.json()["detail"]

    conn = _app_conn()  # hms_app may SELECT the catalog but never write it
    try:
        cur = conn.cursor()
        _expect_blocked(
            cur,
            "INSERT INTO lab_test_catalog (code, name, specimen_type)"
            " VALUES ('FAKE', 'Fake test', 'blood')",
            (), ("permission denied",))
        conn.rollback()
    finally:
        conn.close()


def test_lab_order_result_state_machine_four_eyes(client, org_a):
    """ordered -> specimen_collected -> in_progress -> released, with
    four-eyes verification, supersession-only amendment and audited steps."""
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    doctor = _member(client, h, "dr-m5@clinic-a.example.com", "doctor")
    p = _patient(client, h, name="Lab Patient")
    cbc = _lab_test_id(client, h, "CBC")
    fbs = _lab_test_id(client, h, "FBS")

    order = _lab_order(client, h, p["id"], br, [cbc, fbs],
                       priority="urgent", clinical_notes="Fever with fatigue")
    assert order.status_code == 201, order.text
    od = order.json()
    assert od["status"] == "ordered" and od["results"] == []

    # results cannot be imported before a specimen is collected
    early = client.post(f"/api/v1/lab/orders/{od['id']}/results",
                        json={"results": [{"test_id": cbc,
                                           "value_text": "normal"}]}, headers=h)
    assert early.status_code == 409

    spec = client.post(f"/api/v1/lab/orders/{od['id']}/specimens",
                       json={"specimen_type": "blood"}, headers=h)
    assert spec.status_code == 201, spec.text
    assert spec.json()["barcode"].startswith("SPC-")
    assert client.get(f"/api/v1/lab/orders/{od['id']}",
                      headers=h).json()["status"] == "specimen_collected"
    # a second collection on the same order is refused
    assert client.post(f"/api/v1/lab/orders/{od['id']}/specimens",
                       json={"specimen_type": "blood"},
                       headers=h).status_code == 409

    imported = client.post(f"/api/v1/lab/orders/{od['id']}/results", json={
        "specimen_id": spec.json()["id"],
        "results": [
            {"test_id": cbc, "value_text": "", "value_numeric": 11.2,
             "flag": "low"},
            {"test_id": fbs, "value_text": "", "value_numeric": 142.0,
             "flag": "high"},
        ]}, headers=h)
    assert imported.status_code == 201, imported.text
    detail = imported.json()
    assert detail["status"] == "in_progress" and len(detail["results"]) == 2
    fbs_row = next(r for r in detail["results"] if r["test_id"] == fbs)
    assert fbs_row["status"] == "imported"
    assert fbs_row["unit"] == "mg/dL"          # unit/range from the catalog
    assert fbs_row["specimen_id"] == spec.json()["id"]

    # the same test cannot carry a second LIVE result (supersession only)
    again = client.post(f"/api/v1/lab/orders/{od['id']}/results", json={
        "results": [{"test_id": fbs, "value_text": "", "value_numeric": 90.0}]
    }, headers=h)
    assert again.status_code == 409
    # and a duplicate inside one payload is refused too
    dup = client.post(f"/api/v1/lab/orders/{od['id']}/results", json={
        "results": [{"test_id": cbc, "value_text": "x"},
                    {"test_id": cbc, "value_text": "y"}]}, headers=h)
    assert dup.status_code == 422

    results = detail["results"]
    cbc_row = next(r["id"] for r in results if r["test_id"] == cbc)
    fbs_row = next(r["id"] for r in results if r["test_id"] == fbs)
    # four-eyes: the importer (owner) cannot verify their own import
    own = client.post(f"/api/v1/lab/results/{cbc_row}/verify", headers=h)
    assert own.status_code == 403 and "Four-eyes" in own.json()["detail"]

    # a second person verifies both results
    for rid in (cbc_row, fbs_row):
        v = client.post(f"/api/v1/lab/results/{rid}/verify", headers=doctor)
        assert v.status_code == 200, v.text
        assert v.json()["status"] == "verified" and v.json()["verified_at"]
        # verified is a one-way door: re-verify -> 409
        assert client.post(f"/api/v1/lab/results/{rid}/verify",
                           headers=doctor).status_code == 409

    # release them; the order completes only when ALL live results are out
    rel1 = client.post(f"/api/v1/lab/results/{cbc_row}/release",
                       headers=doctor)
    assert rel1.status_code == 200
    assert client.get(f"/api/v1/lab/orders/{od['id']}",
                      headers=h).json()["status"] == "in_progress"
    rel2 = client.post(f"/api/v1/lab/results/{fbs_row}/release",
                       headers=doctor)
    assert rel2.status_code == 200
    final = client.get(f"/api/v1/lab/orders/{od['id']}", headers=h).json()
    assert final["status"] == "released"

    # amendment: NEW audited version, old row superseded (never edited)
    amended = client.post(f"/api/v1/lab/results/{fbs_row}/amend", json={
        "value_text": "", "value_numeric": 96.0, "flag": "normal",
        "reason": "Repeat fasting sample taken in error"},
        headers=h)
    assert amended.status_code == 201, amended.text
    new_row = amended.json()
    assert new_row["status"] == "imported" and new_row["id"] != fbs_row
    after = client.get(f"/api/v1/lab/orders/{od['id']}", headers=h).json()
    assert after["status"] == "in_progress"  # reopened by the amendment
    old_row = next(r for r in after["results"] if r["id"] == fbs_row)
    assert old_row["status"] == "superseded"
    assert old_row["superseded_by_id"] == new_row["id"]
    assert "Repeat fasting" in old_row["amendment_reason"]
    # the superseded content is retained untouched
    assert float(old_row["value_numeric"]) == 142.0

    # re-verify (doctor) and re-release (owner) the new version
    assert client.post(f"/api/v1/lab/results/{new_row['id']}/verify",
                       headers=doctor).status_code == 200
    assert client.post(f"/api/v1/lab/results/{new_row['id']}/release",
                       headers=h).status_code == 200
    assert client.get(f"/api/v1/lab/orders/{od['id']}",
                      headers=h).json()["status"] == "released"

    # raw SQL: released results are frozen content-wise and cannot vanish
    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_org(cur, org_a["auth"]["organization"]["id"])
        _expect_blocked(
            cur, "UPDATE lab_results SET value_numeric = 0.1 WHERE id = %s",
            (cbc_row,), ("supersession", "immutable", "frozen"))
        _expect_blocked(
            cur, "DELETE FROM lab_results WHERE id = %s",
            (cbc_row,), ("immutable",))
        # the released order header is frozen: no re-pointing the order
        _expect_blocked(
            cur, "UPDATE lab_orders SET patient_id = %s WHERE id = %s",
            (str(uuid.uuid4()), od["id"]), "frozen")
        _expect_blocked(
            cur, "UPDATE lab_orders SET priority = 'routine' WHERE id = %s",
            (od["id"],), "frozen")
        conn.rollback()
    finally:
        conn.close()

    events = client.get("/api/v1/audit-events", params={"limit": 200},
                        headers=h).json()
    actions = {e["action"] for e in events["items"]}
    assert {"lab.order_created", "lab.specimen_collected",
            "lab.results_imported", "lab.result_verified",
            "lab.result_released", "lab.result_amended"} <= actions


def test_lab_cancel_and_freeze_rules(client, org_a):
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    p = _patient(client, h, name="Cancel Patient")
    cbc = _lab_test_id(client, h, "CBC")

    # open order cancels with a reason, then everything about it is refused
    o1 = _lab_order(client, h, p["id"], br, [cbc]).json()
    cancel = client.post(f"/api/v1/lab/orders/{o1['id']}/cancel",
                         json={"reason": "Patient refused the test"}, headers=h)
    assert cancel.status_code == 200 and cancel.json()["status"] == "cancelled"
    assert client.post(f"/api/v1/lab/orders/{o1['id']}/cancel",
                       json={"reason": "again"}, headers=h).status_code == 409
    assert client.post(f"/api/v1/lab/orders/{o1['id']}/specimens",
                       json={"specimen_type": "blood"},
                       headers=h).status_code == 409

    # collected-but-not-imported orders may still be cancelled
    o2 = _lab_order(client, h, p["id"], br, [cbc]).json()
    assert client.post(f"/api/v1/lab/orders/{o2['id']}/specimens",
                       json={"specimen_type": "blood"},
                       headers=h).status_code == 201
    assert client.post(f"/api/v1/lab/orders/{o2['id']}/cancel",
                       json={"reason": "Haemolysed sample, will re-order"},
                       headers=h).status_code == 200

    # once results exist the order is closed for cancellation
    o3 = _lab_order(client, h, p["id"], br, [cbc]).json()
    assert client.post(f"/api/v1/lab/orders/{o3['id']}/specimens",
                       json={"specimen_type": "blood"},
                       headers=h).status_code == 201
    assert client.post(f"/api/v1/lab/orders/{o3['id']}/results",
                       json={"results": [{"test_id": cbc,
                                          "value_text": "ok"}]},
                       headers=h).status_code == 201
    late = client.post(f"/api/v1/lab/orders/{o3['id']}/cancel",
                       json={"reason": "changed my mind"}, headers=h)
    assert late.status_code == 409

    events = client.get("/api/v1/audit-events", params={"limit": 200},
                        headers=h).json()
    assert "lab.order_cancelled" in {e["action"] for e in events["items"]}


def test_specimen_rejection_is_the_only_transition(client, org_a):
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    p = _patient(client, h, name="Specimen Patient")
    cbc = _lab_test_id(client, h, "CBC")
    order = _lab_order(client, h, p["id"], br, [cbc]).json()
    spec = client.post(f"/api/v1/lab/orders/{order['id']}/specimens",
                       json={"specimen_type": "blood"}, headers=h).json()

    rejected = client.post(f"/api/v1/lab/specimens/{spec['id']}/reject",
                           json={"reason": "Haemolysed sample"}, headers=h)
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    # reject twice -> 409; results cannot be imported onto a rejected specimen
    assert client.post(f"/api/v1/lab/specimens/{spec['id']}/reject",
                       json={"reason": "again"},
                       headers=h).status_code == 409
    imp = client.post(f"/api/v1/lab/orders/{order['id']}/results",
                      json={"specimen_id": spec["id"],
                            "results": [{"test_id": cbc, "value_text": "x"}]},
                      headers=h)
    assert imp.status_code == 409 and "rejected" in imp.json()["detail"]

    conn = _app_conn()  # specimens are append-only but for the reject flip
    try:
        cur = conn.cursor()
        _set_org(cur, org_a["auth"]["organization"]["id"])
        _expect_blocked(cur, "UPDATE lab_specimens SET barcode = 'SPC-999999'"
                             " WHERE id = %s", (spec["id"],), "immutable")
        _expect_blocked(cur, "DELETE FROM lab_specimens WHERE id = %s",
                        (spec["id"],), ("append-only", "immutable"))
        conn.rollback()
    finally:
        conn.close()


def test_lab_rbac_matrix(client):
    """Technicians import but never release; nurses collect but never verify;
    support/auditor have zero lab access."""
    owner_h, br = _fresh_org(client, "clinic-m5-lab")
    p = _patient(client, owner_h, name="Lab RBAC Patient")
    cbc = _lab_test_id(client, owner_h, "CBC")

    tech = _member(client, owner_h, "tech-m5@clinic-m5-lab.example.com",
                   "lab_technician", slug="clinic-m5-lab")
    nurse = _member(client, owner_h, "nurse-m5@clinic-m5-lab.example.com",
                    "nurse", slug="clinic-m5-lab")
    doct = _member(client, owner_h, "doct-m5b@clinic-m5-lab.example.com",
                   "doctor", slug="clinic-m5-lab")
    billing = _member(client, owner_h, "bill-m5@clinic-m5-lab.example.com",
                      "billing", slug="clinic-m5-lab")
    pharm = _member(client, owner_h, "pharm-m5b@clinic-m5-lab.example.com",
                    "pharmacist", slug="clinic-m5-lab")
    supp = _member(client, owner_h, "supp-m5b@clinic-m5-lab.example.com",
                   "support", slug="clinic-m5-lab")

    order = _lab_order(client, doct, p["id"], br, [cbc])  # doctors may order
    assert order.status_code == 201, order.text
    oid = order.json()["id"]

    # nurse collects the specimen but cannot import/verify/release
    spec = client.post(f"/api/v1/lab/orders/{oid}/specimens",
                       json={"specimen_type": "blood"}, headers=nurse)
    assert spec.status_code == 201, spec.text
    assert client.post(f"/api/v1/lab/orders/{oid}/results",
                       json={"results": [{"test_id": cbc, "value_text": "ok"}]},
                       headers=nurse).status_code == 403

    # technician imports, but cannot release and cannot order cancellations
    imported = client.post(f"/api/v1/lab/orders/{oid}/results",
                           json={"specimen_id": spec.json()["id"],
                                 "results": [{"test_id": cbc,
                                              "value_text": "ok"}]},
                           headers=tech)
    assert imported.status_code == 201, imported.text
    rid = imported.json()["results"][0]["id"]
    assert client.post(f"/api/v1/lab/results/{rid}/release",
                       headers=tech).status_code == 403

    # pharmacist & billing: read-only lab
    assert client.get(f"/api/v1/lab/orders/{oid}", headers=pharm).status_code == 200
    assert client.get(f"/api/v1/lab/orders/{oid}",
                      headers=billing).status_code == 200
    assert client.post(f"/api/v1/lab/orders/{oid}/results",
                       json={"results": [{"test_id": cbc, "value_text": "x"}]},
                       headers=billing).status_code == 403

    # support: no lab access at all
    assert client.get("/api/v1/lab/orders", headers=supp).status_code == 403
    assert client.get("/api/v1/lab/tests", headers=supp).status_code == 403

    # doctor verifies (four-eyes: technician imported) and releases
    assert client.post(f"/api/v1/lab/results/{rid}/verify",
                       headers=doct).status_code == 200
    assert client.post(f"/api/v1/lab/results/{rid}/release",
                       headers=doct).status_code == 200


# --- tenancy ----------------------------------------------------------------
def test_m5_cross_tenant_isolation(client, org_a, org_b):
    ha, hb = _auth(org_a["auth"]), _auth(org_b["auth"])
    br_a, br_b = org_a["branch"]["id"], org_b["branch"]["id"]
    para = _drug_id_exact(client, ha, "Paracetamol")
    p_a = _patient(client, ha, name="Tenant A Patient")

    batch = _receive(client, ha, br_a, para, "M5-TENANT-001", 10, days=60)
    order = _lab_order(client, ha, p_a["id"], br_a,
                       [_lab_test_id(client, ha, "CBC")]).json()

    # org B cannot see A's stock, movements, dispenses or lab orders
    assert client.get(f"/api/v1/pharmacy/stock/batches/{batch['id']}",
                      headers=hb).status_code in (404, 405)
    assert all(b["id"] != batch["id"] for b in
               client.get("/api/v1/pharmacy/stock/batches",
                          params={"limit": 100}, headers=hb).json()["items"])
    assert client.get(f"/api/v1/lab/orders/{order['id']}",
                      headers=hb).status_code == 404
    assert client.post(f"/api/v1/lab/orders/{order['id']}/cancel",
                       json={"reason": "cross tenant"},
                       headers=hb).status_code == 404
    assert client.post(f"/api/v1/lab/orders/{order['id']}/specimens",
                       json={"specimen_type": "blood"},
                       headers=hb).status_code == 404
    assert all(i["id"] != order["id"] for i in
               client.get("/api/v1/lab/orders",
                          params={"limit": 100}, headers=hb).json()["items"])

    # B cannot dispense A's stock by pointing at A's branch (no stock there
    # for B, so the FEFO allocation finds nothing)
    p_b = _patient(client, hb, name="Tenant B Patient")
    cross = _dispense(client, hb, [(para, 1)], branch_id=br_a,
                      patient_id=p_b["id"])
    assert cross.status_code == 409

    # B cannot order labs for A's patient
    assert _lab_order(client, hb, p_a["id"], br_b,
                      [_lab_test_id(client, hb, "CBC")]).status_code == 404


def test_m5_rls_without_org_context_is_empty(client, org_a):
    """Raw proof: as hms_app with no tenant GUC, every M5 table is empty."""
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    para = _drug_id_exact(client, h, "Paracetamol")
    p = _patient(client, h, name="RLS Patient")
    _receive(client, h, br, para, "M5-RLS-001", 5, days=40)
    assert _dispense(client, h, [(para, 1)], branch_id=br,
                     patient_id=p["id"]).status_code == 201
    assert _lab_order(client, h, p["id"], br,
                      [_lab_test_id(client, h, "CBC")]).status_code == 201

    conn = _app_conn()
    try:
        cur = conn.cursor()
        for table in ("stock_batches", "dispenses", "dispense_items",
                      "stock_movements", "lab_orders", "lab_specimens",
                      "lab_results"):
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0] == 0, f"{table} leaked without org context"

        # wrong tenant context sees nothing of A's stock...
        _set_org(cur, uuid.uuid4())
        cur.execute("SELECT count(*) FROM stock_batches WHERE batch_number = %s",
                    ("M5-RLS-001",))
        assert cur.fetchone()[0] == 0

        # ...and it stays invisible under the wrong context
        cur.execute("SELECT count(*) FROM stock_batches WHERE batch_number = %s",
                    ("M5-RLS-001",))
        assert cur.fetchone()[0] == 0
        conn.rollback()
    finally:
        conn.close()
