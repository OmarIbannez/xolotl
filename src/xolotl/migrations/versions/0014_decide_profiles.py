"""monitoring: rail settings, partitioned decisions and behaviour profiles

Revision ID: 0014_decide_profiles
Revises: 0013_rules_captures
Create Date: 2026-10-21

SP-04 spec §5.2, the 4c block (the spec's 0013). ``rail_setting`` holds the fail
mode an ADMIN set for a rail; ``txn_decision`` stores every synchronous decision
and is keyed ``(tenant_id, decided_at, id)`` and partitioned monthly by
``decided_at``, like ``transaction``: its ``txn_decision_default`` partition and the
partitions of this month and the three after it are hardened with forced row-level
security and no grant, so the application role reaches rows through the parent
only. ``ix_txn_decision_id`` serves the lookup of a decision by id, which the
partitioned key alone would answer by scanning every partition (plan 4c Deviation
7). ``transaction.decision_id`` gets its composite foreign key,
``fk_transaction_decision``. ``behaviour_profile`` holds one row per relationship
and month, and ``customer_relationship`` gains ``declared_profile``. The three new
tables get forced row-level security and the default grant. ``counterparty`` gains
``ix_counterparty_identifier_hash`` for the reverse blocking of plan 4c C2, which
looks counterparties up by a list entry's identifiers. Downgrade reverses each step;
``txn_decision`` takes its partitions with it.

Locking. ``transaction`` and ``txn_decision`` reference each other, so creating a
partition of either one locks the other against writes while it runs. Partition
maintenance must therefore stay months ahead (``ensure_partitions`` and the daily
``monitoring.partitions`` job) and never run on the decide hot path. Adding
``fk_transaction_decision`` validates every ``transaction`` partition and blocks
ingest while it does: Postgres 17 has no ``NOT VALID`` foreign key on a partitioned
table. Run this upgrade in a quiet window.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from xolotl.platform.partitions import ensure_partitions, harden_partition
from xolotl.platform.tenancy.ddl import enable_rls

revision: str = "0014_decide_profiles"
down_revision: str | None = "0013_rules_captures"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _keys() -> list[sa.Column[UUID]]:
    return [
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
    ]


def _now(name: str) -> sa.Column[datetime]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def _at(name: str, *, nullable: bool = True) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _enum(name: str) -> sa.Column[str]:
    return sa.Column(name, sa.String(length=32), nullable=False)


def _text(name: str) -> sa.Column[str]:
    return sa.Column(name, sa.Text(), nullable=False)


def _uuid(name: str, *, nullable: bool = False) -> sa.Column[UUID]:
    return sa.Column(name, sa.Uuid(), nullable=nullable)


def _int(name: str) -> sa.Column[int]:
    return sa.Column(name, sa.Integer(), nullable=False)


def _jsonb(name: str, *, nullable: bool = True) -> sa.Column[object]:
    return sa.Column(name, postgresql.JSONB(), nullable=nullable)


def _fk(
    table: str, column: str, target: str, *, name: str | None = None
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{target}.tenant_id", f"{target}.id"],
        name=name or f"fk_{table}_tenant_id_{column}_{target}",
    )


def _rail_settings() -> None:
    _ = op.create_table(
        "rail_setting",
        *_keys(),
        _enum("rail"),
        _enum("fail_mode"),
        _uuid("changed_by"),
        _now("updated_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_rail_setting"),
        sa.UniqueConstraint("tenant_id", "rail", name="uq_rail_setting_tenant_id_rail"),
        _fk("rail_setting", "changed_by", "staff_user"),
    )
    enable_rls("rail_setting")


def _decisions() -> None:
    table = "txn_decision"
    _ = op.create_table(
        table,
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        _at("decided_at", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        _text("external_id"),
        _enum("rail"),
        _uuid("relationship_id"),
        _uuid("account_id", nullable=True),
        _enum("decision"),
        _enum("mode"),
        _jsonb("reasons", nullable=False),
        sa.Column("ruleset_hash", sa.LargeBinary(), nullable=False),
        _uuid("policy_snapshot_id", nullable=True),
        _int("latency_ms"),
        _int("db_ms"),
        _uuid("transaction_id", nullable=True),
        _at("occurred_at"),
        _text("actor"),
        sa.PrimaryKeyConstraint(
            "tenant_id", "decided_at", "id", name="pk_txn_decision"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "occurred_at", "transaction_id"],
            ["transaction.tenant_id", "transaction.occurred_at", "transaction.id"],
            name="fk_txn_decision_transaction",
        ),
        _fk(
            table,
            "relationship_id",
            "customer_relationship",
            name="fk_txn_decision_relationship",
        ),
        _fk(table, "account_id", "account", name="fk_txn_decision_account"),
        _fk(
            table,
            "policy_snapshot_id",
            "policy_snapshot",
            name="fk_txn_decision_policy_snapshot",
        ),
        postgresql_partition_by="RANGE (decided_at)",
    )
    op.create_index(
        "ux_txn_decision_external",
        table,
        ["tenant_id", "external_id", "decided_at"],
        unique=True,
    )
    op.create_index(
        "ix_txn_decision_relationship",
        table,
        ["tenant_id", "relationship_id", sa.text("decided_at DESC")],
    )
    op.create_index("ix_txn_decision_id", table, ["tenant_id", "id"])
    enable_rls(table)
    op.execute(f"CREATE TABLE {table}_default PARTITION OF {table} DEFAULT")
    conn = op.get_bind()
    harden_partition(conn, f"{table}_default")
    _ = ensure_partitions(conn, table=table, months_ahead=3)
    op.create_foreign_key(
        "fk_transaction_decision",
        "transaction",
        table,
        ["tenant_id", "decided_at", "decision_id"],
        ["tenant_id", "decided_at", "id"],
    )


def _profiles() -> None:
    table = "behaviour_profile"
    _ = op.create_table(
        table,
        *_keys(),
        _uuid("relationship_id"),
        _uuid("party_id"),
        _text("month"),
        _jsonb("metrics", nullable=False),
        _jsonb("declared_profile"),
        _jsonb("deviation"),
        sa.Column(
            "deviated", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        _now("computed_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_behaviour_profile"),
        sa.UniqueConstraint(
            "tenant_id",
            "relationship_id",
            "month",
            name="uq_behaviour_profile_tenant_id_relationship_id_month",
        ),
        _fk(
            table,
            "relationship_id",
            "customer_relationship",
            name="fk_behaviour_profile_relationship",
        ),
        _fk(table, "party_id", "party"),
    )
    enable_rls(table)
    op.add_column("customer_relationship", _jsonb("declared_profile"))


def upgrade() -> None:
    _rail_settings()
    _decisions()
    _profiles()
    op.create_index(
        "ix_counterparty_identifier_hash",
        "counterparty",
        ["tenant_id", "identifier_hash"],
    )


def downgrade() -> None:
    # Dropping a table drops its indexes, policy, grants and partitions with it.
    op.drop_index("ix_counterparty_identifier_hash", table_name="counterparty")
    op.drop_column("customer_relationship", "declared_profile")
    op.drop_table("behaviour_profile")
    op.drop_constraint("fk_transaction_decision", "transaction", type_="foreignkey")
    op.drop_table("txn_decision")
    op.drop_table("rail_setting")
