"""tenant_key: wrapped per-tenant data-encryption keys

Revision ID: 0003_tenant_key
Revises: 0002_events_jobs_workflows
Create Date: 2026-10-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from xolotl.platform.tenancy.ddl import enable_rls

revision: str = "0003_tenant_key"
down_revision: str | None = "0002_events_jobs_workflows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _ = op.create_table(
        "tenant_key",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        # NULL once the key is destroyed (crypto-shredding).
        sa.Column("wrapped_dek", sa.LargeBinary(), nullable=True),
        sa.Column("kek_ref", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("destroyed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name="fk_tenant_key_tenant_id_tenant"
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_tenant_key"),
        sa.UniqueConstraint(
            "tenant_id",
            "purpose",
            "version",
            name="uq_tenant_key_tenant_id_purpose_version",
        ),
    )
    enable_rls("tenant_key")


def downgrade() -> None:
    # Dropping the table drops its policy and grants with it.
    op.drop_table("tenant_key")
