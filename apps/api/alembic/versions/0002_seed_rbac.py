"""Seed the permission catalogue and system roles.

Revision ID: 0002
Revises: 0001

Permissions grow feature-by-feature; roles map to them. The `owner` role is
granted only during organization registration — APIs refuse to grant it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

PERMISSIONS = [
    ("org.read", "Read organization profile and settings"),
    ("org.manage", "Modify organization settings"),
    ("org.transfer_ownership", "Transfer organization ownership (reserved until M2)"),
    ("branch.read", "List and view branches"),
    ("branch.manage", "Create and manage branches"),
    ("member.read", "List organization members"),
    ("member.manage", "Invite, create and modify member accounts"),
    ("audit.read", "Read the audit trail"),
]

ADMIN_PERMS = [
    "org.read", "org.manage", "branch.read", "branch.manage",
    "member.read", "member.manage", "audit.read",
]
CLINICAL_VIEW_PERMS = ["org.read", "branch.read", "member.read"]

_OWNER_PERMS = ADMIN_PERMS + ["org.transfer_ownership"]
_SUPPORT_PERMS = ["org.read"]

ROLES = [
    ("owner", "Clinic Owner",
     "Full control including ownership transfer", _OWNER_PERMS),
    ("admin", "Administrator",
     "Manages branches, members and organization settings", ADMIN_PERMS),
    ("doctor", "Doctor",
     "Clinical staff (M2+: consultations and prescriptions)", CLINICAL_VIEW_PERMS),
    ("nurse", "Nurse",
     "Nursing staff (M2+: vitals and nursing notes)", CLINICAL_VIEW_PERMS),
    ("receptionist", "Receptionist",
     "Front desk (M2+: registration, appointments, queue)", CLINICAL_VIEW_PERMS),
    ("pharmacist", "Pharmacist",
     "Pharmacy staff (M2+: stock and dispensing)", CLINICAL_VIEW_PERMS),
    ("lab_technician", "Laboratory Technician",
     "Lab staff (M2+: orders and results)", CLINICAL_VIEW_PERMS),
    ("billing", "Billing Staff",
     "Cashier (M2+: invoices and receipts)", CLINICAL_VIEW_PERMS),
    ("auditor", "Auditor",
     "Read-only plus audit trail access", ADMIN_PERMS),
    ("support", "Support (no patient data)",
     "Vendor support without access to identifiable data", _SUPPORT_PERMS),
]


def upgrade() -> None:
    permissions = sa.table(
        "permissions",
        sa.column("key", sa.String),
        sa.column("description", sa.Text),
    )
    roles = sa.table(
        "roles",
        sa.column("key", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.String),
        sa.column("permission_key", sa.String),
    )

    op.bulk_insert(permissions, [{"key": k, "description": d} for k, d in PERMISSIONS])
    op.bulk_insert(
        roles,
        [{"key": k, "name": n, "description": d} for k, n, d, _ in ROLES],
    )

    # Map each role's permissions (resolve role ids by key)
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, key FROM roles")).fetchall()
    role_ids = {key: str(rid) for rid, key in rows}
    link_rows = []
    for key, _, _, perms in ROLES:
        for perm in perms:
            link_rows.append({"role_id": role_ids[key], "permission_key": perm})
    op.bulk_insert(role_permissions, link_rows)


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions;")
    op.execute("DELETE FROM roles;")
    op.execute("DELETE FROM permissions;")
