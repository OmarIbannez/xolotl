"""party: customer_relationship.first_processed_at

Revision ID: 0015_first_processed
Revises: 0014_decide_profiles
Create Date: 2026-10-22

The rules count a customer's history months from the earliest ``occurred_at``
among the relationship's ``PROCESSED`` transactions. Reading it with
``min(occurred_at)`` had no bound on ``occurred_at``, so every profile probed the
relationship index of every monthly ``transaction`` partition. Processing now keeps
that instant on the relationship (earliest wins) and retention moves it forward,
so the profile reads it with the row.

Upgrade adds the nullable column and fills it from the processed rows already
there. Row-level security is forced on both tables and binds the owner of a
managed server (neither a superuser nor ``BYPASSRLS``), so the fill runs once per
tenant with ``app.tenant_id`` set for the transaction; the tenant list is read as
the platform operator. Downgrade drops the column.

Downtime. ``add_column`` takes ``ACCESS EXCLUSIVE`` on ``customer_relationship`` and
the whole upgrade runs in one transaction, so the lock is held until every
tenant's fill is done. The fill aggregates each tenant's processed rows across
every ``transaction`` partition and rewrites each relationship that has one, so it
lasts in proportion to ``transaction``. ``serve`` migrates before it listens and
the old instance is stopped first, so nothing waits on the lock, but the service
stays down until the fill ends: on a large tenant, allow for it in the deploy's
start-up and health-check timeouts.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_first_processed"
down_revision: str | None = "0014_decide_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FILL = sa.text(
    """
    UPDATE customer_relationship AS cr
    SET first_processed_at = earliest.first
    FROM (
        SELECT relationship_id, min(occurred_at) AS first
        FROM transaction
        WHERE tenant_id = CAST(:tenant_id AS uuid) AND status = 'PROCESSED'
        GROUP BY relationship_id
    ) AS earliest
    WHERE cr.tenant_id = CAST(:tenant_id AS uuid)
      AND cr.id = earliest.relationship_id
    """
)


def upgrade() -> None:
    op.add_column(
        "customer_relationship",
        sa.Column("first_processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    conn = op.get_bind()
    _ = conn.execute(
        sa.text("SELECT set_config('app.actor_type', 'PLATFORM_OPERATOR', true)")
    )
    tenants = [str(row) for row in conn.scalars(sa.text("SELECT id FROM tenant"))]
    for tenant_id in tenants:
        _ = conn.execute(
            sa.text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        _ = conn.execute(_FILL, {"tenant_id": tenant_id})
    _ = conn.execute(
        sa.text(
            "SELECT set_config('app.tenant_id', '', true), "
            "set_config('app.actor_type', '', true)"
        )
    )


def downgrade() -> None:
    op.drop_column("customer_relationship", "first_processed_at")
