"""screening: index screening_hit on (tenant_id, run_id)

Revision ID: 0011_screening_hit_run_index
Revises: 0010_screening
Create Date: 2026-10-20

The run view and the replay read a tenant's hits by run (``run_id = :run``), and the
purge handler of a party redacts the hits of the party's runs (``run_id IN (...)``);
the row-level security policy adds ``tenant_id = <the tenant>`` to each. 0010 gave
``screening_hit`` an index on ``(tenant_id, match_id)`` only, and the composite foreign
key to ``screening_run`` creates none, so every such read scanned the tenant's hits
through the primary key or the match index. ``ix_screening_hit_run`` on
``(tenant_id, run_id)`` serves all three as they are written. Downgrade drops it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_screening_hit_run_index"
down_revision: str | None = "0010_screening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_screening_hit_run"


def upgrade() -> None:
    op.create_index(INDEX, "screening_hit", ["tenant_id", "run_id"])


def downgrade() -> None:
    op.drop_index(INDEX, table_name="screening_hit")
