"""policy packs, unit values, holidays, regulatory profile and branches

Revision ID: 0006_policy_units_calendar
Revises: 0005_encrypt_idempotency
Create Date: 2026-10-19

Four shared reference tables (``policy_pack``, ``policy_pack_version``,
``unit_value``, ``holiday``) carry no row security. The application role reads all
four; it also inserts into and updates ``unit_value`` (the fetch job appends values
and marks a corrected row with ``superseded_by``) but never deletes from it. Packs
and holidays are written by the owner engine only.

Five tenant tables follow the kernel's conventions: ``(tenant_id, id)`` primary key,
composite foreign keys into the tenant, forced row-level security with the kernel
predicate. ``tenant_regulatory_profile.rfc`` and ``tenant_branch.address`` hold
ciphertext under the tenant's DATA key.
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from xolotl.platform.db import APP_ROLE
from xolotl.platform.tenancy.ddl import enable_rls, grant_shared

revision: str = "0006_policy_units_calendar"
down_revision: str | None = "0005_encrypt_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column[datetime]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def _approved_by_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "approved_by"],
        ["staff_user.tenant_id", "staff_user.id"],
        name=f"fk_{table}_tenant_id_approved_by_staff_user",
    )


def _shared_tables() -> None:
    _ = op.create_table(
        "policy_pack",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("sector", sa.Text(), nullable=False),
        sa.Column("licence", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_policy_pack"),
        sa.UniqueConstraint("code", name="uq_policy_pack_code"),
    )
    grant_shared("policy_pack")

    _ = op.create_table(
        "policy_pack_version",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pack_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "loaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("loaded_by", sa.Text(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["pack_id"],
            ["policy_pack.id"],
            name="fk_policy_pack_version_pack_id_policy_pack",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_policy_pack_version"),
        sa.UniqueConstraint(
            "pack_id", "version", name="uq_policy_pack_version_pack_id_version"
        ),
    )
    grant_shared("policy_pack_version")

    _ = op.create_table(
        "unit_value",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("unit_code", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("value", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("quote_ccy", sa.String(length=3), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["unit_value.id"],
            name="fk_unit_value_superseded_by_unit_value",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_unit_value"),
        sa.UniqueConstraint(
            "unit_code",
            "valid_from",
            "fetched_at",
            name="uq_unit_value_unit_code_valid_from_fetched_at",
        ),
    )
    grant_shared("unit_value")
    # The fetch job appends values and supersedes corrected rows; it never deletes.
    op.execute(f"GRANT INSERT, UPDATE ON unit_value TO {APP_ROLE}")
    op.create_index(
        "ix_unit_value_lookup",
        "unit_value",
        ["unit_code", "valid_from"],
        postgresql_where=sa.text("superseded_by IS NULL"),
    )

    _ = op.create_table(
        "holiday",
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("country_code", "scope", "day", name="pk_holiday"),
    )
    grant_shared("holiday")


def _policy_tenant_tables() -> None:
    _ = op.create_table(
        "tenant_pack_assignment",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pack_version_id", sa.Uuid(), nullable=False),
        sa.Column("product", sa.Text(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=False),
        _created_at(),
        _approved_by_fk("tenant_pack_assignment"),
        sa.ForeignKeyConstraint(
            ["pack_version_id"],
            ["policy_pack_version.id"],
            name="fk_tenant_pack_assignment_pack_version_id_policy_pack_version",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_tenant_pack_assignment"),
    )
    enable_rls("tenant_pack_assignment")

    _ = op.create_table(
        "tenant_override_version",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("overrides", postgresql.JSONB(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("approved_by", sa.Uuid(), nullable=False),
        _created_at(),
        _approved_by_fk("tenant_override_version"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_tenant_override_version"),
        sa.UniqueConstraint(
            "tenant_id", "version", name="uq_tenant_override_version_tenant_id_version"
        ),
    )
    enable_rls("tenant_override_version")

    _ = op.create_table(
        "policy_snapshot",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("content_hash", sa.LargeBinary(), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_policy_snapshot"),
        sa.UniqueConstraint(
            "tenant_id",
            "content_hash",
            name="uq_policy_snapshot_tenant_id_content_hash",
        ),
    )
    enable_rls("policy_snapshot")


def _tenant_profile_tables() -> None:
    _ = op.create_table(
        "tenant_regulatory_profile",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rfc", sa.LargeBinary(), nullable=False),
        sa.Column("legal_name", sa.Text(), nullable=False),
        sa.Column("casfim_supervisor", sa.Text(), nullable=True),
        sa.Column("casfim_entity_key", sa.Text(), nullable=True),
        sa.Column("spplld_activity_key", sa.Text(), nullable=True),
        sa.Column("platform_domain", sa.Text(), nullable=True),
        sa.Column(
            "entidad_colegiada",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("compliance_officer_user_id", sa.Uuid(), nullable=True),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_tenant_regulatory_profile"),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_regulatory_profile_tenant_id"),
    )
    enable_rls("tenant_regulatory_profile")

    _ = op.create_table(
        "tenant_branch",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("address", sa.LargeBinary(), nullable=False),
        sa.Column("state_code", sa.Text(), nullable=False),
        sa.Column("municipality_code", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="ACTIVE"
        ),
        _created_at(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_tenant_branch"),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_tenant_branch_tenant_id_code"
        ),
    )
    enable_rls("tenant_branch")


def upgrade() -> None:
    _shared_tables()
    _policy_tenant_tables()
    _tenant_profile_tables()


def downgrade() -> None:
    # Dropping a table drops its index, policy and grants with it.
    for table in (
        "tenant_branch",
        "tenant_regulatory_profile",
        "policy_snapshot",
        "tenant_override_version",
        "tenant_pack_assignment",
        "holiday",
        "unit_value",
        "policy_pack_version",
        "policy_pack",
    ):
        op.drop_table(table)
