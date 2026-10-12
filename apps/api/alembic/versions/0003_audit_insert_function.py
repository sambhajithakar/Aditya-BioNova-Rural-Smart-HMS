"""Add insert_audit_event() SECURITY DEFINER helper.

Revision ID: 0003
Revises: 0002

Why this exists: PostgreSQL applies the table's SELECT row policies to the
output of INSERT ... RETURNING. Audit events with organization_id NULL
(failed logins of unknown emails, etc.) are invisible to every tenant by
design, so an ORM/Core INSERT with RETURNING is rejected for them.

The function keeps the tenancy WRITE guard (a caller may only write its own
organization's events, or org-NULL system events) while performing the
INSERT without RETURNING, and is owned by the table owner so RLS SELECT
policies do not apply to it.
"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION insert_audit_event(
            p_organization_id uuid,
            p_actor_user_id uuid,
            p_action text,
            p_resource_type text,
            p_resource_id text,
            p_metadata jsonb,
            p_correlation_id text,
            p_ip_address text
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        BEGIN
            IF p_organization_id IS NOT NULL
               AND p_organization_id
                   <> nullif(current_setting('app.current_org_id', true), '')::uuid THEN
                RAISE EXCEPTION 'audit insert denied for organization %', p_organization_id;
            END IF;
            INSERT INTO audit_events (
                organization_id, actor_user_id, action, resource_type,
                resource_id, metadata, correlation_id, ip_address
            ) VALUES (
                p_organization_id, p_actor_user_id, p_action, p_resource_type,
                p_resource_id, p_metadata, p_correlation_id, p_ip_address
            );
        END;
        $$;
    """)
    fn_sig = (
        "insert_audit_event(uuid, uuid, text, text, text, jsonb, text, text)"
    )
    op.execute(f"REVOKE ALL ON FUNCTION {fn_sig} FROM PUBLIC;")
    op.execute(f"GRANT EXECUTE ON FUNCTION {fn_sig} TO hms_app;")


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "insert_audit_event(uuid, uuid, text, text, text, jsonb, text, text);"
    )
