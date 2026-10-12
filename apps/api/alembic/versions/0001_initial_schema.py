"""Initial schema: tenancy, identity, RBAC, sessions, audit + row-level security.

Revision ID: 0001
Revises:
Create Date: 2026-10-12

Security notes
-------------
* Runtime queries run as the NON-OWNER role ``hms_app``; PostgreSQL row-level
  security (RLS) therefore enforces tenant isolation at the database layer
  (defence in depth behind service-layer scoping).
* ``audit_events`` is append-only (trigger + revoked UPDATE/DELETE grants).
* ``users`` and ``auth_sessions`` intentionally have NO RLS: authentication
  must read them before any tenant context exists. They are never joined into
  cross-tenant responses; ``password_hash`` is excluded from all API schemas.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

GUC_ORG = "nullif(current_setting('app.current_org_id', true), '')::uuid"
GUC_USER = "nullif(current_setting('app.current_user_id', true), '')::uuid"

TABLES = ["audit_events", "memberships", "auth_sessions", "branches", "role_permissions",
          "permissions", "roles", "users", "organizations"]


def upgrade() -> None:
    # ---------------------------------------------------------------- tables
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('active','suspended','deleting')", name="ck_org_status"),
    )

    op.create_table(
        "branches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "code", name="uq_branch_org_code"),
        sa.CheckConstraint("status IN ('active','inactive')", name="ck_branch_status"),
    )
    op.create_index("ix_branches_org_created", "branches", ["organization_id", "created_at"])

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("is_superadmin", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('active','disabled')", name="ck_user_status"),
    )

    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("key", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("system", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )

    op.create_table(
        "permissions",
        sa.Column("key", sa.String(60), primary_key=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("permission_key", sa.String(60),
                  sa.ForeignKey("permissions.key", ondelete="CASCADE"), primary_key=True),
    )

    op.create_table(
        "memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("branches.id", ondelete="SET NULL"), nullable=True),
        sa.Column("role_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "organization_id", name="uq_membership_user_org"),
        sa.CheckConstraint("status IN ('active','suspended')", name="ck_membership_status"),
    )
    op.create_index("ix_memberships_org", "memberships", ["organization_id"])

    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_agent", sa.String(400), nullable=True),
        sa.Column("ip_address", sa.String(60), nullable=True),
    )
    op.create_index("ix_auth_sessions_family", "auth_sessions", ["family_id"])
    op.create_index("ix_auth_sessions_user_org", "auth_sessions", ["user_id", "organization_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("resource_type", sa.String(60), nullable=True),
        sa.Column("resource_id", sa.String(80), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("ip_address", sa.String(60), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_org_created", "audit_events", ["organization_id", "created_at"])
    op.create_index("ix_audit_action", "audit_events", ["action"])

    # ---------------------------------------------------- application role
    # Non-owner: PostgreSQL enforces RLS for this role (owner would bypass).
    # Login/password are provisioned by deployment init (never in migrations).
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hms_app') THEN
                CREATE ROLE hms_app NOLOGIN;
            END IF;
        END
        $$;
    """)
    op.execute("GRANT USAGE ON SCHEMA public TO hms_app;")
    op.execute("""
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO hms_app;
        ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO hms_app;
    """)
    # Tighten: reference tables are read-only; the audit log is append-only.
    op.execute("REVOKE UPDATE, DELETE ON audit_events FROM hms_app;")
    op.execute(
        "REVOKE INSERT, UPDATE, DELETE ON roles, permissions, "
        "role_permissions FROM hms_app;"
    )
    op.execute("GRANT SELECT ON roles, permissions, role_permissions TO hms_app;")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO hms_app;")

    # ------------------------------------------------------- row-level security
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE branches ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE memberships ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;")

    # organizations: visible if token-org matches OR caller holds a membership
    op.execute(f"""
        CREATE POLICY org_select_member ON organizations FOR SELECT USING (
            id = {GUC_ORG}
            OR EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.organization_id = organizations.id
                  AND m.user_id = {GUC_USER}
            )
        );
    """)
    # organizations: only the registration flow (one-shot bootstrap flag) may insert
    op.execute("""
        CREATE POLICY org_insert_bootstrap ON organizations FOR INSERT
        WITH CHECK (current_setting('app.is_bootstrap', true) = 'true');
    """)
    op.execute(f"""
        CREATE POLICY org_update_member ON organizations FOR UPDATE USING (
            EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.organization_id = organizations.id
                  AND m.user_id = {GUC_USER}
                  AND m.status = 'active'
            )
        ) WITH CHECK (TRUE);
    """)
    # no DELETE policy on organizations => deletes are denied by default

    # branches: strict tenant scoping both ways
    op.execute(f"""
        CREATE POLICY branch_tenant ON branches
        USING (organization_id = {GUC_ORG})
        WITH CHECK (organization_id = {GUC_ORG});
    """)

    # memberships: own rows (login discovery) OR rows of the active tenant
    op.execute(f"""
        CREATE POLICY membership_select ON memberships FOR SELECT USING (
            user_id = {GUC_USER}
            OR organization_id = {GUC_ORG}
        );
    """)
    op.execute(f"""
        CREATE POLICY membership_insert ON memberships FOR INSERT
        WITH CHECK (organization_id = {GUC_ORG});
    """)
    op.execute(f"""
        CREATE POLICY membership_update ON memberships FOR UPDATE
        USING (organization_id = {GUC_ORG})
        WITH CHECK (organization_id = {GUC_ORG});
    """)
    op.execute(f"""
        CREATE POLICY membership_delete ON memberships FOR DELETE
        USING (organization_id = {GUC_ORG});
    """)

    # audit_events: readable for active tenant; writable for tenant or system (NULL)
    op.execute(f"""
        CREATE POLICY audit_select ON audit_events FOR SELECT
        USING (organization_id = {GUC_ORG});
    """)
    op.execute(f"""
        CREATE POLICY audit_insert ON audit_events FOR INSERT
        WITH CHECK (organization_id IS NULL OR organization_id = {GUC_ORG});
    """)
    # no UPDATE/DELETE policies => denied; plus trigger and revoked grants

    # -------------------------------------------- append-only audit guarantee
    op.execute("""
        CREATE OR REPLACE FUNCTION forbids_audit_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only (blocked %)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_audit_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION forbids_audit_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_append_only ON audit_events;")
    op.execute("DROP FUNCTION IF EXISTS forbids_audit_mutation();")
    for policy in [
        "audit_insert", "audit_select",
        "membership_delete", "membership_update", "membership_insert", "membership_select",
        "branch_tenant",
        "org_update_member", "org_insert_bootstrap", "org_select_member",
    ]:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON "
                   f"{'organizations' if policy.startswith('org') else ''}"
                   f"{'branches' if policy.startswith('branch') else ''}"
                   f"{'memberships' if policy.startswith('membership') else ''}"
                   f"{'audit_events' if policy.startswith('audit') else ''};")
    for table in TABLES:
        op.drop_table(table)
