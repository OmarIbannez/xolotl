"""The shared ``unit_value`` table (spec §4.1).

A correction is a new row; the old row's ``superseded_by`` points at it, so a
lookup reads only rows whose ``superseded_by`` is null. ``id`` is an identity
column: the application role inserts without any grant on a sequence.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.platform.db import Base


class UnitValueRow(Base):
    __tablename__: str = "unit_value"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("unit_code", "valid_from", "fetched_at"),
        Index(
            "ix_unit_value_lookup",
            "unit_code",
            "valid_from",
            postgresql_where=text("superseded_by IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    unit_code: Mapped[str] = mapped_column(Text)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    quote_ccy: Mapped[str] = mapped_column(String(3))
    source: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("unit_value.id")
    )
