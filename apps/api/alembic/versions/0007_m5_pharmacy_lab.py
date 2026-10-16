"""M5: pharmacy stock (batch/expiry/FEFO) + laboratory orders and results.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-14

Security / integrity notes
--------------------------
* Every tenant table carries ``organization_id`` with strict RLS policies
  (USING + WITH CHECK) — same pattern as M1–M3 tables.
* ``stock_movements`` is the single source of truth for stock on hand
  (append-only ledger, same guarantees as ``ledger_events``): the batch's
  current quantity is derived as SUM(in) − SUM(out) and cached on the batch
  row by a trigger, so a raw-SQL oversell or negative stock is impossible.
* FEFO (first-expiry-first-out) is enforced by the dispense service which
  locks batches in expiry order; expired or recalled batches cannot be
  dispensed (DB trigger backstop).
* ``lab_results`` follows the imported → verified → released state machine
  with column-level freezing (same trigger technique as signed Rx);
  verification is four-eyes (verifier ≠ importer) and released results can
  only be superseded by a NEW audited version, never edited in place.
* ``lab_test_catalog`` and ``drug_catalog`` are GLOBAL reference data —
  read-only for ``hms_app``.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

GUC_ORG = "nullif(current_setting('app.current_org_id', true), '')::uuid"

M5_PERMS = [
    ("pharmacy.read", "View stock batches, levels and dispensing history"),
    ("pharmacy.manage", "Receive stock, adjust batches and manage expiry"),
    ("pharmacy.dispense", "Dispense medicines against prescriptions (FEFO)"),
    ("lab.read", "View lab orders, specimens and results"),
    ("lab.order", "Place and cancel lab orders, collect specimens"),
    ("lab.import", "Import lab results (technicians)"),
    ("lab.verify", "Verify imported results (second person, four-eyes)"),
    ("lab.release", "Release verified results to the patient record"),
]

ROLE_GRANTS = {
    "owner": [p for p, _ in M5_PERMS],
    "admin": [p for p, _ in M5_PERMS],
    "doctor": ["pharmacy.read", "lab.read", "lab.order", "lab.verify",
               "lab.release"],
    "nurse": ["pharmacy.read", "lab.read", "lab.order"],
    "receptionist": ["pharmacy.read", "lab.read"],
    "pharmacist": ["pharmacy.read", "pharmacy.manage", "pharmacy.dispense",
                   "lab.read"],
    "lab_technician": ["lab.read", "lab.order", "lab.import", "lab.verify"],
    "billing": ["pharmacy.read", "lab.read"],
    # auditor, support: no M5 grants (least privilege — consistent with M3:
    # auditor/support hold zero clinical and zero financial data access).
}

# Global validated lab-test reference (code, name, specimen, unit, range)
LAB_TESTS = [
    ("CBC", "Complete Blood Count", "blood", "", "age/sex specific"),
    ("HBA1C", "Glycated Haemoglobin", "blood", "%", "< 5.7 normal"),
    ("FBS", "Fasting Blood Sugar", "blood", "mg/dL", "70-100"),
    ("RBS", "Random Blood Sugar", "blood", "mg/dL", "< 140"),
    ("TSH", "Thyroid Stimulating Hormone", "blood", "mIU/L", "0.4-4.0"),
    ("CREAT", "Serum Creatinine", "blood", "mg/dL", "0.6-1.3"),
    ("UREA", "Blood Urea", "blood", "mg/dL", "15-40"),
    ("LIPID", "Lipid Profile", "blood", "mg/dL", "fasting"),
    ("LFT", "Liver Function Tests", "blood", "U/L", "panel"),
    ("PTINR", "Prothrombin Time / INR", "blood", "INR", "0.8-1.2"),
    ("CRP", "C-Reactive Protein", "blood", "mg/L", "< 6"),
    ("URINE", "Urine Routine Microscopy", "urine", "", "negative"),
    ("HIV", "HIV (ELISA)", "blood", "", "non-reactive"),
    ("HBSAG", "Hepatitis B Surface Antigen", "blood", "", "non-reactive"),
    ("HCV", "Hepatitis C Antibody", "blood", "", "non-reactive"),
    ("DENGE", "Dengue NS1 / IgM-IgG", "blood", "", "negative"),
    ("MALARIA", "Malaria ICT (HRP2/pLDH)", "blood", "", "negative"),
    ("WIDAL", "Widal Test", "blood", "", "negative"),
    ("THYPHR", "Typhoid IgM (Typhidot)", "blood", "", "negative"),
    ("BGRH", "Blood Group & Rh", "blood", "", "A/B/O/Rh"),
    ("PREG", "Pregnancy Test (hCG)", "urine", "", "negative"),
    ("STOOL", "Stool Routine + Occult Blood", "stool", "", "negative"),
    ("AFB", "Sputum AFB (Ziehl-Neelsen)", "sputum", "", "negative"),
    ("CULTURE", "Urine Culture & Sensitivity", "urine", "CFU/mL", "< 10^4"),
]


def upgrade() -> None:
    # --------------------------------------------------------------- tables
    op.create_table(
        "stock_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("branches.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("drug_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("drug_catalog.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("batch_number", sa.String(60), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("quantity_received", sa.Integer(), nullable=False),
        sa.Column("quantity_on_hand", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False,
                  server_default="0"),
        sa.Column("supplier", sa.String(200), nullable=False,
                  server_default=""),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="active"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("received_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "branch_id", "drug_id",
                            "batch_number", name="uq_batch_org_branch_drug_no"),
        sa.CheckConstraint("quantity_received > 0",
                           name="ck_batch_qty_received_positive"),
        sa.CheckConstraint("quantity_on_hand >= 0",
                           name="ck_batch_on_hand_nonneg"),
        sa.CheckConstraint("unit_cost >= 0", name="ck_batch_unit_cost_nonneg"),
        sa.CheckConstraint(
            "status IN ('active','depleted','expired','recalled')",
            name="ck_batch_status"),
    )
    op.create_index("ix_batches_org_drug_expiry", "stock_batches",
                    ["organization_id", "drug_id", "expiry_date"])
    op.create_index("ix_batches_org_branch", "stock_batches",
                    ["organization_id", "branch_id"])
    op.create_index("ix_batches_org_status", "stock_batches",
                    ["organization_id", "status"])

    op.create_table(
        "dispenses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("branches.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("prescription_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("prescriptions.id", ondelete="RESTRICT"),
                  nullable=True),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("patients.id", ondelete="RESTRICT"),
                  nullable=True),
        sa.Column("notes", sa.String(500), nullable=False,
                  server_default=""),
        sa.Column("dispensed_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "prescription_id IS NOT NULL OR patient_id IS NOT NULL",
            name="ck_dispense_has_subject"),
    )
    op.create_index("ix_dispenses_org_created", "dispenses",
                    ["organization_id", "created_at"])
    op.create_index("ix_dispenses_org_rx", "dispenses",
                    ["organization_id", "prescription_id"])

    op.create_table(
        "dispense_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("dispense_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("dispenses.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("stock_batches.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("drug_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("drug_catalog.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False,
                  server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("quantity > 0", name="ck_dispitem_qty_positive"),
        sa.CheckConstraint("unit_price >= 0",
                           name="ck_dispitem_price_nonneg"),
    )
    op.create_index("ix_dispitem_org_dispense", "dispense_items",
                    ["organization_id", "dispense_id"])
    op.create_index("ix_dispitem_org_batch", "dispense_items",
                    ["organization_id", "batch_id"])

    op.create_table(
        "stock_movements",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("stock_batches.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("movement_type", sa.String(20), nullable=False),
        sa.Column("direction", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("dispense_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("dispenses.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("patients.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("reason", sa.String(300), nullable=False,
                  server_default=""),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "movement_type IN ('receive','dispense','adjustment','return')",
            name="ck_movement_type"),
        sa.CheckConstraint("direction IN ('in','out')",
                           name="ck_movement_direction"),
        sa.CheckConstraint("quantity > 0", name="ck_movement_qty_positive"),
    )
    op.create_index("ix_movement_org_batch", "stock_movements",
                    ["organization_id", "batch_id", "id"])

    op.create_table(
        "lab_test_catalog",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(30), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("specimen_type", sa.String(30), nullable=False),
        sa.Column("unit", sa.String(40), nullable=False, server_default=""),
        sa.Column("reference_range", sa.String(120), nullable=False,
                  server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
    )

    op.create_table(
        "lab_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("patients.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("encounter_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("encounters.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("branches.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="ordered"),
        sa.Column("priority", sa.String(10), nullable=False,
                  server_default="routine"),
        sa.Column("clinical_notes", sa.Text(), nullable=False,
                  server_default=""),
        sa.Column("cancel_reason", sa.String(300), nullable=False,
                  server_default=""),
        sa.Column("ordered_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('ordered','specimen_collected','in_progress',"
            "'released','cancelled')",
            name="ck_laborder_status"),
        sa.CheckConstraint("priority IN ('routine','urgent')",
                           name="ck_laborder_priority"),
    )
    op.create_index("ix_laborder_org_status", "lab_orders",
                    ["organization_id", "status"])
    op.create_index("ix_laborder_org_patient", "lab_orders",
                    ["organization_id", "patient_id"])

    op.create_table(
        "lab_specimens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("lab_orders.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("barcode", sa.String(20), nullable=False),
        sa.Column("specimen_type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="collected"),
        sa.Column("rejection_reason", sa.String(300), nullable=False,
                  server_default=""),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("collected_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "barcode",
                            name="uq_specimen_org_barcode"),
        sa.CheckConstraint("status IN ('collected','rejected')",
                           name="ck_specimen_status"),
        sa.CheckConstraint(
            "specimen_type IN ('blood','urine','stool','swab','sputum',"
            "'tissue','other')",
            name="ck_specimen_type"),
    )
    op.create_index("ix_specimen_org_order", "lab_specimens",
                    ["organization_id", "order_id"])

    op.create_table(
        "lab_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("lab_orders.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("test_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("lab_test_catalog.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("specimen_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("lab_specimens.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("value_text", sa.String(200), nullable=False,
                  server_default=""),
        sa.Column("value_numeric", sa.Numeric(14, 3), nullable=True),
        sa.Column("unit", sa.String(40), nullable=False, server_default=""),
        sa.Column("reference_range", sa.String(120), nullable=False,
                  server_default=""),
        sa.Column("flag", sa.String(20), nullable=False,
                  server_default="normal"),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="imported"),
        # deferrable FK: the amendment UPDATE that points the OLD row at its
        # replacement runs BEFORE the replacement row is inserted (the
        # partial unique index on live results forces that order); the FK is
        # checked at COMMIT, when both rows exist.
        sa.Column("superseded_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("lab_results.id", ondelete="SET NULL",
                                deferrable=True, initially="deferred"),
                  nullable=True),
        sa.Column("amendment_reason", sa.String(300), nullable=False,
                  server_default=""),
        sa.Column("imported_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("verified_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('imported','verified','released','superseded')",
            name="ck_labresult_status"),
        sa.CheckConstraint("flag IN ('normal','low','high','abnormal',"
                           "'critical')", name="ck_labresult_flag"),
    )
    op.create_index("ix_labresult_org_order", "lab_results",
                    ["organization_id", "order_id"])
    op.create_index("ix_labresult_org_status", "lab_results",
                    ["organization_id", "status"])
    # exactly one live result per test per order (amendments supersede)
    op.execute("""
        CREATE UNIQUE INDEX uq_labresult_live_per_test
        ON lab_results (order_id, test_id)
        WHERE status <> 'superseded';
    """)

    # ------------------------------------------------------- reference data
    lab_tests = sa.table(
        "lab_test_catalog",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("specimen_type", sa.String),
        sa.column("unit", sa.String),
        sa.column("reference_range", sa.String),
    )
    op.bulk_insert(
        lab_tests,
        [{"code": c, "name": n, "specimen_type": s, "unit": u,
          "reference_range": r} for c, n, s, u, r in LAB_TESTS],
    )

    # ------------------------------------------------------- RBAC seed
    perms = sa.table("permissions", sa.column("key", sa.String),
                     sa.column("description", sa.Text))
    op.bulk_insert(perms, [{"key": k, "description": d}
                           for k, d in M5_PERMS])

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, key FROM roles")).fetchall()
    role_ids = {key: str(rid) for rid, key in rows}
    link_rows = [
        {"role_id": role_ids[role], "permission_key": perm}
        for role, perm_list in ROLE_GRANTS.items()
        for perm in perm_list
    ]
    op.bulk_insert(
        sa.table("role_permissions", sa.column("role_id", sa.String),
                 sa.column("permission_key", sa.String)),
        link_rows,
    )

    # --------------------------------------------------- row-level security
    tenant_tables = [
        "stock_batches", "dispenses", "dispense_items", "stock_movements",
        "lab_orders", "lab_specimens", "lab_results",
    ]
    for table in tenant_tables:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"""
            CREATE POLICY {table}_tenant ON {table}
            USING (organization_id = {GUC_ORG})
            WITH CHECK (organization_id = {GUC_ORG});
        """)

    # ------------------------------------------- immutability guarantees
    # Stock: the movement ledger is append-only; the trigger maintains the
    # cached on-hand quantity and enforces expiry / recall / oversell rules.
    op.execute("""
        CREATE OR REPLACE FUNCTION apply_stock_movement() RETURNS trigger AS $$
        DECLARE
            v_org uuid; v_status text; v_expiry date; v_onhand integer;
        BEGIN
            SELECT organization_id, status, expiry_date, quantity_on_hand
              INTO v_org, v_status, v_expiry, v_onhand
              FROM stock_batches WHERE id = NEW.batch_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'batch % not found', NEW.batch_id;
            END IF;
            IF v_org <> NEW.organization_id THEN
                RAISE EXCEPTION 'movement/batch tenancy mismatch';
            END IF;
            IF v_status = 'recalled' THEN
                RAISE EXCEPTION 'batch % is recalled; no movements allowed',
                    NEW.batch_id;
            END IF;
            IF NEW.direction = 'in' THEN
                UPDATE stock_batches
                   SET quantity_on_hand = quantity_on_hand + NEW.quantity,
                       status = CASE WHEN status = 'depleted' THEN 'active'
                                     ELSE status END,
                       updated_at = now()
                 WHERE id = NEW.batch_id;
            ELSE
                IF NEW.movement_type = 'dispense' AND v_expiry < CURRENT_DATE
                THEN
                    RAISE EXCEPTION
                        'batch % expired on %; expired stock cannot be dispensed',
                        NEW.batch_id, v_expiry;
                END IF;
                IF v_onhand < NEW.quantity THEN
                    RAISE EXCEPTION
                        'insufficient stock in batch %: % on hand, % requested',
                        NEW.batch_id, v_onhand, NEW.quantity;
                END IF;
                UPDATE stock_batches
                   SET quantity_on_hand = quantity_on_hand - NEW.quantity,
                       status = CASE
                           WHEN quantity_on_hand - NEW.quantity = 0
                           THEN 'depleted' ELSE status END,
                       updated_at = now()
                 WHERE id = NEW.batch_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_movement_apply
        BEFORE INSERT ON stock_movements
        FOR EACH ROW EXECUTE FUNCTION apply_stock_movement();
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION forbid_movement_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'stock_movements is append-only (blocked %)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_movement_append_only
        BEFORE UPDATE OR DELETE ON stock_movements
        FOR EACH ROW EXECUTE FUNCTION forbid_movement_mutation();
    """)
    # dispense documents are corrections-by-return, never edited in place
    op.execute("""
        CREATE OR REPLACE FUNCTION forbid_dispense_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'dispenses are immutable (blocked %)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_dispense_immutable
        BEFORE UPDATE OR DELETE ON dispenses
        FOR EACH ROW EXECUTE FUNCTION forbid_dispense_mutation();
    """)
    op.execute("""
        CREATE TRIGGER trg_dispense_item_immutable
        BEFORE UPDATE OR DELETE ON dispense_items
        FOR EACH ROW EXECUTE FUNCTION forbid_dispense_mutation();
    """)
    # recalled batches are frozen (the movement trigger blocks activity too)
    op.execute("""
        CREATE OR REPLACE FUNCTION forbid_recalled_batch_change() RETURNS trigger AS $$
        BEGIN
            IF OLD.status = 'recalled'
               AND (NEW.status <> 'recalled'
                    OR NEW.quantity_received <> OLD.quantity_received
                    OR NEW.expiry_date <> OLD.expiry_date
                    OR NEW.batch_number <> OLD.batch_number) THEN
                RAISE EXCEPTION 'batch % is recalled and frozen', OLD.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_batch_recall_frozen
        BEFORE UPDATE ON stock_batches
        FOR EACH ROW EXECUTE FUNCTION forbid_recalled_batch_change();
    """)

    # Lab specimens: collected -> rejected is the only mutable transition
    op.execute("""
        CREATE OR REPLACE FUNCTION forbid_specimen_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'lab_specimens is append-only (blocked DELETE)';
            END IF;
            IF NOT (
                OLD.status = 'collected' AND NEW.status = 'rejected'
                AND (to_jsonb(NEW) - 'status' - 'rejection_reason'
                        - 'updated_at') =
                    (to_jsonb(OLD) - 'status' - 'rejection_reason'
                        - 'updated_at')
            ) THEN
                RAISE EXCEPTION 'lab specimen % is immutable', OLD.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_specimen_immutable
        BEFORE UPDATE OR DELETE ON lab_specimens
        FOR EACH ROW EXECUTE FUNCTION forbid_specimen_mutation();
    """)

    # Lab results: imported -> verified -> released -> superseded, with
    # column-level freezing at every step (technique from signed Rx).
    op.execute("""
        CREATE OR REPLACE FUNCTION forbid_labresult_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'imported' THEN
                    RAISE EXCEPTION
                        'lab result % is % and immutable (blocked DELETE)',
                        OLD.id, OLD.status;
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.status = 'imported' THEN
                IF NEW.status NOT IN ('imported', 'verified') THEN
                    RAISE EXCEPTION
                        'lab result %: illegal transition % -> %',
                        OLD.id, OLD.status, NEW.status;
                END IF;
                IF NEW.status = 'verified' AND NEW.status <> OLD.status THEN
                    IF (to_jsonb(NEW) - 'status' - 'verified_by'
                            - 'verified_at' - 'updated_at') <>
                       (to_jsonb(OLD) - 'status' - 'verified_by'
                            - 'verified_at' - 'updated_at') THEN
                        RAISE EXCEPTION
                            'lab result % content frozen at verification',
                            OLD.id;
                    END IF;
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status = 'verified' THEN
                IF NOT (
                    NEW.status = 'released'
                    AND (to_jsonb(NEW) - 'status' - 'released_by'
                            - 'released_at' - 'updated_at') =
                        (to_jsonb(OLD) - 'status' - 'released_by'
                            - 'released_at' - 'updated_at')
                ) THEN
                    RAISE EXCEPTION
                        'lab result %: verified results only move to released',
                        OLD.id;
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status = 'released' THEN
                IF NOT (
                    NEW.status = 'superseded'
                    AND NEW.superseded_by_id IS NOT NULL
                    AND (to_jsonb(NEW) - 'status' - 'superseded_by_id'
                            - 'amendment_reason' - 'updated_at') =
                        (to_jsonb(OLD) - 'status' - 'superseded_by_id'
                            - 'amendment_reason' - 'updated_at')
                ) THEN
                    RAISE EXCEPTION
                        'lab result % is released; only supersession allowed',
                        OLD.id;
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'lab result % is % and immutable', OLD.id, OLD.status;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_labresult_immutable
        BEFORE UPDATE OR DELETE ON lab_results
        FOR EACH ROW EXECUTE FUNCTION forbid_labresult_mutation();
    """)
    # released/cancelled orders are frozen header-wise
    op.execute("""
        CREATE OR REPLACE FUNCTION forbid_closed_laborder() RETURNS trigger AS $$
        BEGIN
            IF OLD.status IN ('released', 'cancelled') AND (
                NEW.status <> OLD.status
                OR NEW.patient_id <> OLD.patient_id
                OR NEW.branch_id <> OLD.branch_id
                OR NEW.priority <> OLD.priority
            ) THEN
                RAISE EXCEPTION 'lab order % is % and frozen', OLD.id, OLD.status;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_laborder_frozen
        BEFORE UPDATE ON lab_orders
        FOR EACH ROW EXECUTE FUNCTION forbid_closed_laborder();
    """)

    # ------------------------------------------------------- least privilege
    op.execute("REVOKE INSERT, UPDATE, DELETE ON lab_test_catalog FROM hms_app;")
    op.execute("GRANT SELECT ON lab_test_catalog TO hms_app;")
    op.execute("REVOKE UPDATE, DELETE ON stock_movements FROM hms_app;")
    op.execute("REVOKE UPDATE, DELETE ON dispenses FROM hms_app;")
    op.execute("REVOKE UPDATE, DELETE ON dispense_items FROM hms_app;")


def downgrade() -> None:
    perm_keys = [p for p, _ in M5_PERMS]
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM role_permissions WHERE permission_key IN :keys")
        .bindparams(sa.bindparam("keys", expanding=True)),
        {"keys": perm_keys},
    )
    conn.execute(
        sa.text("DELETE FROM permissions WHERE key IN :keys")
        .bindparams(sa.bindparam("keys", expanding=True)),
        {"keys": perm_keys},
    )
    conn.execute(sa.text("DELETE FROM lab_test_catalog"))
    op.execute("DROP TRIGGER IF EXISTS trg_laborder_frozen ON lab_orders;")
    op.execute("DROP TRIGGER IF EXISTS trg_labresult_immutable ON lab_results;")
    op.execute("DROP TRIGGER IF EXISTS trg_specimen_immutable ON lab_specimens;")
    op.execute("DROP TRIGGER IF EXISTS trg_batch_recall_frozen ON stock_batches;")
    op.execute("DROP TRIGGER IF EXISTS trg_dispense_item_immutable ON dispense_items;")
    op.execute("DROP TRIGGER IF EXISTS trg_dispense_immutable ON dispenses;")
    op.execute("DROP TRIGGER IF EXISTS trg_movement_append_only ON stock_movements;")
    op.execute("DROP TRIGGER IF EXISTS trg_movement_apply ON stock_movements;")
    for fn in ["forbid_closed_laborder", "forbid_labresult_mutation",
               "forbid_specimen_mutation", "forbid_recalled_batch_change",
               "forbid_dispense_mutation", "forbid_movement_mutation",
               "apply_stock_movement"]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}();")
    for table in ["lab_results", "lab_specimens", "lab_orders",
                  "stock_movements", "dispense_items", "dispenses",
                  "stock_batches"]:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
        op.drop_table(table)
    op.drop_table("lab_test_catalog")
