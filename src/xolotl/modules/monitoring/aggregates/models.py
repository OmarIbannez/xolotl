"""The aggregate tables (SP-04 spec §5.2, first block; migration 0012).

Each table is keyed naturally, with no ``id`` (plan 4a Deviation 3). A bucket holds
one declaration's running value for one key over one hour, day, month, quarter or
accumulator; ``agg_distinct`` holds the blind-indexed members a distinct count
counts (spec Decision 6); ``agg_declaration`` holds every declaration the tenant's
processing maintains and how far its backfill has walked.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    LargeBinary,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.modules.monitoring.enums import BucketKind
from xolotl.modules.monitoring.txn.schema import KeyKind
from xolotl.platform.db import Base
from xolotl.platform.sqltypes import enum_column


class AggBucket(Base):
    __tablename__: str = "agg_bucket"
    __table_args__: tuple[SchemaItem, ...] = (
        PrimaryKeyConstraint("tenant_id", "key_kind", "key", "agg_id", "bucket_start"),
        # The decide path's point read.
        Index(
            "ix_agg_bucket_window",
            "tenant_id",
            "key_kind",
            "key",
            "agg_id",
            "bucket_kind",
            text("bucket_start DESC"),
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid)
    key_kind: Mapped[KeyKind] = mapped_column(enum_column(KeyKind))
    # The uuid as text, or the hex of a counterparty's dedup hash or a device hash.
    key: Mapped[str] = mapped_column(Text)
    # 16 hex characters of the declaration's hash (spec §8.2).
    agg_id: Mapped[str] = mapped_column(Text)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bucket_kind: Mapped[BucketKind] = mapped_column(enum_column(BucketKind))
    # ACCUM only: bucket_start plus the window.
    bucket_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    value: Mapped[Decimal] = mapped_column(
        Numeric(28, 10), default=Decimal(0), server_default=text("0")
    )
    count: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    max_value: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    min_value: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    first_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class AggDistinct(Base):
    __tablename__: str = "agg_distinct"
    __table_args__: tuple[SchemaItem, ...] = (
        PrimaryKeyConstraint(
            "tenant_id", "key_kind", "key", "agg_id", "bucket_start", "member_hash"
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid)
    key_kind: Mapped[KeyKind] = mapped_column(enum_column(KeyKind))
    key: Mapped[str] = mapped_column(Text)
    agg_id: Mapped[str] = mapped_column(Text)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # HMAC under the tenant's index key of the member (a counterparty, a device, …).
    member_hash: Mapped[bytes] = mapped_column(LargeBinary)


class AggDeclaration(Base):
    __tablename__: str = "agg_declaration"
    __table_args__: tuple[SchemaItem, ...] = (
        PrimaryKeyConstraint("tenant_id", "agg_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid)
    agg_id: Mapped[str] = mapped_column(Text)
    # The AggregateDeclaration's canonical JSON.
    declaration: Mapped[dict[str, object]] = mapped_column(JSONB)
    bucket_kind: Mapped[BucketKind] = mapped_column(enum_column(BucketKind))
    # DURATION, MONTH, QUARTER or ACCUM: plan 4a's A7 defines the WindowKind enum.
    window_kind: Mapped[str] = mapped_column(String(32))
    # An ISO-8601 duration ('P6M' for ACCUM) or the period name.
    window: Mapped[str] = mapped_column(Text)
    # NULL until monitoring.agg_backfill has walked the window's history.
    backfilled_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
