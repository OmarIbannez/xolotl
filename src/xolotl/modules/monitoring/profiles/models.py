"""The ``behaviour_profile`` table (SP-04 spec §5.2 4c block, §13.3; migration 0014).

One row per relationship and month: the month's ``metrics`` from processed
transactions (amounts as decimal strings), a copy of the relationship's
``declared_profile`` when it was computed, and the actual-to-declared ``deviation``
ratios, ``NULL`` without a declaration. The monthly ``monitoring.profiles`` job
(plan 4c C8) writes it; relationships and parties are referenced by table name.
"""

from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.platform.db import Base
from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_fk, tenant_table_args


class BehaviourProfile(TenantTableMixin, Base):
    __tablename__: str = "behaviour_profile"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "behaviour_profile",
        UniqueConstraint("tenant_id", "relationship_id", "month"),
        # The conventional name would pass Postgres's 63 bytes.
        ForeignKeyConstraint(
            ["tenant_id", "relationship_id"],
            ["customer_relationship.tenant_id", "customer_relationship.id"],
            name="fk_behaviour_profile_relationship",
        ),
        tenant_fk("party_id", "party"),
    )

    relationship_id: Mapped[UUID] = mapped_column(Uuid)
    party_id: Mapped[UUID] = mapped_column(Uuid)
    # 'YYYY-MM'.
    month: Mapped[str] = mapped_column(Text)
    metrics: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    declared_profile: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB)
    deviation: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB)
    deviated: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
