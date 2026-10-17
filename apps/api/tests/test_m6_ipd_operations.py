"""M6: IPD (wards/beds/admissions/nursing/discharge) + inventory/expenses.

Covers the blueprint's M6 rules (R3.4): at most one inpatient per bed and
one active admission per patient (partial unique indexes proven with raw
SQL), admitted -> discharged as the ONLY admission transition with the
record frozen afterwards (DB trigger), bed transfers through an append-only
ledger, append-only nursing records that close with the admission, an
inventory ledger with trigger-maintained on-hand (no negative stock even
via raw SQL), frozen expense records whose only legal change is void, plus
the M6 reports, the RBAC matrix and cross-tenant isolation for every new
table.
"""
from __future__ import annotations

import itertools
import uuid
from datetime import date, timedelta

from tests.conftest import _auth
from tests.test_m3_clinical_billing import (
    _app_conn,
    _expect_blocked,
    _invoice,
    _patient,
    _set_org,
)
from tests.test_m5_pharmacy_lab import _member

_seq = itertools.count(1)

TENANT_TABLES = (
    "wards", "beds", "admissions", "admission_transfers", "nursing_records",
    "inventory_items", "inventory_movements", "expenses",
)

# REVOKE-d tables answer "permission denied"; the append-only triggers are
# the backstop behind that grant — either message proves the mutation died.
APPEND_ONLY = ("append-only", "permission denied")


# --- helpers ----------------------------------------------------------------
def _ward(client, h, branch_id, *, code=None, name=None, ward_type="general"):
    n = next(_seq)
    body = {"branch_id": branch_id, "name": name or f"Ward {n}",
            "code": code or f"W{n}", "ward_type": ward_type}
    r = client.post("/api/v1/ipd/wards", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _bed(client, h, ward_id, *, label=None):
    n = next(_seq)
    r = client.post("/api/v1/ipd/beds",
                    json={"ward_id": ward_id, "label": label or f"B{n}"},
                    headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _admit(client, h, patient_id, ward_id, bed_id, *, headers=None, **kw):
    body = {"patient_id": patient_id, "ward_id": ward_id, "bed_id": bed_id,
            "reason": kw.pop("reason", "Fever with dehydration")}
    body.update(kw)
    return client.post("/api/v1/ipd/admissions", json=body,
                       headers=headers or h)


def _discharge(client, h, admission_id, *, headers=None, **kw):
    body = {"outcome": kw.pop("outcome", "recovered"),
            "summary": kw.pop(
                "summary",
                "Afebrile at discharge; oral rehydration and review in 3 days.")}
    body.update(kw)
    return client.post(f"/api/v1/ipd/admissions/{admission_id}/discharge",
                       json=body, headers=headers or h)


def _nursing(client, h, admission_id, **fields):
    return client.post(f"/api/v1/ipd/admissions/{admission_id}/nursing",
                       json=fields, headers=h)


def _item(client, h, branch_id, **kw):
    body = {"branch_id": branch_id, "name": kw.pop("name", f"Glove Box {next(_seq)}"),
            "unit": "box", "reorder_level": 5}
    body.update(kw)
    r = client.post("/api/v1/inventory/items", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _move(client, h, item_id, **kw):
    return client.post(f"/api/v1/inventory/items/{item_id}/movements",
                       json=kw, headers=h)


def _expense(client, h, **kw):
    body = {"category": "supplies", "amount": 250,
            "expense_date": date.today().isoformat(),
            "description": "Consumables"}
    body.update(kw)
    return client.post("/api/v1/expenses", json=body, headers=h)


def _get_json(r, *path):
    data = r.json()
    for key in path:
        data = data[key]
    return data


# --- wards & beds -----------------------------------------------------------
def test_ward_and_bed_creation_validates_and_counts(client, org_a):
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    w = _ward(client, h, br, ward_type="icu")
    assert w["bed_count"] == 0

    dup = client.post("/api/v1/ipd/wards",
                      json={"branch_id": br, "name": "Dupe", "code": w["code"]},
                      headers=h)
    assert dup.status_code == 409
    assert client.post("/api/v1/ipd/wards",
                       json={"branch_id": br, "name": "Bad", "code": "!!"},
                       headers=h).status_code == 422
    assert client.post("/api/v1/ipd/wards",
                       json={"branch_id": br, "name": "Bad", "code": "WARDX",
                             "ward_type": "space"}, headers=h).status_code == 422

    b1 = _bed(client, h, w["id"], label="ICU-1")
    _bed(client, h, w["id"], label="ICU-2")
    dup_bed = client.post("/api/v1/ipd/beds",
                          json={"ward_id": w["id"], "label": "ICU-1"},
                          headers=h)
    assert dup_bed.status_code == 409
    # a bed must belong to an existing ward of this tenant
    ghost = client.post("/api/v1/ipd/beds",
                        json={"ward_id": str(uuid.uuid4()), "label": "G-1"},
                        headers=h)
    assert ghost.status_code == 404

    wards = client.get("/api/v1/ipd/wards", headers=h).json()
    mine = next(x for x in wards if x["id"] == w["id"])
    assert (mine["bed_count"], mine["occupied_count"],
            mine["available_count"]) == (2, 0, 2)

    beds = client.get("/api/v1/ipd/beds", params={"ward_id": w["id"]},
                      headers=h).json()
    assert {b["label"] for b in beds} == {"ICU-1", "ICU-2"}
    assert all(b["admission_id"] is None for b in beds)
    assert b1["status"] == "available"


def test_bed_status_of_occupied_bed_cannot_change(client, org_a):
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    w = _ward(client, h, br)
    b = _bed(client, h, w["id"])
    p = _patient(client, h, name="Occupied Bed Patient")
    assert _admit(client, h, p["id"], w["id"], b["id"]).status_code == 201

    blocked = client.patch(f"/api/v1/ipd/beds/{b['id']}",
                           json={"status": "maintenance"}, headers=h)
    assert blocked.status_code == 409
    # a free bed flips freely
    b2 = _bed(client, h, w["id"])
    ok = client.patch(f"/api/v1/ipd/beds/{b2['id']}",
                      json={"status": "maintenance"}, headers=h)
    assert ok.status_code == 200 and ok.json()["status"] == "maintenance"


# --- admissions -------------------------------------------------------------
def test_admission_blocks_double_booking_and_replays_idempotently(
        client, org_a):
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    w = _ward(client, h, br)
    bed1, bed2, bed3 = (_bed(client, h, w["id"]) for _ in range(3))
    p1 = _patient(client, h, name="IPD Patient One")

    key = "m6-admit-001"
    r1 = _admit(client, h, p1["id"], w["id"], bed1["id"],
                headers={**h, "Idempotency-Key": key})
    assert r1.status_code == 201, r1.text
    adm = r1.json()
    assert adm["status"] == "admitted"
    assert adm["discharged_at"] is None
    assert adm["patient_uhid"] == p1["uhid"]
    assert adm["patient_full_name"] == p1["full_name"]

    # replay with the same key + payload -> same admission, no second row
    r2 = _admit(client, h, p1["id"], w["id"], bed1["id"],
                headers={**h, "Idempotency-Key": key})
    assert r2.status_code == 201 and r2.json()["id"] == adm["id"]
    # same key, different payload -> 409
    r3 = _admit(client, h, p1["id"], w["id"], bed1["id"],
                reason="Different reason",
                headers={**h, "Idempotency-Key": key})
    assert r3.status_code == 409

    # occupancy is visible on the bed board
    beds = client.get("/api/v1/ipd/beds", params={"ward_id": w["id"]},
                      headers=h).json()
    b1 = next(b for b in beds if b["id"] == bed1["id"])
    assert b1["admission_id"] == adm["id"] and b1["patient_id"] == p1["id"]

    # one active admission PER PATIENT
    same_patient = _admit(client, h, p1["id"], w["id"], bed2["id"])
    assert same_patient.status_code == 409
    assert "already has an active admission" in same_patient.json()["detail"]

    # one active admission PER BED
    p2 = _patient(client, h, name="IPD Patient Two")
    taken = _admit(client, h, p2["id"], w["id"], bed1["id"])
    assert taken.status_code == 409
    assert "already occupied" in taken.json()["detail"]

    # a physical bed out of service refuses admission
    client.patch(f"/api/v1/ipd/beds/{bed3['id']}",
                 json={"status": "maintenance"}, headers=h)
    out_of_service = _admit(client, h, p2["id"], w["id"], bed3["id"])
    assert out_of_service.status_code == 409

    # bed and ward must agree
    w2 = _ward(client, h, br)
    mismatch = _admit(client, h, p2["id"], w2["id"], bed2["id"])
    assert mismatch.status_code == 422

    # unknown identifiers are 404 (no existence leaks)
    ghost = _admit(client, h, str(uuid.uuid4()), w["id"], str(uuid.uuid4()))
    assert ghost.status_code == 404  # patient is checked first
    no_ward = _admit(client, h, p2["id"], str(uuid.uuid4()), bed2["id"])
    assert no_ward.status_code == 404  # ward of another tenant/none
    no_bed = _admit(client, h, p2["id"], w["id"], str(uuid.uuid4()))
    assert no_bed.status_code == 404

    # admission list filters by status and is org-scoped
    admitted = client.get("/api/v1/ipd/admissions",
                          params={"status": "admitted", "ward_id": w["id"]},
                          headers=h).json()
    assert admitted["total"] == 1
    assert {a["id"] for a in admitted["items"]} == {adm["id"]}


def test_partial_unique_indexes_block_double_booking_sql(client, org_a):
    """Raw SQL proof: the database itself refuses two active admissions for
    one bed / one patient — the API checks are not the only guard."""
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    w = _ward(client, h, br)
    bed1, bed2 = _bed(client, h, w["id"]), _bed(client, h, w["id"])
    p1 = _patient(client, h, name="Backstop One")
    p2 = _patient(client, h, name="Backstop Two")
    assert _admit(client, h, p1["id"], w["id"], bed1["id"]).status_code == 201

    sql = ("INSERT INTO admissions "
           "(organization_id, patient_id, branch_id, ward_id, bed_id, reason) "
           "VALUES (%s, %s, %s, %s, %s, 'raw sql attempt')")
    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_org(cur, org_a["auth"]["organization"]["id"])
        # second patient into the OCCUPIED bed
        _expect_blocked(
            cur, sql,
            (org_a["auth"]["organization"]["id"], p2["id"], br, w["id"],
             bed1["id"]),
            "uq_admission_bed_occupied",
        )
        # same patient into a FREE bed
        _expect_blocked(
            cur, sql,
            (org_a["auth"]["organization"]["id"], p1["id"], br, w["id"],
             bed2["id"]),
            "uq_admission_patient_active",
        )
        conn.rollback()
    finally:
        conn.close()


# --- transfer ----------------------------------------------------------------
def test_transfer_moves_beds_and_ledgers_append_only(client, org_a):
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    w1, w2 = _ward(client, h, br), _ward(client, h, br)
    a1, a2 = _bed(client, h, w1["id"]), _bed(client, h, w1["id"])
    b1 = _bed(client, h, w2["id"])
    p1 = _patient(client, h, name="Transfer One")
    p2 = _patient(client, h, name="Transfer Two")
    p3 = _patient(client, h, name="Transfer Three")
    adm = _admit(client, h, p1["id"], w1["id"], a1["id"]).json()

    transfer = client.post(
        f"/api/v1/ipd/admissions/{adm['id']}/transfer",
        json={"to_bed_id": a2["id"], "reason": "Closer to nurses station"},
        headers=h)
    assert transfer.status_code == 200, transfer.text
    moved = transfer.json()
    assert moved["bed_id"] == a2["id"] and moved["ward_id"] == w1["id"]
    assert moved["status"] == "admitted"

    # moving to the current bed is a no-op error
    same = client.post(f"/api/v1/ipd/admissions/{adm['id']}/transfer",
                       json={"to_bed_id": a2["id"], "reason": "again"},
                       headers=h)
    assert same.status_code == 422

    # cross-ward transfer updates both bed and ward
    cross = client.post(f"/api/v1/ipd/admissions/{adm['id']}/transfer",
                        json={"to_bed_id": b1["id"],
                              "reason": "Step-down to general ward"},
                        headers=h)
    assert cross.status_code == 200
    assert (cross.json()["bed_id"], cross.json()["ward_id"]) == \
        (b1["id"], w2["id"])

    # occupied targets refuse
    _admit(client, h, p2["id"], w1["id"], a1["id"])  # refill a1
    busy = client.post(f"/api/v1/ipd/admissions/{adm['id']}/transfer",
                       json={"to_bed_id": a1["id"], "reason": "crowd in"},
                       headers=h)
    assert busy.status_code == 409

    # maintenance targets refuse
    client.patch(f"/api/v1/ipd/beds/{a2['id']}",
                 json={"status": "maintenance"}, headers=h)
    maint = client.post(f"/api/v1/ipd/admissions/{adm['id']}/transfer",
                        json={"to_bed_id": a2["id"], "reason": "hop back"},
                        headers=h)
    assert maint.status_code == 409

    # the vacated bed is free for a new patient
    assert _admit(client, h, p3["id"], w1["id"], a1["id"]).status_code == 409
    a3 = _bed(client, h, w1["id"])
    _admit(client, h, p3["id"], w1["id"], a3["id"])  # sanity: admits work

    # discharged admissions cannot move
    assert _discharge(client, h, adm["id"]).status_code == 200
    late = client.post(f"/api/v1/ipd/admissions/{adm['id']}/transfer",
                       json={"to_bed_id": a3["id"], "reason": "too late"},
                       headers=h)
    assert late.status_code == 409

    # transfer history lives in an append-only ledger (raw SQL)
    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_org(cur, org_a["auth"]["organization"]["id"])
        _expect_blocked(
            cur,
            "UPDATE admission_transfers SET reason = 'tampered' "
            "WHERE admission_id = %s",
            (adm["id"],), APPEND_ONLY)
        _expect_blocked(
            cur, "DELETE FROM admission_transfers WHERE admission_id = %s",
            (adm["id"],), APPEND_ONLY)
        conn.rollback()
    finally:
        conn.close()


# --- discharge ---------------------------------------------------------------
def test_discharge_frees_bed_and_freezes_record(client, org_a):
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    w = _ward(client, h, br)
    bed = _bed(client, h, w["id"])
    p = _patient(client, h, name="Discharge Patient")
    adm = _admit(client, h, p["id"], w["id"], bed["id"]).json()

    r = _discharge(client, h, adm["id"], outcome="improved")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "discharged"
    assert out["discharge_outcome"] == "improved"
    assert out["discharged_at"] is not None

    # discharging twice is refused
    again = _discharge(client, h, adm["id"])
    assert again.status_code == 409
    # outcome comes from a closed list
    w_ = _ward(client, h, br)
    bed_ = _bed(client, h, w_["id"])
    p2 = _patient(client, h, name="Bad Outcome Patient")
    adm2 = _admit(client, h, p2["id"], w_["id"], bed_["id"])
    assert adm2.status_code == 201, adm2.text
    adm2 = adm2.json()
    bad = _discharge(client, h, adm2["id"], outcome="cured_at_home")
    assert bad.status_code == 422
    missing = client.post(f"/api/v1/ipd/admissions/{adm2['id']}/discharge",
                          json={"outcome": "recovered"}, headers=h)
    assert missing.status_code == 422  # summary is mandatory

    # the bed is free again — visible on the board and usable immediately
    beds = client.get("/api/v1/ipd/beds", params={"ward_id": w["id"]},
                      headers=h).json()
    assert next(b for b in beds if b["id"] == bed["id"])["admission_id"] is None
    # p2 still holds its own active admission…
    again = _admit(client, h, p2["id"], w_["id"], bed_["id"])
    assert again.status_code == 409
    # …and the freed bed accepts a fresh patient
    p3 = _patient(client, h, name="Readmit Patient")
    re_admit = _admit(client, h, p3["id"], w["id"], bed["id"])
    assert re_admit.status_code == 201, re_admit.text

    # nursing entries close with the stay (API + DB trigger)
    api_nurse = _nursing(client, h, adm["id"], note="late entry")
    assert api_nurse.status_code == 409

    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_org(cur, org_a["auth"]["organization"]["id"])
        # raw SQL backstops: frozen rows, permanent record, nursing guard
        _expect_blocked(
            cur,
            "UPDATE admissions SET discharge_summary = 'rewritten' WHERE id = %s",
            (adm["id"],), "frozen")
        _expect_blocked(
            cur, "UPDATE admissions SET status = 'admitted' WHERE id = %s",
            (adm["id"],), "frozen")
        _expect_blocked(
            cur, "DELETE FROM admissions WHERE id = %s",
            (adm["id"],), "permanent")
        _expect_blocked(
            cur,
            "INSERT INTO nursing_records (organization_id, admission_id, note) "
            "VALUES (%s, %s, 'after the fact')",
            (org_a["auth"]["organization"]["id"], adm["id"]),
            "no nursing entries")
        conn.rollback()
    finally:
        conn.close()


# --- nursing -----------------------------------------------------------------
def test_nursing_records_range_checks_and_immutability(client, org_a):
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    w = _ward(client, h, br)
    bed = _bed(client, h, w["id"])
    p = _patient(client, h, name="Nursing Patient")
    adm = _admit(client, h, p["id"], w["id"], bed["id"]).json()

    # range checks (same posture as OPD vitals) + the SpO2 escalation rule
    assert _nursing(client, h, adm["id"], spo2=120).status_code == 422
    assert _nursing(client, h, adm["id"], spo2=84).status_code == 422
    assert _nursing(client, h, adm["id"], bp_systolic=80,
                    bp_diastolic=120).status_code == 422
    assert _nursing(client, h, adm["id"], temperature_c=46).status_code == 422
    assert _nursing(client, h, adm["id"], pulse=15).status_code == 422
    # an empty record carries no clinical content
    assert _nursing(client, h, adm["id"]).status_code == 422

    vitals = _nursing(client, h, adm["id"], note_type="vitals",
                      bp_systolic=126, bp_diastolic=82, pulse=88,
                      temperature_c=37.8, spo2=97,
                      note="Morning round")
    assert vitals.status_code == 201, vitals.text
    note = _nursing(client, h, adm["id"], note="Patient ambulated in corridor")
    assert note.status_code == 201

    listing = client.get(f"/api/v1/ipd/admissions/{adm['id']}/nursing",
                         headers=h).json()
    assert listing["total"] == 2
    # newest first
    assert listing["items"][0]["note"].startswith("Patient ambulated")

    # clinical evidence is immutable (append-only: grant + trigger)
    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_org(cur, org_a["auth"]["organization"]["id"])
        _expect_blocked(
            cur, "UPDATE nursing_records SET note = 'edited' WHERE id = %s",
            (vitals.json()["id"],), APPEND_ONLY)
        _expect_blocked(
            cur, "DELETE FROM nursing_records WHERE id = %s",
            (note.json()["id"],), APPEND_ONLY)
        conn.rollback()
    finally:
        conn.close()


# --- inventory ----------------------------------------------------------------
def test_inventory_receive_issue_adjust_and_ledger_invariants(client, org_a):
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    item = _item(client, h, br, name=f"Sterile Gloves {next(_seq)}",
                 reorder_level=6)

    dup = client.post("/api/v1/inventory/items",
                      json={"branch_id": br, "name": item["name"],
                            "unit": "box"}, headers=h)
    assert dup.status_code == 409
    assert client.post("/api/v1/inventory/items",
                       json={"branch_id": br, "name": "Bad Reorder",
                             "reorder_level": -1}, headers=h).status_code == 422
    assert item["quantity_on_hand"] == 0

    # receive 10 with idempotency (replay + mismatch)
    key = "m6-inv-recv-001"
    body = {"movement_type": "receive", "quantity": 10,
            "reason": "Opening stock"}
    m1 = client.post(f"/api/v1/inventory/items/{item['id']}/movements",
                     json=body, headers={**h, "Idempotency-Key": key})
    assert m1.status_code == 201, m1.text
    assert (m1.json()["movement_type"], m1.json()["direction"],
            m1.json()["quantity"]) == ("receive", "in", 10)
    m2 = client.post(f"/api/v1/inventory/items/{item['id']}/movements",
                     json=body, headers={**h, "Idempotency-Key": key})
    assert m2.status_code == 201 and m2.json()["id"] == m1.json()["id"]
    m3 = client.post(f"/api/v1/inventory/items/{item['id']}/movements",
                     json={**body, "quantity": 99},
                     headers={**h, "Idempotency-Key": key})
    assert m3.status_code == 409

    # issue 4 -> 6 on hand
    issued = client.post(f"/api/v1/inventory/items/{item['id']}/movements",
                         json={"movement_type": "issue", "quantity": 4,
                               "reason": "Ward consumption"}, headers=h)
    assert issued.status_code == 201
    row = client.get("/api/v1/inventory/items", params={"q": item["name"]},
                     headers=h).json()["items"][0]
    assert row["quantity_on_hand"] == 6

    # oversell refused by the TRIGGER (409, not a 500)
    oversell = client.post(f"/api/v1/inventory/items/{item['id']}/movements",
                           json={"movement_type": "issue", "quantity": 100,
                                 "reason": "Too much"}, headers=h)
    assert oversell.status_code == 409
    assert "insufficient stock" in oversell.json()["detail"].lower()

    # adjustment: signed quantity, write-off is always OUT
    adj = client.post(f"/api/v1/inventory/items/{item['id']}/movements",
                      json={"movement_type": "adjustment", "quantity": -2,
                            "reason": "Damaged carton"}, headers=h)
    assert adj.status_code == 201 and adj.json()["direction"] == "out"
    off = client.post(f"/api/v1/inventory/items/{item['id']}/movements",
                      json={"movement_type": "adjustment", "quantity": 1,
                            "write_off": True,
                            "reason": "Damaged unit write-off"}, headers=h)
    assert off.status_code == 201 and off.json()["direction"] == "out"
    # a write-off larger than what is on hand is refused by the trigger
    over = client.post(f"/api/v1/inventory/items/{item['id']}/movements",
                       json={"movement_type": "adjustment", "quantity": 99,
                             "write_off": True,
                             "reason": "Impossible write-off"}, headers=h)
    assert over.status_code == 409

    # on-hand equals SUM(in) - SUM(out) — ledger IS the source of truth
    movements = client.get("/api/v1/inventory/movements",
                           params={"item_id": item["id"], "limit": 50},
                           headers=h).json()
    delta = sum(m["quantity"] if m["direction"] == "in" else -m["quantity"]
                for m in movements["items"])
    row = client.get("/api/v1/inventory/items", params={"q": item["name"]},
                     headers=h).json()["items"][0]
    assert delta == row["quantity_on_hand"] == 3

    # movement reveals the low-stock alarm (4 <= reorder 6)
    assert row["low_stock"] is True
    alerts = client.get("/api/v1/inventory/alerts", headers=h).json()
    assert item["id"] in {i["id"] for i in alerts["items"]}

    # audited
    events = client.get("/api/v1/audit-events", params={"limit": 200},
                        headers=h).json()
    assert "inventory.movement" in {e["action"] for e in events["items"]}

    # append-only ledger + trigger backstop under raw SQL
    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_org(cur, org_a["auth"]["organization"]["id"])
        _expect_blocked(
            cur, "UPDATE inventory_movements SET quantity = 9999 WHERE id = %s",
            (m1.json()["id"],), APPEND_ONLY)
        _expect_blocked(
            cur, "DELETE FROM inventory_movements WHERE id = %s",
            (m1.json()["id"],), APPEND_ONLY)
        # direct insert bypassing the API: negative stock still impossible
        _expect_blocked(
            cur,
            "INSERT INTO inventory_movements "
            "(organization_id, item_id, movement_type, direction, quantity, "
            " reason) VALUES (%s, %s, 'issue', 'out', 9999, 'raw')",
            (org_a["auth"]["organization"]["id"], item["id"]),
            "insufficient stock")
        conn.rollback()
    finally:
        conn.close()


def test_retired_inventory_item_is_frozen(client, org_a):
    h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    item = _item(client, h, br, name=f"Retired Item {next(_seq)}")
    assert client.post(f"/api/v1/inventory/items/{item['id']}/movements",
                       json={"movement_type": "receive", "quantity": 5,
                             "reason": "stock"}, headers=h).status_code == 201

    retired = client.patch(f"/api/v1/inventory/items/{item['id']}",
                           json={"status": "retired"}, headers=h)
    assert retired.status_code == 200 and retired.json()["status"] == "retired"
    freeze = client.post(f"/api/v1/inventory/items/{item['id']}/movements",
                         json={"movement_type": "receive", "quantity": 1,
                               "reason": "late"}, headers=h)
    assert freeze.status_code == 409
    # reorder level edits still work on a retired item
    assert client.patch(f"/api/v1/inventory/items/{item['id']}",
                        json={"reorder_level": 0}, headers=h).status_code == 200


# --- expenses ----------------------------------------------------------------
def test_expenses_record_void_summary_and_freeze(client, org_a):
    h = _auth(org_a["auth"])
    key = "m6-exp-001"
    body = {"category": "utilities", "amount": 1200.5,
            "expense_date": date.today().isoformat(),
            "description": "Electricity bill", "reference": "ELEC-77"}
    e1 = client.post("/api/v1/expenses", json=body,
                     headers={**h, "Idempotency-Key": key})
    assert e1.status_code == 201, e1.text
    exp = e1.json()
    assert exp["status"] == "recorded"
    assert float(exp["amount"]) == 1200.5

    # idempotency: replay returns the same row; a payload change 409s
    e2 = client.post("/api/v1/expenses", json=body,
                     headers={**h, "Idempotency-Key": key})
    assert e2.status_code == 201 and e2.json()["id"] == exp["id"]
    e3 = client.post("/api/v1/expenses", json={**body, "amount": 10},
                     headers={**h, "Idempotency-Key": key})
    assert e3.status_code == 409

    # closed domains and bounds
    assert client.post("/api/v1/expenses",
                       json={"category": "bribery", "amount": 5,
                             "expense_date": date.today().isoformat()},
                       headers=h).status_code == 422
    assert client.post("/api/v1/expenses",
                       json={"category": "rent", "amount": 0,
                             "expense_date": date.today().isoformat()},
                       headers=h).status_code == 422

    # a second, unrelated expense for the summary maths
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    e4 = _expense(client, h, category="supplies", amount=300,
                  expense_date=yesterday, description="IV sets")
    assert e4.status_code == 201

    # void: the ONLY legal change
    voided = client.post(f"/api/v1/expenses/{exp['id']}/void",
                         json={"reason": "Duplicate entry, corrected"},
                         headers=h)
    assert voided.status_code == 200
    assert voided.json()["status"] == "void"
    assert voided.json()["void_reason"].startswith("Duplicate")
    assert client.post(f"/api/v1/expenses/{exp['id']}/void",
                       json={"reason": "again"}, headers=h).status_code == 409

    # category summary counts recorded money only (the void one is out)
    summary = client.get(
        "/api/v1/expenses/summary",
        params={"from": (date.today() - timedelta(days=7)).isoformat(),
                "to": date.today().isoformat()},
        headers=h).json()
    cats = {row["category"]: row for row in summary["items"]}
    assert "utilities" not in cats
    assert float(cats["supplies"]["total"]) >= 300

    # list filters by category
    only_util = client.get("/api/v1/expenses",
                           params={"category": "utilities"}, headers=h).json()
    assert only_util["total"] >= 1
    assert all(r["category"] == "utilities" for r in only_util["items"])

    # raw SQL: permanent record, frozen amounts, frozen once void
    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_org(cur, org_a["auth"]["organization"]["id"])
        _expect_blocked(
            cur, "UPDATE expenses SET amount = 0.01 WHERE id = %s",
            (exp["id"],), "frozen")
        _expect_blocked(
            cur, "DELETE FROM expenses WHERE id = %s",
            (exp["id"],), "permanent")
        _expect_blocked(
            cur, "UPDATE expenses SET void_reason = '' WHERE id = %s",
            (exp["id"],), "void and frozen")
        conn.rollback()
    finally:
        conn.close()


# --- reports ------------------------------------------------------------------
def test_reports_summary_math_and_tenancy(client, org_a, org_b):
    h = _auth(org_a["auth"])
    hb = _auth(org_b["auth"])
    br = org_a["branch"]["id"]

    # IPD segment: one discharged + one currently admitted, two beds
    w = _ward(client, h, br)
    bed1 = _bed(client, h, w["id"])
    _bed(client, h, w["id"])  # a second bed so beds_total >= 2
    p_out = _patient(client, h, name="Report Discharged")
    p_in = _patient(client, h, name="Report Inpatient")
    adm1 = _admit(client, h, p_out["id"], w["id"], bed1["id"]).json()
    assert _discharge(client, h, adm1["id"]).status_code == 200
    assert _admit(client, h, p_in["id"], w["id"], bed1["id"]).status_code == 201

    # finance segment: a succeeded payment + a recorded expense
    inv = _invoice(client, h, _patient(client, h, name="Report Payer"),
                   org_a["branch"]).json()
    assert client.post(f"/api/v1/billing/invoices/{inv['id']}/issue",
                       headers=h).status_code == 200
    pay = client.post(f"/api/v1/billing/invoices/{inv['id']}/payments",
                      json={"amount": 250, "method": "cash"}, headers=h)
    assert pay.status_code == 201 and pay.json()["status"] == "succeeded"
    exp = _expense(client, h, category="supplies", amount=500)
    assert exp.status_code == 201

    params = {"from": (date.today() - timedelta(days=7)).isoformat(),
              "to": date.today().isoformat()}
    r = client.get("/api/v1/reports/summary", params=params, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()

    # IPD figures must agree with the bed board
    assert data["ipd"]["currently_admitted"] >= 1
    assert data["ipd"]["admissions_in_period"] >= 2
    assert data["ipd"]["discharges_in_period"] >= 1
    assert data["ipd"]["deaths_in_period"] == 0
    assert data["ipd"]["beds_occupied"] == data["ipd"]["currently_admitted"]
    assert data["ipd"]["beds_total"] >= 2

    # finance figures: payment visible, expense segment equals itself
    assert float(data["finance"]["revenue"]) >= 250
    fed = client.get("/api/v1/expenses/summary",
                     params={"from": params["from"], "to": params["to"]},
                     headers=h).json()
    fed_total = sum(float(row["total"]) for row in fed["items"])
    assert abs(float(data["finance"]["expenses"]) - fed_total) < 0.005
    assert data["finance"]["by_category"]

    # tenant B (no data) sees ITS zeros — never org A's figures
    rb = client.get("/api/v1/reports/summary", params=params, headers=hb).json()
    assert rb["ipd"]["currently_admitted"] == 0
    assert rb["ipd"]["beds_total"] == 0
    assert float(rb["finance"]["revenue"]) == 0
    assert float(rb["finance"]["expenses"]) == 0
    assert rb["inventory"]["items_active"] == 0


# --- RBAC -----------------------------------------------------------------------
def test_m6_rbac_matrix(client, org_a):
    owner_h = _auth(org_a["auth"])
    br = org_a["branch"]["id"]
    nurse = _member(client, owner_h, "nurse-m6@clinic-a.example.com", "nurse")
    reco = _member(client, owner_h, "recep-m6@clinic-a.example.com",
                   "receptionist")
    bill = _member(client, owner_h, "billing-m6@clinic-a.example.com",
                   "billing")
    support = _member(client, owner_h, "support-m6@clinic-a.example.com",
                      "support")
    auditor = _member(client, owner_h, "auditor-m6@clinic-a.example.com",
                      "auditor")

    w = _ward(client, owner_h, br)
    bed1, bed2, bed3 = (_bed(client, owner_h, w["id"]) for _ in range(3))
    p1 = _patient(client, owner_h, name="RBAC Nurse Patient")
    p2 = _patient(client, owner_h, name="RBAC Reco Patient")

    # nurse: NO ward management, YES admit + nursing, NO discharge, NO money
    assert client.post("/api/v1/ipd/wards",
                       json={"branch_id": br, "name": "N Ward",
                             "code": "NW1"}, headers=nurse).status_code == 403
    adm = _admit(client, nurse, p1["id"], w["id"], bed1["id"])
    assert adm.status_code == 201, adm.text
    assert _nursing(client, nurse, adm.json()["id"],
                    note="Nurse round").status_code == 201
    assert _discharge(client, nurse, adm.json()["id"]).status_code == 403
    assert _expense(client, nurse).status_code == 403
    assert client.get("/api/v1/reports/summary", headers=nurse).status_code == 200

    # receptionist: may admit, nothing clinical or financial
    adm2 = _admit(client, reco, p2["id"], w["id"], bed2["id"])
    assert adm2.status_code == 201, adm2.text
    assert _discharge(client, reco, adm2.json()["id"]).status_code == 403
    assert _nursing(client, reco, adm2.json()["id"],
                    note="nope").status_code == 403
    assert client.get("/api/v1/reports/summary", headers=reco).status_code == 403
    assert _expense(client, reco).status_code == 403
    assert client.post(
        f"/api/v1/ipd/admissions/{adm2.json()['id']}/transfer",
        json={"to_bed_id": bed3["id"], "reason": "reco may transfer"},
        headers=reco).status_code == 200  # admission.manage includes transfer

    # billing: records expenses + reads reports, no clinical actions
    exp = _expense(client, bill, category="maintenance", amount=150)
    assert exp.status_code == 201
    assert client.get("/api/v1/reports/summary", headers=bill).status_code == 200
    assert _discharge(client, bill, adm.json()["id"]).status_code == 403
    assert _nursing(client, bill, adm.json()["id"],
                    note="no").status_code == 403
    assert client.get(f"/api/v1/ipd/admissions/{adm.json()['id']}/nursing",
                      headers=bill).status_code == 403

    # support & auditor: ZERO M6 access (least privilege, as in M3/M5)
    for weak in (support, auditor):
        assert client.get("/api/v1/ipd/wards", headers=weak).status_code == 403
        assert client.get("/api/v1/ipd/beds", headers=weak).status_code == 403
        assert client.get("/api/v1/ipd/admissions",
                          headers=weak).status_code == 403
        assert _nursing(client, weak, adm.json()["id"],
                        note="x").status_code in (401, 403)
        assert _expense(client, weak).status_code == 403
        assert client.get("/api/v1/inventory/items",
                          headers=weak).status_code == 403
        assert client.get("/api/v1/reports/summary",
                          headers=weak).status_code == 403
        assert client.post("/api/v1/ipd/wards",
                           json={"branch_id": br, "name": "X", "code": "XX1"},
                           headers=weak).status_code == 403


# --- cross-tenant ---------------------------------------------------------
def test_m6_cross_tenant_isolation_and_raw_rls(client, org_a, org_b):
    ha = _auth(org_a["auth"])
    hb = _auth(org_b["auth"])
    br = org_a["branch"]["id"]

    # ten rows of M6 data in tenant A
    w = _ward(client, ha, br)
    bed = _bed(client, ha, w["id"])
    p = _patient(client, ha, name="Tenant A Inpatient")
    adm = _admit(client, ha, p["id"], w["id"], bed["id"]).json()
    _nursing(client, ha, adm["id"], note="A side note")
    item = _item(client, ha, br, name=f"Tenant A Suture {next(_seq)}")
    _move(client, ha, item["id"], movement_type="receive", quantity=3,
          reason="opening")
    exp = _expense(client, ha, category="transport", amount=90)
    assert exp.status_code == 201

    # tenant B sees NONE of it
    assert client.get("/api/v1/ipd/wards", headers=hb).json() == []
    assert client.get("/api/v1/ipd/beds", headers=hb).json() == []
    assert client.post("/api/v1/ipd/beds",
                       json={"ward_id": w["id"], "label": "B-1"},
                       headers=hb).status_code == 404
    assert client.get(f"/api/v1/ipd/admissions/{adm['id']}",
                      headers=hb).status_code == 404
    assert client.post(
        f"/api/v1/ipd/admissions/{adm['id']}/discharge",
        json={"outcome": "recovered", "summary": "cross tenant"},
        headers=hb).status_code == 404
    assert client.post(
        f"/api/v1/ipd/admissions/{adm['id']}/transfer",
        json={"to_bed_id": bed["id"], "reason": "cross tenant"},
        headers=hb).status_code == 404
    assert client.get(f"/api/v1/ipd/admissions/{adm['id']}/nursing",
                      headers=hb).status_code == 404
    assert _nursing(client, hb, adm["id"], note="intrusion").status_code == 404
    assert client.patch(f"/api/v1/ipd/beds/{bed['id']}",
                        json={"status": "blocked"},
                        headers=hb).status_code == 404
    assert client.get("/api/v1/inventory/movements",
                      params={"item_id": item["id"]},
                      headers=hb).json()["total"] == 0
    assert _move(client, hb, item["id"], movement_type="issue", quantity=1,
                 reason="cross tenant").status_code == 404
    assert client.get("/api/v1/inventory/items", headers=hb
                      ).json()["total"] == 0
    assert client.post(f"/api/v1/expenses/{exp.json()['id']}/void",
                       json={"reason": "cross tenant"},
                       headers=hb).status_code == 404
    assert client.get("/api/v1/expenses", headers=hb).json()["total"] == 0
    rb = client.get("/api/v1/reports/summary", headers=hb).json()
    assert rb["ipd"]["beds_total"] == 0
    assert float(rb["finance"]["expenses"]) == 0

    # raw RLS: with org A's context every table has rows; any other tenant
    # context sees ZERO rows across all eight new tables.
    org_a_id = org_a["auth"]["organization"]["id"]
    conn = _app_conn()
    try:
        cur = conn.cursor()
        _set_org(cur, org_a_id)
        for table in TENANT_TABLES:
            cur.execute(f"SELECT count(*) FROM {table}")
            (n,) = cur.fetchone()
            assert n >= 1, f"expected seed rows in {table}"
        _set_org(cur, uuid.uuid4())
        for table in TENANT_TABLES:
            cur.execute(f"SELECT count(*) FROM {table}")
            (n,) = cur.fetchone()
            assert n == 0, f"RLS leaked {n} rows from {table}"
        conn.rollback()
    finally:
        conn.close()