from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, LargeBinary, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from xolotl.platform.db import Base


class AuditEventRow(Base):
    """One link of a tenant's audit chain. Partitioned monthly by ``at``."""

    __tablename__: str = "audit_event"
    __table_args__: tuple[Index, dict[str, str]] = (
        Index("ix_audit_event_tenant_seq", "tenant_id", "seq"),
        {"postgresql_partition_by": "RANGE (at)"},
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger)
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    prev_hash: Mapped[bytes] = mapped_column(LargeBinary)
    hash: Mapped[bytes] = mapped_column(LargeBinary)
