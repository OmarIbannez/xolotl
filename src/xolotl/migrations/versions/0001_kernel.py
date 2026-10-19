"""kernel: tenant, staff, api keys, idempotency, audit

Revision ID: 0001_kernel
Revises:
Create Date: 2026-10-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from xolotl.platform.audit.partitions import (
    IMMUTABLE_FUNCTION,
    ensure_partitions,
    harden_partition,
)
from xolotl.platform.tenancy.ddl import (
    APPEND_ONLY_PRIVILEGES,
    TENANT_TABLE_PREDICATE,
    enable_rls,
    grant_shared,
)

revision: str = "0001_kernel"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUDIT_IMMUTABLE_FN = f"""
CREATE FUNCTION {IMMUTABLE_FUNCTION}() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_event is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    _ = op.create_table(
        "tenant",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("legal_name", sa.Text(), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("cell", sa.Text(), nullable=False, server_default="mx-1"),
        sa.Column("env", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tenant"),
        sa.UniqueConstraint("slug", name="uq_tenant_slug"),
    )
    enable_rls("tenant", predicate=TENANT_TABLE_PREDICATE)

    _ = op.create_table(
        "api_key_lookup",
        sa.Column("key_prefix", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name="fk_api_key_lookup_tenant_id_tenant"
        ),
        sa.PrimaryKeyConstraint("key_prefix", name="pk_api_key_lookup"),
    )
    grant_shared("api_key_lookup")

    _ = op.create_table(
        "staff_user",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("roles", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name="fk_staff_user_tenant_id_tenant"
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_staff_user"),
        sa.UniqueConstraint("tenant_id", "email", name="uq_staff_user_tenant_id_email"),
    )
    enable_rls("staff_user")

    _ = op.create_table(
        "staff_credential",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("staff_user_id", sa.Uuid(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("totp_secret", sa.LargeBinary(), nullable=True),
        sa.Column(
            "totp_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "failed_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "staff_user_id"],
            ["staff_user.tenant_id", "staff_user.id"],
            name="fk_staff_credential_tenant_id_staff_user_id_staff_user",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_staff_credential"),
        sa.UniqueConstraint(
            "tenant_id",
            "staff_user_id",
            name="uq_staff_credential_tenant_id_staff_user_id",
        ),
    )
    enable_rls("staff_credential")

    _ = op.create_table(
        "api_key",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key_prefix", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.LargeBinary(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("env", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["staff_user.tenant_id", "staff_user.id"],
            name="fk_api_key_tenant_id_created_by_staff_user",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_api_key"),
        sa.UniqueConstraint("key_prefix", name="uq_api_key_key_prefix"),
    )
    enable_rls("api_key")

    _ = op.create_table(
        "idempotency_key",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.LargeBinary(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id", "actor_id", "key", name="pk_idempotency_key"
        ),
    )
    op.create_index(
        "ix_idempotency_key_tenant_id_expires_at",
        "idempotency_key",
        ["tenant_id", "expires_at"],
    )
    enable_rls("idempotency_key")

    _ = op.create_table(
        "audit_event",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=True),
        sa.Column("entity_id", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("prev_hash", sa.LargeBinary(), nullable=False),
        sa.Column("hash", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "at", "id", name="pk_audit_event"),
        postgresql_partition_by="RANGE (at)",
    )
    op.create_index("ix_audit_event_tenant_seq", "audit_event", ["tenant_id", "seq"])
    # Append-only: the app role may read and insert, never update or delete; the
    # triggers below also stop the owner.
    enable_rls("audit_event", grant=APPEND_ONLY_PRIVILEGES)
    op.execute(AUDIT_IMMUTABLE_FN)
    op.execute(
        "CREATE TRIGGER audit_event_no_update_delete BEFORE UPDATE OR DELETE "
        f"ON audit_event FOR EACH ROW EXECUTE FUNCTION {IMMUTABLE_FUNCTION}()"
    )
    op.execute(
        "CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE "
        f"ON audit_event FOR EACH STATEMENT EXECUTE FUNCTION {IMMUTABLE_FUNCTION}()"
    )
    op.execute("CREATE TABLE audit_event_default PARTITION OF audit_event DEFAULT")
    conn = op.get_bind()
    harden_partition(conn, "audit_event_default")
    _ = ensure_partitions(conn, months_ahead=3)


def downgrade() -> None:
    op.drop_table("audit_event")  # drops partitions, policies and triggers with it
    op.execute(f"DROP FUNCTION IF EXISTS {IMMUTABLE_FUNCTION}()")
    op.drop_table("idempotency_key")
    op.drop_table("api_key")
    op.drop_table("staff_credential")
    op.drop_table("staff_user")
    op.drop_table("api_key_lookup")
    op.drop_table("tenant")
