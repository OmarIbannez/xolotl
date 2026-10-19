"""Kernel table behind the Idempotency-Key convention (spec §5, §7)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, LargeBinary, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from xolotl.platform.db import Base


class IdempotencyKey(Base):
    """One reservation or stored response per ``(tenant, actor, key)``.

    ``status_code == 0`` marks a reservation whose request is still running.
    """

    __tablename__: str = "idempotency_key"
    # Each reservation purges its tenant's expired keys by this range (_reserve).
    __table_args__: tuple[Index, ...] = (
        Index("ix_idempotency_key_tenant_id_expires_at", "tenant_id", "expires_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    actor_id: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[bytes] = mapped_column(LargeBinary)
    status_code: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
