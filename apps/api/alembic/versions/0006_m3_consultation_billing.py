"""M3: encounters, vitals, diagnoses, prescriptions, billing + ledger.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-13

Security / integrity notes
--------------------------
* Every tenant table carries ``organization_id`` with strict RLS policies
  (USING + WITH CHECK) — same pattern as ``branches``.
* ``drug_catalog`` is GLOBAL reference data (read-only for ``hms_app``);
  prescription items must reference it ("validated data" per the blueprint).
* A signed prescription is immutable: a trigger blocks content UPDATEs and
  any DELETE once status leaves ``draft``. Amendments insert a NEW version
  (``supersedes_id`` chain) and flip the old row to ``superseded`` — the
  only post-sign UPDATE permitted.
* ``ledger_entries`` is append-only (trigger + revoked UPDATE/DELETE
  grants), mirroring ``audit_events``. Unique partial indexes make double
  posting impossible even under races.
* Invoice totals are frozen once the invoice leaves ``draft``; line items
  may only change while the invoice is a draft.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

GUC_ORG = "nullif(current_setting('app.current_org_id', true), '')::uuid"

M3_PERMS = [
    ("encounter.read", "View consultations, vitals and diagnoses"),
    ("encounter.write", "Record consultations, vitals and diagnoses"),
    ("prescription.read", "View prescriptions (incl. printing)"),
    ("prescription.sign", "Sign and amend prescriptions (practitioners)"),
    ("billing.read", "View invoices, payments and receipts"),
    ("billing.invoice", "Create, issue and void invoices"),
    ("billing.payment", "Record payments and refunds"),
    ("billing.refund", "Refund settled payments"),
]

ROLE_GRANTS = {
    "owner": [p for p, _ in M3_PERMS],
    "admin": [p for p, _ in M3_PERMS],
    "doctor": ["encounter.read", "encounter.write", "prescription.read",
               "prescription.sign", "billing.read"],
    "nurse": ["encounter.read", "encounter.write", "prescription.read"],
    "receptionist": ["prescription.read", "billing.read", "billing.invoice",
                     "billing.payment"],
    "billing": ["billing.read", "billing.invoice", "billing.payment",
                "billing.refund"],
    "pharmacist": ["prescription.read"],
    # lab_technician, auditor, support: no M3 grants (least privilege —
    # support/auditor keep zero clinical and zero financial data access).
}

# (generic, brand hint, drug class, extra allergy terms) — validated seed.
DRUGS = [
    ("Paracetamol 500 mg", "Crocin/Dolo", "analgesic-antipyretic", ""),
    ("Ibuprofen 400 mg", "Brufen", "nsaid", "nsaid,aspirin"),
    ("Diclofenac 50 mg", "Volini", "nsaid", "nsaid,aspirin"),
    ("Aspirin 75 mg", "Disprin", "antiplatelet-nsaid", "aspirin,nsaid"),
    ("Amoxicillin 500 mg", "Mox", "penicillin", "penicillin,beta-lactam"),
    ("Amoxicillin-Clavulanate 625 mg", "Augmentin", "penicillin",
     "penicillin,beta-lactam"),
    ("Cephalexin 500 mg", "Keflex", "cephalosporin",
     "cephalosporin,penicillin,beta-lactam"),
    ("Azithromycin 500 mg", "Zithromax", "macrolide", "macrolide"),
    ("Doxycycline 100 mg", "Doxy", "tetracycline", "tetracycline"),
    ("Ciprofloxacin 500 mg", "Cifran", "fluoroquinolone", "quinolone"),
    ("Metronidazole 400 mg", "Flagyl", "nitroimidazole", ""),
    ("Trimethoprim-Sulfamethoxazole", "Septran", "sulfonamide",
     "sulfa,sulfonamide"),
    ("Fluconazole 150 mg", "Flucon", "antifungal", "azole,antifungal"),
    ("Cetirizine 10 mg", "Cetzine", "antihistamine", ""),
    ("Montelukast 10 mg", "Montair", "leukotriene-antagonist", ""),
    ("Salbutamol inhaler", "Asthalin", "bronchodilator", ""),
    ("Budesonide inhaler", "Budecort", "inhaled-corticosteroid",
     "corticosteroid"),
    ("Prednisolone 5 mg", "Wysolone", "corticosteroid", "corticosteroid"),
    ("Pantoprazole 40 mg", "Pan", "ppi", ""),
    ("Omeprazole 20 mg", "Omez", "ppi", ""),
    ("Ondansetron 4 mg", "Emeset", "antiemetic", ""),
    ("Domperidone 10 mg", "Motilium", "prokinetic", ""),
    ("ORS sachet", "ORSL", "rehydration", ""),
    ("Zinc sulfate 20 mg", "Zincovit", "supplement", ""),
    ("Metformin 500 mg", "Glycomet", "antidiabetic", ""),
    ("Glimepiride 2 mg", "Glimisave", "antidiabetic", ""),
    ("Insulin (regular)", "Human Actrapid", "insulin", "insulin"),
    ("Amlodipine 5 mg", "Amlong", "ccb", ""),
    ("Telmisartan 40 mg", "Telma", "arb", ""),
    ("Enalapril 5 mg", "Envas", "acei", "acei"),
    ("Atorvastatin 10 mg", "Atorva", "statin", "statin"),
    ("Levothyroxine 50 mcg", "Thyronorm", "thyroid", ""),
    ("Ferrous ascorbate + Folic acid", "Fefol", "iron", ""),
    ("Albendazole 400 mg", "Zentel", "anthelmintic", ""),
    ("Ivermectin 12 mg", "Ivermectol", "anthelmintic", ""),
    ("Hydrochlorothiazide 25 mg", "Aquazide", "diuretic", ""),
    ("Furosemide 40 mg", "Lasix", "diuretic", "sulfonamide,sulfa"),
    ("Lidocaine 2% injection", "Lignocaine", "local-anesthetic", ""),
    ("Clotrimazole 1% cream", "Candid", "antifungal", "azole,antifungal"),
    ("Silver sulfadiazine 1% cream", "Silverdin", "sulfonamide-antibacterial",
     "sulfa,sulfonamide"),
    ("Sucralfate 1 g", "Sucral", "antiulcer", ""),
    ("Sodium chloride 0.9% IV", "Normal saline", "iv-fluid", ""),
    ("Ringer lactate IV", "Ringer lactate", "iv-fluid", ""),
    ("Cholecalciferol (Vit D3) 60K", "Calcirol", "vitamin", ""),
    ("Tetanus toxoid injection", "TT vaccine", "vaccine", ""),
]


def upgrade() -> None:
    # --------------------------------------------------------------- tables
    op.create_table(
        "encounters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("branches.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("patients.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("appointment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("appointments.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("queue_entry_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("opd_queue.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("provider_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("chief_complaint", sa.String(500), nullable=False,
                  server_default=""),
        sa.Column("history", sa.Text(), nullable=False, server_default=""),
        sa.Column("examination", sa.Text(), nullable=False, server_default=""),
        sa.Column("assessment", sa.Text(), nullable=False, server_default=""),
        sa.Column("plan", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('open','completed')", name="ck_enc_status"),
    )
    op.create_index("ix_enc_org_patient", "encounters",
                    ["organization_id", "patient_id"])
    op.create_index("ix_enc_org_status", "encounters",
                    ["organization_id", "status"])
    # one open consultation per patient at a time
    op.execute("""
        CREATE UNIQUE INDEX uq_enc_one_open ON encounters
        (organization_id, patient_id) WHERE status = 'open';
    """)

    op.create_table(
        "encounter_vitals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("encounter_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("encounters.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("bp_systolic", sa.Integer(), nullable=True),
        sa.Column("bp_diastolic", sa.Integer(), nullable=True),
        sa.Column("pulse", sa.Integer(), nullable=True),
        sa.Column("temperature_c", sa.Numeric(4, 1), nullable=True),
        sa.Column("spo2", sa.Integer(), nullable=True),
        sa.Column("respiratory_rate", sa.Integer(), nullable=True),
        sa.Column("weight_kg", sa.Numeric(5, 2), nullable=True),
        sa.Column("height_cm", sa.Numeric(5, 1), nullable=True),
        sa.Column("notes", sa.String(500), nullable=False, server_default=""),
        sa.Column("recorded_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "bp_systolic IS NULL OR bp_systolic BETWEEN 40 AND 300",
            name="ck_vitals_sys"),
        sa.CheckConstraint(
            "bp_diastolic IS NULL OR bp_diastolic BETWEEN 20 AND 200",
            name="ck_vitals_dia"),
        sa.CheckConstraint("pulse IS NULL OR pulse BETWEEN 20 AND 250",
                           name="ck_vitals_pulse"),
        sa.CheckConstraint(
            "temperature_c IS NULL OR temperature_c BETWEEN 30 AND 45",
            name="ck_vitals_temp"),
        sa.CheckConstraint("spo2 IS NULL OR spo2 BETWEEN 40 AND 100",
                           name="ck_vitals_spo2"),
        sa.CheckConstraint(
            "respiratory_rate IS NULL OR respiratory_rate BETWEEN 5 AND 80",
            name="ck_vitals_rr"),
        sa.CheckConstraint("weight_kg IS NULL OR weight_kg BETWEEN 0.5 AND 400",
                           name="ck_vitals_weight"),
        sa.CheckConstraint(
            "height_cm IS NULL OR height_cm BETWEEN 30 AND 260",
            name="ck_vitals_height"),
        sa.CheckConstraint(
            "bp_systolic IS NULL OR bp_diastolic IS NULL "
            "OR bp_systolic > bp_diastolic", name="ck_vitals_bp_pair"),
    )
    op.create_index("ix_vitals_org_enc", "encounter_vitals",
                    ["organization_id", "encounter_id"])

    op.create_table(
        "encounter_diagnoses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("encounter_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("encounters.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="primary"),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="confirmed"),
        sa.Column("recorded_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("kind IN ('primary','secondary')",
                           name="ck_dx_kind"),
        sa.CheckConstraint(
            "status IN ('suspected','confirmed','ruled_out')", name="ck_dx_status"),
    )
    op.create_index("ix_dx_org_enc", "encounter_diagnoses",
                    ["organization_id", "encounter_id"])

    op.create_table(
        "drug_catalog",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("generic_name", sa.String(200), nullable=False, unique=True),
        sa.Column("brand_hint", sa.String(200), nullable=False, server_default=""),
        sa.Column("drug_class", sa.String(100), nullable=False, server_default=""),
        sa.Column("allergy_terms", sa.String(300), nullable=False,
                  server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
    )

    op.create_table(
        "prescriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("encounter_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("encounters.id", ondelete="RESTRICT"),
                  nullable=True),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("patients.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("prescriber_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("prescriptions.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("diagnosis_text", sa.String(500), nullable=False,
                  server_default=""),
        sa.Column("advice", sa.Text(), nullable=False, server_default=""),
        sa.Column("follow_up", sa.String(300), nullable=False,
                  server_default=""),
        sa.Column("allergy_acknowledged", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("amendment_reason", sa.String(500), nullable=False,
                  server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('draft','signed','superseded')",
                           name="ck_rx_status"),
        sa.CheckConstraint("version >= 1", name="ck_rx_version"),
    )
    op.create_index("ix_rx_org_patient", "prescriptions",
                    ["organization_id", "patient_id"])
    op.create_index("ix_rx_org_status", "prescriptions",
                    ["organization_id", "status"])

    op.create_table(
        "prescription_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("prescription_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("prescriptions.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("drug_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("drug_catalog.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("dose", sa.String(100), nullable=False),
        sa.Column("route", sa.String(40), nullable=False, server_default="oral"),
        sa.Column("frequency", sa.String(100), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.String(100), nullable=False,
                  server_default=""),
        sa.Column("instructions", sa.String(500), nullable=False,
                  server_default=""),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("duration_days BETWEEN 1 AND 365",
                           name="ck_rxitem_duration"),
        sa.CheckConstraint("seq >= 0", name="ck_rxitem_seq"),
    )
    op.create_index("ix_rxitem_org_rx", "prescription_items",
                    ["organization_id", "prescription_id"])

    op.create_table(
        "invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("branches.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("patients.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("encounter_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("encounters.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("invoice_number", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="draft"),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False,
                  server_default="0"),
        sa.Column("discount", sa.Numeric(12, 2), nullable=False,
                  server_default="0"),
        sa.Column("tax_total", sa.Numeric(12, 2), nullable=False,
                  server_default="0"),
        sa.Column("total", sa.Numeric(12, 2), nullable=False,
                  server_default="0"),
        sa.Column("amount_paid", sa.Numeric(12, 2), nullable=False,
                  server_default="0"),
        sa.Column("notes", sa.String(500), nullable=False, server_default=""),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "invoice_number",
                            name="uq_invoice_org_number"),
        sa.CheckConstraint("status IN ('draft','issued','paid','void')",
                           name="ck_inv_status"),
        sa.CheckConstraint("subtotal >= 0 AND discount >= 0 AND tax_total >= 0 "
                           "AND total >= 0 AND amount_paid >= 0",
                           name="ck_inv_amounts_nonneg"),
        sa.CheckConstraint("discount <= subtotal", name="ck_inv_discount_bound"),
    )
    op.create_index("ix_inv_org_patient", "invoices",
                    ["organization_id", "patient_id"])
    op.create_index("ix_inv_org_status", "invoices",
                    ["organization_id", "status"])

    op.create_table(
        "invoice_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("invoices.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("item_type", sa.String(30), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("tax_rate", sa.Numeric(5, 2), nullable=False,
                  server_default="0"),
        sa.Column("line_total", sa.Numeric(12, 2), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "item_type IN ('consultation','procedure','pharmacy','lab','other')",
            name="ck_invitem_type"),
        sa.CheckConstraint("quantity > 0 AND unit_price >= 0 AND tax_rate >= 0 "
                           "AND line_total >= 0", name="ck_invitem_amounts"),
    )
    op.create_index("ix_invitem_org_inv", "invoice_items",
                    ["organization_id", "invoice_id"])

    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("invoices.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("gateway", sa.String(20), nullable=False,
                  server_default="manual"),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="pending"),
        sa.Column("gateway_ref", sa.String(120), nullable=False,
                  server_default=""),
        sa.Column("failure_reason", sa.String(300), nullable=False,
                  server_default=""),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("amount > 0", name="ck_pay_amount_positive"),
        sa.CheckConstraint(
            "method IN ('cash','upi','card','insurance','other')",
            name="ck_pay_method"),
        sa.CheckConstraint("gateway IN ('manual','sandbox')",
                           name="ck_pay_gateway"),
        sa.CheckConstraint(
            "status IN ('pending','succeeded','failed','refunded',"
            "'partially_refunded')", name="ck_pay_status"),
    )
    op.create_index("ix_pay_org_invoice", "payments",
                    ["organization_id", "invoice_id"])

    op.create_table(
        "refunds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("payments.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="succeeded"),
        sa.Column("gateway_ref", sa.String(120), nullable=False,
                  server_default=""),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("amount > 0", name="ck_ref_amount_positive"),
        sa.CheckConstraint("status IN ('succeeded','failed')",
                           name="ck_ref_status"),
    )
    op.create_index("ix_ref_org_payment", "refunds",
                    ["organization_id", "payment_id"])

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("invoices.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("payments.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("refund_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("refunds.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("entry_type", sa.String(30), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "entry_type IN ('charge','payment','refund','adjustment')",
            name="ck_ledger_type"),
        sa.CheckConstraint("direction IN ('debit','credit')",
                           name="ck_ledger_direction"),
        sa.CheckConstraint("amount > 0", name="ck_ledger_amount_positive"),
    )
    op.create_index("ix_ledger_org_invoice", "ledger_entries",
                    ["organization_id", "invoice_id", "id"])
    # double-posting backstops (even if an app-level guard raced)
    op.execute("""
        CREATE UNIQUE INDEX uq_ledger_charge_per_invoice
        ON ledger_entries (invoice_id) WHERE entry_type = 'charge';
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_ledger_payment_once
        ON ledger_entries (payment_id)
        WHERE payment_id IS NOT NULL AND entry_type = 'payment';
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_ledger_refund_once
        ON ledger_entries (refund_id)
        WHERE refund_id IS NOT NULL AND entry_type = 'refund';
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_ledger_void_per_invoice
        ON ledger_entries (invoice_id)
        WHERE entry_type = 'adjustment';
    """)

    # ---------------------------------------------------- reference data
    drugs = sa.table(
        "drug_catalog",
        sa.column("generic_name", sa.String),
        sa.column("brand_hint", sa.String),
        sa.column("drug_class", sa.String),
        sa.column("allergy_terms", sa.String),
    )
    op.bulk_insert(
        drugs,
        [
            {"generic_name": g, "brand_hint": b, "drug_class": c,
             "allergy_terms": t}
            for g, b, c, t in DRUGS
        ],
    )

    # ------------------------------------------------------- RBAC seed
    perms = sa.table("permissions", sa.column("key", sa.String),
                     sa.column("description", sa.Text))
    op.bulk_insert(perms, [{"key": k, "description": d} for k, d in M3_PERMS])

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
        "encounters", "encounter_vitals", "encounter_diagnoses",
        "prescriptions", "prescription_items", "invoices", "invoice_items",
        "payments", "refunds", "ledger_entries",
    ]
    for table in tenant_tables:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"""
            CREATE POLICY {table}_tenant ON {table}
            USING (organization_id = {GUC_ORG})
            WITH CHECK (organization_id = {GUC_ORG});
        """)

    # ------------------------------------------- immutability guarantees
    # Signed prescriptions: content frozen; only signed -> superseded allowed.
    op.execute("""
        CREATE OR REPLACE FUNCTION forbid_rx_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'draft' THEN
                    RAISE EXCEPTION 'prescription % is immutable (blocked DELETE)',
                        OLD.id;
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.status <> 'draft' THEN
                -- the ONLY legal post-sign change is signed -> superseded
                -- with every other column byte-identical
                IF NOT (
                    OLD.status = 'signed'
                    AND NEW.status = 'superseded'
                    AND (to_jsonb(NEW) - 'status' - 'updated_at')
                        = (to_jsonb(OLD) - 'status' - 'updated_at')
                ) THEN
                    RAISE EXCEPTION 'prescription % is signed and immutable',
                        OLD.id;
                END IF;
                RETURN NEW;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_rx_immutable
        BEFORE UPDATE OR DELETE ON prescriptions
        FOR EACH ROW EXECUTE FUNCTION forbid_rx_mutation();
    """)
    # items follow their prescription's status
    op.execute("""
        CREATE OR REPLACE FUNCTION forbid_rxitem_mutation() RETURNS trigger AS $$
        DECLARE
            v_status text;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                SELECT status INTO v_status FROM prescriptions
                WHERE id = NEW.prescription_id;
            ELSE
                SELECT status INTO v_status FROM prescriptions
                WHERE id = OLD.prescription_id;
            END IF;
            IF v_status IS NOT NULL AND v_status <> 'draft' THEN
                RAISE EXCEPTION 'prescription items are immutable once %',
                    v_status;
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_rxitem_immutable
        BEFORE UPDATE OR DELETE ON prescription_items
        FOR EACH ROW EXECUTE FUNCTION forbid_rxitem_mutation();
    """)
    # closed encounters are frozen
    op.execute("""
        CREATE OR REPLACE FUNCTION forbid_closed_encounter() RETURNS trigger AS $$
        BEGIN
            IF OLD.status = 'completed' THEN
                RAISE EXCEPTION 'encounter % is completed and immutable',
                    OLD.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_enc_completed_immutable
        BEFORE UPDATE ON encounters
        FOR EACH ROW EXECUTE FUNCTION forbid_closed_encounter();
    """)
    # invoice financials frozen after issue
    op.execute("""
        CREATE OR REPLACE FUNCTION forbid_issued_invoice_change() RETURNS trigger AS $$
        BEGIN
            IF OLD.status <> 'draft' AND (
                NEW.invoice_number <> OLD.invoice_number
                OR NEW.subtotal <> OLD.subtotal
                OR NEW.discount <> OLD.discount
                OR NEW.tax_total <> OLD.tax_total
                OR NEW.total <> OLD.total
                OR NEW.branch_id <> OLD.branch_id
                OR NEW.patient_id <> OLD.patient_id
            ) THEN
                RAISE EXCEPTION 'invoice % is issued; financials are frozen',
                    OLD.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_inv_issued_frozen
        BEFORE UPDATE ON invoices
        FOR EACH ROW EXECUTE FUNCTION forbid_issued_invoice_change();
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION forbid_issued_item_change() RETURNS trigger AS $$
        DECLARE
            v_status text;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                SELECT status INTO v_status FROM invoices
                WHERE id = NEW.invoice_id;
            ELSE
                SELECT status INTO v_status FROM invoices
                WHERE id = OLD.invoice_id;
            END IF;
            IF v_status IS NOT NULL AND v_status <> 'draft' THEN
                RAISE EXCEPTION 'invoice items are immutable once invoice is %',
                    v_status;
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_invitem_frozen
        BEFORE INSERT OR UPDATE OR DELETE ON invoice_items
        FOR EACH ROW EXECUTE FUNCTION forbid_issued_item_change();
    """)
    # ledger is append-only
    op.execute("""
        CREATE OR REPLACE FUNCTION forbid_ledger_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'ledger_entries is append-only (blocked %)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_ledger_append_only
        BEFORE UPDATE OR DELETE ON ledger_entries
        FOR EACH ROW EXECUTE FUNCTION forbid_ledger_mutation();
    """)

    # ------------------------------------------------------- least privilege
    op.execute("REVOKE UPDATE, DELETE ON ledger_entries FROM hms_app;")
    op.execute("REVOKE INSERT, UPDATE, DELETE ON drug_catalog FROM hms_app;")
    op.execute("GRANT SELECT ON drug_catalog TO hms_app;")


def downgrade() -> None:
    perm_keys = [p for p, _ in M3_PERMS]
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
    conn.execute(sa.text("DELETE FROM drug_catalog"))
    op.execute("DROP TRIGGER IF EXISTS trg_ledger_append_only ON ledger_entries;")
    op.execute("DROP TRIGGER IF EXISTS trg_invitem_frozen ON invoice_items;")
    op.execute("DROP TRIGGER IF EXISTS trg_inv_issued_frozen ON invoices;")
    op.execute("DROP TRIGGER IF EXISTS trg_enc_completed_immutable ON encounters;")
    op.execute("DROP TRIGGER IF EXISTS trg_rxitem_immutable ON prescription_items;")
    op.execute("DROP TRIGGER IF EXISTS trg_rx_immutable ON prescriptions;")
    for fn in ["forbid_ledger_mutation", "forbid_issued_item_change",
               "forbid_issued_invoice_change", "forbid_closed_encounter",
               "forbid_rxitem_mutation", "forbid_rx_mutation"]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}();")
    for table in ["ledger_entries", "refunds", "payments", "invoice_items",
                  "invoices", "prescription_items", "prescriptions",
                  "encounter_diagnoses", "encounter_vitals", "encounters"]:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
        op.drop_table(table)
    op.drop_table("drug_catalog")
