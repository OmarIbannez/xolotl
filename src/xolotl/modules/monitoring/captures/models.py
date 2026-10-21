"""The capture tables (SP-04 spec §5.2 4b block, §10; migration 0013).

A ``reportable_event`` is one transaction captured for one capture code (D13): its
pre-mapped report fields carry amounts, units, codes and ids, never a name (PRV-501).
A ``capture_group`` accumulates a key's transactions in a window until the threshold
is crossed. A ``capture_period`` is the reconciliation record of one capture code
and period. Transactions, parties, relationships and snapshots are referenced by
table name; the foreign keys whose conventional name would pass Postgres's 63 bytes,
or that the plan pins shorter, are named by hand.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.modules.monitoring.captures.enums import (
    CaptureGroupStatus,
    CaptureKind,
    CapturePeriodStatus,
    ReportableEventStatus,
)
from xolotl.modules.monitoring.txn.schema import KeyKind
from xolotl.platform.db import Base
from xolotl.platform.sqltypes import enum_column
from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_fk, tenant_table_args

OPEN_GROUP_PREDICATE = "status <> 'SUPERSEDED'"


def _named_fk(column: str, target: str, name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", column], [f"{target}.tenant_id", f"{target}.id"], name=name
    )


class CaptureGroup(TenantTableMixin, Base):
    """An accumulation window of one key for one capture code (spec §10.2)."""

    __tablename__: str = "capture_group"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "capture_group",
        _named_fk(
            "superseded_by_id", "capture_group", "fk_capture_group_superseded_by"
        ),
        Index(
            "ux_capture_group_open",
            "tenant_id",
            "capture_code",
            "key_kind",
            "key",
            "window_start",
            unique=True,
            postgresql_where=text(OPEN_GROUP_PREDICATE),
        ),
    )

    capture_code: Mapped[str] = mapped_column(Text)
    # RELATIONSHIP as a rule: the accumulator's key.
    key_kind: Mapped[KeyKind] = mapped_column(enum_column(KeyKind))
    key: Mapped[str] = mapped_column(Text)
    period_key: Mapped[str] = mapped_column(Text)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    threshold_crossed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    accumulated_minor: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
    accumulated_unit: Mapped[Decimal] = mapped_column(
        Numeric(28, 10), default=Decimal(0), server_default=text("0")
    )
    status: Mapped[CaptureGroupStatus] = mapped_column(
        enum_column(CaptureGroupStatus),
        default=CaptureGroupStatus.OPEN,
        server_default=CaptureGroupStatus.OPEN.value,
    )
    superseded_by_id: Mapped[UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ReportableEvent(TenantTableMixin, Base):
    """One transaction captured for one capture code, with its report fields."""

    __tablename__: str = "reportable_event"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "reportable_event",
        ForeignKeyConstraint(
            ["tenant_id", "occurred_at", "transaction_id"],
            ["transaction.tenant_id", "transaction.occurred_at", "transaction.id"],
            name="fk_reportable_event_transaction",
        ),
        tenant_fk("party_id", "party"),
        _named_fk(
            "relationship_id",
            "customer_relationship",
            "fk_reportable_event_relationship",
        ),
        tenant_fk("capture_group_id", "capture_group"),
        _named_fk(
            "supersedes_id", "reportable_event", "fk_reportable_event_supersedes"
        ),
        _named_fk(
            "superseded_by_id",
            "reportable_event",
            "fk_reportable_event_superseded_by",
        ),
        _named_fk(
            "policy_snapshot_id",
            "policy_snapshot",
            "fk_reportable_event_policy_snapshot",
        ),
        # Supersession voids an event before re-capturing (D13).
        UniqueConstraint("tenant_id", "capture_code", "transaction_id"),
        Index(
            "ix_reportable_event_period",
            "tenant_id",
            "report_code",
            "period_key",
            "status",
        ),
        Index("ix_reportable_event_transaction", "tenant_id", "transaction_id"),
    )

    capture_code: Mapped[str] = mapped_column(Text)
    report_code: Mapped[str] = mapped_column(Text)
    capture_kind: Mapped[CaptureKind] = mapped_column(enum_column(CaptureKind))
    transaction_id: Mapped[UUID] = mapped_column(Uuid)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    party_id: Mapped[UUID] = mapped_column(Uuid)
    relationship_id: Mapped[UUID] = mapped_column(Uuid)
    product: Mapped[str | None] = mapped_column(Text)
    # '2027-02' or '2027-Q1', per the capture's period.
    period_key: Mapped[str] = mapped_column(Text)
    # ACCUMULATION: the group whose crossing captured this transaction.
    capture_group_id: Mapped[UUID | None] = mapped_column(Uuid)
    # Pre-mapped report fields without PII (spec §10.3).
    fields: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    # The threshold and its conversion, for the record.
    unit_values: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    status: Mapped[ReportableEventStatus] = mapped_column(
        enum_column(ReportableEventStatus),
        default=ReportableEventStatus.CAPTURED,
        server_default=ReportableEventStatus.CAPTURED.value,
    )
    supersedes_id: Mapped[UUID | None] = mapped_column(Uuid)
    superseded_by_id: Mapped[UUID | None] = mapped_column(Uuid)
    void_reason: Mapped[str | None] = mapped_column(Text)
    policy_snapshot_id: Mapped[UUID] = mapped_column(Uuid)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    late: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )


class CapturePeriod(TenantTableMixin, Base):
    """One capture code's period and its close reconciliation (spec §10.4)."""

    __tablename__: str = "capture_period"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "capture_period", UniqueConstraint("tenant_id", "capture_code", "period_key")
    )

    capture_code: Mapped[str] = mapped_column(Text)
    report_code: Mapped[str] = mapped_column(Text)
    period_key: Mapped[str] = mapped_column(Text)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    status: Mapped[CapturePeriodStatus] = mapped_column(
        enum_column(CapturePeriodStatus),
        default=CapturePeriodStatus.OPEN,
        server_default=CapturePeriodStatus.OPEN.value,
    )
    event_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    reconciled_count: Mapped[int | None] = mapped_column(Integer)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Ids of the transactions the close found missing or extra.
    discrepancies: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    change_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
