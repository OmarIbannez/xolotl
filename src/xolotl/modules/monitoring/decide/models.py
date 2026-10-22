"""The decide tables (SP-04 spec §5.2, the 4c block; migration 0014).

``rail_setting`` holds a rail's fail mode where an ADMIN set one; a rail without a
row answers ``XOLOTL_MONITORING_DECIDE_FAIL_MODE``. ``txn_decision`` stores every
answer of ``POST /v1/transactions/decide``: it is partitioned monthly by
``decided_at`` and keyed ``(tenant_id, decided_at, id)``, like ``transaction``, so
importing this module registers it with ``xolotl.platform.partitions`` and the
partition job keeps its months ahead. ``ix_txn_decision_id`` serves the lookup by
id, which the partitioned key alone would answer by scanning every partition (plan
4c Deviation 7). ``reasons`` names rule versions and is shown to staff only beyond
each ``code``. Other tables are referenced by name.
"""

from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.modules.monitoring.decide.enums import (
    DecisionMode,
    DecisionOutcome,
    FailMode,
)
from xolotl.modules.monitoring.txn.schema import Rail
from xolotl.platform.db import Base
from xolotl.platform.ids import uuid7
from xolotl.platform.partitions import PartitionedTable, register_partitioned_table
from xolotl.platform.sqltypes import enum_column
from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_fk, tenant_table_args

TABLE = "txn_decision"

register_partitioned_table(PartitionedTable(TABLE, "decided_at", None))


def _named_fk(column: str, target: str, name: str) -> ForeignKeyConstraint:
    # The conventional name would pass Postgres's 63 bytes.
    return ForeignKeyConstraint(
        ["tenant_id", column], [f"{target}.tenant_id", f"{target}.id"], name=name
    )


class RailSetting(TenantTableMixin, Base):
    """A rail's fail mode as an ADMIN set it; one row per tenant and rail."""

    __tablename__: str = "rail_setting"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "rail_setting",
        UniqueConstraint("tenant_id", "rail"),
        tenant_fk("changed_by", "staff_user"),
    )

    rail: Mapped[Rail] = mapped_column(enum_column(Rail))
    fail_mode: Mapped[FailMode] = mapped_column(enum_column(FailMode))
    changed_by: Mapped[UUID] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class TxnDecision(Base):
    """One synchronous decision, monthly-partitioned by ``decided_at``."""

    __tablename__: str = TABLE
    __table_args__: tuple[SchemaItem | dict[str, str], ...] = (
        PrimaryKeyConstraint("tenant_id", "decided_at", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "occurred_at", "transaction_id"],
            ["transaction.tenant_id", "transaction.occurred_at", "transaction.id"],
            name="fk_txn_decision_transaction",
        ),
        _named_fk(
            "relationship_id", "customer_relationship", "fk_txn_decision_relationship"
        ),
        _named_fk("account_id", "account", "fk_txn_decision_account"),
        _named_fk(
            "policy_snapshot_id", "policy_snapshot", "fk_txn_decision_policy_snapshot"
        ),
        Index(
            "ux_txn_decision_external",
            "tenant_id",
            "external_id",
            "decided_at",
            unique=True,
        ),
        Index(
            "ix_txn_decision_relationship",
            "tenant_id",
            "relationship_id",
            text("decided_at DESC"),
        ),
        Index("ix_txn_decision_id", "tenant_id", "id"),
        {"postgresql_partition_by": "RANGE (decided_at)"},
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    id: Mapped[UUID] = mapped_column(Uuid, default=uuid7)
    external_id: Mapped[str] = mapped_column(Text)
    rail: Mapped[Rail] = mapped_column(enum_column(Rail))
    relationship_id: Mapped[UUID] = mapped_column(Uuid)
    account_id: Mapped[UUID | None] = mapped_column(Uuid)
    decision: Mapped[DecisionOutcome] = mapped_column(enum_column(DecisionOutcome))
    mode: Mapped[DecisionMode] = mapped_column(enum_column(DecisionMode))
    # [{"code", "rule_version_id", "severity", "detail"}]; an API client sees codes.
    reasons: Mapped[list[dict[str, JsonValue]]] = mapped_column(JSONB)
    # sha256 over the sorted definition hashes of the sync versions evaluated.
    ruleset_hash: Mapped[bytes] = mapped_column(LargeBinary)
    policy_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid)
    latency_ms: Mapped[int] = mapped_column(Integer)
    db_ms: Mapped[int] = mapped_column(Integer)
    # Set when the transaction arrives through ingest, matched by external_id.
    transaction_id: Mapped[UUID | None] = mapped_column(Uuid)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 'api_client:<id>'.
    actor: Mapped[str] = mapped_column(Text)
