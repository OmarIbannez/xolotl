"""connector credentials and metered connector calls

Revision ID: 0007_connectors
Revises: 0006_policy_units_calendar
Create Date: 2026-10-19

Two tenant tables follow the kernel's conventions: ``(tenant_id, id)`` primary key
and forced row-level security with the kernel predicate.
``connector_credential.secret`` holds the credential fields as JSON ciphertext under
the tenant's DATA key; ``connector_call`` meters one row per connector run, indexed
by tenant and time for the usage aggregation.
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

from xolotl.platform.tenancy.ddl import enable_rls

revision: str = "0007_connectors"
down_revision: str | None = "0006_policy_units_calendar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column[datetime]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    _ = op.create_table(
        "connector_credential",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connector", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("secret", sa.LargeBinary(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _created_at(),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_connector_credential"),
        sa.UniqueConstraint(
            "tenant_id",
            "connector",
            "mode",
            name="uq_connector_credential_tenant_id_connector_mode",
        ),
    )
    enable_rls("connector_credential")

    _ = op.create_table(
        "connector_call",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connector", sa.Text(), nullable=False),
        sa.Column("check_type", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("route", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "cost_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("evidence_key", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("subject_party_id", sa.Uuid(), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_connector_call"),
    )
    op.create_index(
        "ix_connector_call_tenant_day", "connector_call", ["tenant_id", "created_at"]
    )
    enable_rls("connector_call")


def downgrade() -> None:
    # Dropping a table drops its index, policy and grants with it.
    op.drop_table("connector_call")
    op.drop_table("connector_credential")
