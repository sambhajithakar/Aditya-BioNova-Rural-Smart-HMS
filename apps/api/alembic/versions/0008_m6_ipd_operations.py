"""M6: IPD (wards, beds, admissions, nursing, discharge) + operations
(inventory, expenses) and the report backing tables.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-17

Security / integrity notes
--------------------------
* Every tenant table carries ``organization_id`` with strict RLS policies
  (USING + WITH CHECK) — same pattern as M1–M5 tables.
* Bed occupancy is DERIVED: a partial unique index guarantees at most one
  ``admitted`` row per bed and per patient, so a double-booking is refused
  even by raw SQL; ``beds.status`` only describes physical availability
  (available / maintenance / blocked).
* Admissions follow admitted -> discharged as their ONLY transition; a
  discharged admission is completely frozen (column-level trigger), bed
  transfers are recorded in an append-only ledger, and admissions can never
  be deleted — the stay history is part of the patient record.
* Nursing records are append-only clinical evidence: notes are immutable
  once written and can only be added while the admission is open (DB
  trigger backstop), with the same range checks as OPD vitals.
* ``inventory_movements`` is an append-only ledger mirroring the pharmacy
  stock pattern: on-hand is maintained by trigger (SUM(in) − SUM(out)),
  negative stock and movement on retired items are impossible even via
  raw SQL.
* Expenses are financial records: amounts/dates are frozen, the only legal
  change is recorded -> void (with a reason), and DELETE is blocked.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

GUC_ORG = "nullif(current_setting('app.current_org_id', true), '')::uuid"

M6_PERMS = [
    ("bed.read", "View wards, beds, admissions and the IPD bed board"),
    ("bed.manage", "Create and manage wards and beds (maintenance, closure)"),
    ("admission.manage", "Admit patients to beds and transfer them between beds"),
    ("discharge", "Discharge patients from inpatient care"),
    ("nursing.read", "Read nursing records of an admission"),
    ("nursing.write", "Write nursing notes and inpatient vitals"),
    ("inventory.read", "View inventory items, levels and movements"),
    ("inventory.manage", "Receive, issue and adjust inventory stock"),
    ("expense.read", "View expenses and expense reports"),
    ("expense.manage", "Record and void expenses"),
    ("report.read", "View operational and financial reports"),
]

ROLE_GRANTS = {
    "owner": [p for p, _ in M6_PERMS],
    "admin": [p for p, _ in M6_PERMS],
    "doctor": ["bed.read", "admission.manage", "discharge", "nursing.read",
               "nursing.write", "inventory.read", "expense.read",
               "report.read"],
    "nurse": ["bed.read", "admission.manage", "nursing.read",
              "nursing.write", "inventory.read", "report.read"],
    "receptionist": ["bed.read", "admission.manage"],
    "pharmacist": ["bed.read", "inventory.read"],
    "lab_technician": ["bed.read"],
    "billing": ["bed.read", "expense.read", "expense.manage", "report.read"],
    # auditor, support: no M6 grants (least privilege — consistent with M3/M5:
    # auditor/support hold zero clinical and zero financial data access).
}

TENANT_TABLES = [
    "wards", "beds", "admissions", "admission_transfers", "nursing_records",
    "inventory_items", "inventory_movements", "expenses",
]


def upgrade() -> None:
    # --------------------------------------------------------------- tables
    op.create_table(
        "wards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("branches.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("ward_type", sa.String(30), nullable=False,
                  server_default="general"),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "branch_id", "code",
                            name="uq_ward_org_branch_code"),
        sa.CheckConstraint(
            "ward_type IN ('general','icu','maternity','pediatric',"
            "'isolation','day_care','private','other')",
            name="ck_ward_type"),
        sa.CheckConstraint("status IN ('active','closed')",
                           name="ck_ward_status"),
    )
    op.create_index("ix_wards_org_branch", "wards",
                    ["organization_id", "branch_id"])

    op.create_table(
        "beds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("ward_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("wards.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("label", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="available"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "ward_id", "label",
                            name="uq_bed_org_ward_label"),
        # physical availability only — OCCUPANCY is derived from admissions
        sa.CheckConstraint(
            "status IN ('available','maintenance','blocked')",
            name="ck_bed_status"),
    )
    op.create_index("ix_beds_org_ward", "beds", ["organization_id", "ward_id"])
    op.create_index("ix_beds_org_status", "beds",
                    ["organization_id", "status"])

    op.create_table(
        "admissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("patients.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("branches.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("ward_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("wards.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("bed_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("beds.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("attending_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="admitted"),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("admitted_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("discharge_outcome", sa.String(30), nullable=True),
        sa.Column("discharge_summary", sa.Text(), nullable=False,
                  server_default=""),
        sa.Column("discharged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discharged_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('admitted','discharged')",
                           name="ck_admission_status"),
        sa.CheckConstraint(
            "discharge_outcome IS NULL OR discharge_outcome IN "
            "('recovered','improved','referred','left_against_advice',"
            "'death','transfer_out')",
            name="ck_admission_outcome"),
        sa.CheckConstraint(
            "(status = 'admitted') = (discharged_at IS NULL)",
            name="ck_admission_discharge_consistency"),
    )
    op.create_index("ix_admissions_org_status", "admissions",
                    ["organization_id", "status"])
    op.create_index("ix_admissions_org_patient", "admissions",
                    ["organization_id", "patient_id"])
    op.create_index("ix_admissions_org_bed", "admissions",
                    ["organization_id", "bed_id"])
    # occupancy backstop: at most ONE active admission per bed and per
    # patient, enforced by the database itself (raw-SQL proof in tests)
    op.execute("""
        CREATE UNIQUE INDEX uq_admission_bed_occupied
        ON admissions (bed_id) WHERE status = 'admitted';
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_admission_patient_active
        ON admissions (patient_id) WHERE status = 'admitted';
    """)

    op.create_table(
        "admission_transfers",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("admission_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("admissions.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("from_bed_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("beds.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("to_bed_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("beds.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("reason", sa.String(300), nullable=False,
                  server_default=""),
        sa.Column("transferred_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("from_bed_id <> to_bed_id",
                           name="ck_transfer_distinct_beds"),
    )
    op.create_index("ix_transfer_org_admission", "admission_transfers",
                    ["organization_id", "admission_id", "id"])

    op.create_table(
        "nursing_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("admission_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("admissions.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("note_type", sa.String(20), nullable=False,
                  server_default="note"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("bp_systolic", sa.Integer(), nullable=True),
        sa.Column("bp_diastolic", sa.Integer(), nullable=True),
        sa.Column("pulse", sa.Integer(), nullable=True),
        sa.Column("respiratory_rate", sa.Integer(), nullable=True),
        sa.Column("temperature_c", sa.Numeric(4, 1), nullable=True),
        sa.Column("spo2", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("recorded_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "note_type IN ('vitals','note','intake_output','care')",
            name="ck_nursing_note_type"),
        sa.CheckConstraint(
            "note <> '' OR bp_systolic IS NOT NULL OR pulse IS NOT NULL "
            "OR temperature_c IS NOT NULL OR spo2 IS NOT NULL",
            name="ck_nursing_has_content"),
        sa.CheckConstraint(
            "bp_systolic IS NULL OR (bp_systolic BETWEEN 40 AND 300)",
            name="ck_nursing_systolic"),
        sa.CheckConstraint(
            "bp_diastolic IS NULL OR (bp_diastolic BETWEEN 20 AND 200)",
            name="ck_nursing_diastolic"),
        sa.CheckConstraint(
            "bp_systolic IS NULL OR bp_diastolic IS NULL "
            "OR bp_systolic > bp_diastolic",
            name="ck_nursing_bp_order"),
        sa.CheckConstraint("pulse IS NULL OR (pulse BETWEEN 20 AND 250)",
                           name="ck_nursing_pulse"),
        sa.CheckConstraint(
            "respiratory_rate IS NULL OR (respiratory_rate BETWEEN 5 AND 80)",
            name="ck_nursing_rr"),
        sa.CheckConstraint(
            "temperature_c IS NULL OR (temperature_c BETWEEN 30 AND 45)",
            name="ck_nursing_temp"),
        sa.CheckConstraint("spo2 IS NULL OR (spo2 BETWEEN 50 AND 100)",
                           name="ck_nursing_spo2"),
    )
    op.create_index("ix_nursing_org_admission", "nursing_records",
                    ["organization_id", "admission_id", "recorded_at"])

    op.create_table(
        "inventory_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("branches.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("sku", sa.String(60), nullable=False, server_default=""),
        sa.Column("unit", sa.String(30), nullable=False,
                  server_default="pcs"),
        sa.Column("quantity_on_hand", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("reorder_level", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False,
                  server_default="0"),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "branch_id", "name",
                            name="uq_invitem_org_branch_name"),
        sa.CheckConstraint("quantity_on_hand >= 0",
                           name="ck_invitem_on_hand_nonneg"),
        sa.CheckConstraint("reorder_level >= 0",
                           name="ck_invitem_reorder_nonneg"),
        sa.CheckConstraint("unit_cost >= 0", name="ck_invitem_cost_nonneg"),
        sa.CheckConstraint("status IN ('active','retired')",
                           name="ck_invitem_status"),
    )
    op.create_index("ix_invitem_org_branch", "inventory_items",
                    ["organization_id", "branch_id"])

    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("inventory_items.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("movement_type", sa.String(20), nullable=False),
        sa.Column("direction", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False,
                  server_default=""),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "movement_type IN ('receive','issue','adjustment','return')",
            name="ck_invmovement_type"),
        sa.CheckConstraint("direction IN ('in','out')",
                           name="ck_invmovement_direction"),
        sa.CheckConstraint("quantity > 0", name="ck_invmovement_qty_positive"),
    )
    op.create_index("ix_invmovement_org_item", "inventory_movements",
                    ["organization_id", "item_id", "id"])

    op.create_table(
        "expenses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("branches.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("description", sa.String(500), nullable=False,
                  server_default=""),
        sa.Column("reference", sa.String(100), nullable=False,
                  server_default=""),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="recorded"),
        sa.Column("void_reason", sa.String(300), nullable=False,
                  server_default=""),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("amount > 0", name="ck_expense_amount_positive"),
        sa.CheckConstraint(
            "category IN ('rent','salaries','utilities','supplies',"
            "'equipment','maintenance','service','transport','marketing',"
            "'other')",
            name="ck_expense_category"),
        sa.CheckConstraint("status IN ('recorded','void')",
                           name="ck_expense_status"),
    )
    op.create_index("ix_expense_org_date", "expenses",
                    ["organization_id", "expense_date"])
    op.create_index("ix_expense_org_category", "expenses",
                    ["organization_id", "category"])

    # ------------------------------------------------------- RBAC seed
    perms = sa.table("permissions", sa.column("key", sa.String),
                     sa.column("description", sa.Text))
    op.bulk_insert(perms, [{"key": k, "description": d}
                           for k, d in M6_PERMS])

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
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"""
            CREATE POLICY {table}_tenant ON {table}
            USING (organization_id = {GUC_ORG})
            WITH CHECK (organization_id = {GUC_ORG});
        """)

    # ------------------------------------------- immutability guarantees
    # Admissions: permanent record; ONLY admitted -> discharged, header
    # frozen, bed/ward move only as an internally-consistent transfer.
    op.execute("""
        CREATE OR REPLACE FUNCTION forbid_admission_mutation()
        RETURNS trigger AS $$
        DECLARE
            v_ward uuid;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION
                    'admissions are permanent (discharge instead of DELETE)';
            END IF;
            IF NEW.patient_id <> OLD.patient_id
               OR NEW.branch_id <> OLD.branch_id
               OR NEW.admitted_at <> OLD.admitted_at THEN
                RAISE EXCEPTION 'admission % header is frozen', OLD.id;
            END IF;
            IF OLD.status = 'discharged' THEN
                IF (to_jsonb(NEW) - 'updated_at')
                   <> (to_jsonb(OLD) - 'updated_at') THEN
                    RAISE EXCEPTION 'admission % is discharged and frozen',
                        OLD.id;
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.status = 'admitted' AND OLD.status = 'admitted' THEN
                -- legal change: a transfer may move bed (+ its ward)
                IF NEW.discharged_at IS NOT NULL
                   OR NEW.discharge_outcome IS NOT NULL THEN
                    RAISE EXCEPTION 'admission % still admitted', OLD.id;
                END IF;
                IF NEW.bed_id IS DISTINCT FROM OLD.bed_id THEN
                    SELECT ward_id INTO v_ward
                      FROM beds
                     WHERE id = NEW.bed_id
                       AND organization_id = NEW.organization_id;
                    IF NOT FOUND THEN
                        RAISE EXCEPTION 'target bed % not in tenant',
                            NEW.bed_id;
                    END IF;
                    IF NEW.ward_id <> v_ward THEN
                        RAISE EXCEPTION
                            'transfer must set bed and its own ward';
                    END IF;
                ELSIF NEW.ward_id <> OLD.ward_id THEN
                    RAISE EXCEPTION 'admission ward cannot change alone';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.status = 'discharged' AND OLD.status = 'admitted' THEN
                IF NEW.discharged_at IS NULL
                   OR NEW.discharge_outcome IS NULL THEN
                    RAISE EXCEPTION
                        'discharge needs an outcome and discharged_at';
                END IF;
                IF (to_jsonb(NEW) - 'status' - 'discharge_outcome'
                        - 'discharge_summary' - 'discharged_at'
                        - 'discharged_by' - 'updated_at')
                   <> (to_jsonb(OLD) - 'status' - 'discharge_outcome'
                        - 'discharge_summary' - 'discharged_at'
                        - 'discharged_by' - 'updated_at') THEN
                    RAISE EXCEPTION
                        'discharge may only set the discharge columns '
                        '(admission %)', OLD.id;
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'admission %: illegal transition % -> %',
                OLD.id, OLD.status, NEW.status;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_admission_immutable
        BEFORE UPDATE OR DELETE ON admissions
        FOR EACH ROW EXECUTE FUNCTION forbid_admission_mutation();
    """)

    # Transfers and nursing records are append-only documents
    op.execute("""
        CREATE OR REPLACE FUNCTION forbid_append_only_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only (blocked %)',
                TG_TABLE_NAME, TG_OP;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_transfer_append_only
        BEFORE UPDATE OR DELETE ON admission_transfers
        FOR EACH ROW EXECUTE FUNCTION forbid_append_only_mutation();
    """)
    op.execute("""
        CREATE TRIGGER trg_nursing_append_only
        BEFORE UPDATE OR DELETE ON nursing_records
        FOR EACH ROW EXECUTE FUNCTION forbid_append_only_mutation();
    """)
    # nursing entries only while the stay is open + tenancy consistency
    op.execute("""
        CREATE OR REPLACE FUNCTION check_nursing_admission()
        RETURNS trigger AS $$
        DECLARE
            v_org uuid; v_status text;
        BEGIN
            SELECT organization_id, status INTO v_org, v_status
              FROM admissions WHERE id = NEW.admission_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'admission % not found', NEW.admission_id;
            END IF;
            IF v_org <> NEW.organization_id THEN
                RAISE EXCEPTION 'nursing record/admission tenancy mismatch';
            END IF;
            IF v_status <> 'admitted' THEN
                RAISE EXCEPTION
                    'admission % is %; no nursing entries after discharge',
                    NEW.admission_id, v_status;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_nursing_check_admission
        BEFORE INSERT ON nursing_records
        FOR EACH ROW EXECUTE FUNCTION check_nursing_admission();
    """)

    # Inventory: trigger-maintained on-hand, append-only ledger
    op.execute("""
        CREATE OR REPLACE FUNCTION apply_inventory_movement()
        RETURNS trigger AS $$
        DECLARE
            v_org uuid; v_status text; v_onhand integer;
        BEGIN
            SELECT organization_id, status, quantity_on_hand
              INTO v_org, v_status, v_onhand
              FROM inventory_items WHERE id = NEW.item_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'inventory item % not found', NEW.item_id;
            END IF;
            IF v_org <> NEW.organization_id THEN
                RAISE EXCEPTION 'movement/item tenancy mismatch';
            END IF;
            IF v_status <> 'active' THEN
                RAISE EXCEPTION 'item % is %; no movements allowed',
                    NEW.item_id, v_status;
            END IF;
            IF NEW.direction = 'in' THEN
                UPDATE inventory_items
                   SET quantity_on_hand = quantity_on_hand + NEW.quantity,
                       updated_at = now()
                 WHERE id = NEW.item_id;
            ELSE
                IF v_onhand < NEW.quantity THEN
                    RAISE EXCEPTION
                        'insufficient stock for item %: % on hand, % requested',
                        NEW.item_id, v_onhand, NEW.quantity;
                END IF;
                UPDATE inventory_items
                   SET quantity_on_hand = quantity_on_hand - NEW.quantity,
                       updated_at = now()
                 WHERE id = NEW.item_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_inventory_movement_apply
        BEFORE INSERT ON inventory_movements
        FOR EACH ROW EXECUTE FUNCTION apply_inventory_movement();
    """)
    op.execute("""
        CREATE TRIGGER trg_inventory_movement_append_only
        BEFORE UPDATE OR DELETE ON inventory_movements
        FOR EACH ROW EXECUTE FUNCTION forbid_append_only_mutation();
    """)

    # Expenses: permanent financial records, only recorded -> void is legal
    op.execute("""
        CREATE OR REPLACE FUNCTION forbid_expense_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'expenses are permanent (void instead of '
                    'DELETE)';
            END IF;
            IF OLD.status = 'void' THEN
                RAISE EXCEPTION 'expense % is void and frozen', OLD.id;
            END IF;
            IF NEW.status = 'recorded' THEN
                IF (to_jsonb(NEW) - 'updated_at')
                   <> (to_jsonb(OLD) - 'updated_at') THEN
                    RAISE EXCEPTION 'expense % content is frozen', OLD.id;
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.status = 'void'
               AND btrim(NEW.void_reason) <> ''
               AND (to_jsonb(NEW) - 'status' - 'void_reason' - 'updated_at')
                   = (to_jsonb(OLD) - 'status' - 'void_reason'
                        - 'updated_at') THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'expense %: only recorded -> void is allowed',
                OLD.id;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_expense_immutable
        BEFORE UPDATE OR DELETE ON expenses
        FOR EACH ROW EXECUTE FUNCTION forbid_expense_mutation();
    """)

    # ------------------------------------------------------- least privilege
    op.execute("REVOKE UPDATE, DELETE ON inventory_movements FROM hms_app;")
    op.execute("REVOKE UPDATE, DELETE ON admission_transfers FROM hms_app;")
    op.execute("REVOKE UPDATE, DELETE ON nursing_records FROM hms_app;")


def downgrade() -> None:
    perm_keys = [p for p, _ in M6_PERMS]
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
    for trig, table in [
        ("trg_expense_immutable", "expenses"),
        ("trg_inventory_movement_append_only", "inventory_movements"),
        ("trg_inventory_movement_apply", "inventory_movements"),
        ("trg_nursing_check_admission", "nursing_records"),
        ("trg_nursing_append_only", "nursing_records"),
        ("trg_transfer_append_only", "admission_transfers"),
        ("trg_admission_immutable", "admissions"),
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS {trig} ON {table};")
    for fn in ["forbid_expense_mutation", "apply_inventory_movement",
               "check_nursing_admission", "forbid_append_only_mutation",
               "forbid_admission_mutation"]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}();")
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)