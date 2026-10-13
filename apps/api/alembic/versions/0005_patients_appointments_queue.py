"""M2: patients, appointments, OPD queue, idempotency keys + clinical RBAC.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-13

Security notes
-------------
* All three tables carry ``organization_id`` with strict tenant RLS
  policies (USING + WITH CHECK), exactly like ``branches``.
* UHID is unique per tenant (never globally) and generated under an
  advisory transaction lock so concurrent registrations cannot collide.
* ``patients.merged_into_id`` supports supervised duplicate merge; merged
  records are retained (never hard-deleted) for the audit trail.
* The ``support`` role deliberately receives NO patient permissions and the
  ``auditor`` keeps its M1 read-only org/audit set only — identifiable
  patient data stays restricted to clinical/front-desk roles.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

GUC_ORG = "nullif(current_setting('app.current_org_id', true), '')::uuid"

CLINICAL_PERMS = [
    ("patient.read", "Search and view patient records"),
    ("patient.write", "Register and update patients"),
    ("patient.merge", "Supervised merge of duplicate patient records"),
    ("appointment.read", "View appointments"),
    ("appointment.write", "Create, reschedule and cancel appointments"),
    ("queue.read", "View the OPD queue"),
    ("queue.manage", "Issue tokens and advance the OPD queue"),
]

# (role key -> permissions granted by this migration)
ROLE_GRANTS = {
    "owner": [p for p, _ in CLINICAL_PERMS],
    "admin": [p for p, _ in CLINICAL_PERMS],
    "receptionist": [
        "patient.read", "patient.write",
        "appointment.read", "appointment.write",
        "queue.read", "queue.manage",
    ],
    "doctor": [
        "patient.read", "appointment.read", "queue.read", "queue.manage",
    ],
    "nurse": ["patient.read", "appointment.read", "queue.read"],
    "billing": ["patient.read", "appointment.read"],
    "pharmacist": ["patient.read"],
    "lab_technician": ["patient.read"],
    # auditor: unchanged (org/branch/member/audit read-only, no patient data)
    # support: intentionally NO patient-data permissions
}


def upgrade() -> None:
    # --------------------------------------------------------------- tables
    op.create_table(
        "patients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uhid", sa.String(20), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("gender", sa.String(20), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("dob_estimated", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("address", sa.Text(), nullable=False, server_default=""),
        sa.Column("blood_group", sa.String(5), nullable=True),
        sa.Column("allergies", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("merged_into_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("patients.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "uhid", name="uq_patient_org_uhid"),
        sa.CheckConstraint(
            "gender IN ('male','female','other','unknown')", name="ck_patient_gender"
        ),
        sa.CheckConstraint(
            "blood_group IS NULL OR blood_group IN "
            "('A+','A-','B+','B-','AB+','AB-','O+','O-')",
            name="ck_patient_blood_group",
        ),
        sa.CheckConstraint(
            "status IN ('active','inactive','merged')", name="ck_patient_status"
        ),
    )
    op.create_index("ix_patients_org_created", "patients", ["organization_id", "created_at"])
    op.create_index("ix_patients_org_phone", "patients", ["organization_id", "phone"])
    op.create_index("ix_patients_org_name", "patients",
                    ["organization_id", sa.text("lower(full_name)")])

    op.create_table(
        "appointments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("provider_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("ends_at > starts_at", name="ck_appt_end_after_start"),
        sa.CheckConstraint(
            "status IN ('scheduled','checked_in','completed','cancelled','no_show')",
            name="ck_appt_status",
        ),
    )
    op.create_index("ix_appt_org_starts", "appointments", ["organization_id", "starts_at"])
    op.create_index("ix_appt_org_patient", "appointments", ["organization_id", "patient_id"])
    op.create_index("ix_appt_org_provider_starts", "appointments",
                    ["organization_id", "provider_user_id", "starts_at"])

    op.create_table(
        "opd_queue",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("appointment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("token_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="waiting"),
        sa.Column("priority", sa.String(20), nullable=False, server_default="normal"),
        sa.Column("doctor_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("called_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "service_date", "token_number",
                            name="uq_queue_org_date_token"),
        sa.CheckConstraint(
            "status IN ('waiting','called','in_consultation','completed','skipped','cancelled')",
            name="ck_queue_status",
        ),
        sa.CheckConstraint("priority IN ('normal','urgent')", name="ck_queue_priority"),
        sa.CheckConstraint("token_number > 0", name="ck_queue_token_positive"),
    )
    op.create_index("ix_queue_org_date", "opd_queue", ["organization_id", "service_date"])
    op.create_index("ix_queue_org_status", "opd_queue", ["organization_id", "status"])


    # Idempotency keys: safe retries for POSTs (registration desks and the
    # future offline sync client re-send requests; duplicates must not
    # create duplicate patients/queue entries).
    op.create_table(
        "idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("endpoint", sa.String(120), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "idempotency_key",
                            name="uq_idem_org_key"),
    )
    op.create_index("ix_idem_org_created", "idempotency_keys",
                    ["organization_id", "created_at"])

    # ------------------------------------------------------- row-level security
    for table in ("patients", "appointments", "opd_queue", "idempotency_keys"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"""
            CREATE POLICY {table}_tenant ON {table}
            USING (organization_id = {GUC_ORG})
            WITH CHECK (organization_id = {GUC_ORG});
        """)

    # ------------------------------------------------------ clinical RBAC seed
    perms = sa.table("permissions", sa.column("key", sa.String),
                     sa.column("description", sa.Text))
    op.bulk_insert(perms, [{"key": k, "description": d} for k, d in CLINICAL_PERMS])

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


def downgrade() -> None:
    perm_keys = [p for p, _ in CLINICAL_PERMS]
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM role_permissions WHERE permission_key IN :keys").bindparams(
            sa.bindparam("keys", expanding=True)
        ),
        {"keys": perm_keys},
    )
    conn.execute(
        sa.text("DELETE FROM permissions WHERE key IN :keys").bindparams(
            sa.bindparam("keys", expanding=True)
        ),
        {"keys": perm_keys},
    )
    for table in ("idempotency_keys", "opd_queue", "appointments", "patients"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
        op.drop_table(table)
