"""Restrict the auditor role to true read-only access.

Revision ID: 0004
Revises: 0003

Bug: 0002 seeded `auditor` with the full admin permission set, including
branch.manage and member.manage — an auditor could mutate tenant data.
Corrected to read-only: org.read, branch.read, member.read, audit.read.
Caught by tests/test_rbac_audit.py::test_auditor_can_read_audit_only.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

AUDITOR_PERMS = ["org.read", "branch.read", "member.read", "audit.read"]


def upgrade() -> None:
    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT id FROM roles WHERE key = 'auditor'")).fetchone()
    if row is None:
        return
    role_id = row[0]
    conn.execute(
        sa.text("DELETE FROM role_permissions WHERE role_id = :rid"), {"rid": role_id}
    )
    conn.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_key) "
            "VALUES (:rid, :perm)"
        ),
        [{"rid": role_id, "perm": p} for p in AUDITOR_PERMS],
    )


def downgrade() -> None:
    # Restore the (buggy) admin-equivalent set — intentionally not recommended.
    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT id FROM roles WHERE key = 'auditor'")).fetchone()
    if row is None:
        return
    role_id = row[0]
    admin_perms = [
        "org.read", "org.manage", "branch.read", "branch.manage",
        "member.read", "member.manage", "audit.read",
    ]
    conn.execute(
        sa.text("DELETE FROM role_permissions WHERE role_id = :rid"), {"rid": role_id}
    )
    conn.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_key) "
            "VALUES (:rid, :perm)"
        ),
        [{"rid": role_id, "perm": p} for p in admin_perms],
    )
