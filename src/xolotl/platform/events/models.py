"""Tables behind the outbox, webhooks and in-app notifications (spec §9.1)."""

from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    BigInteger,
    DateTime,
    Identity,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.platform.db import Base
from xolotl.platform.enums import DeliveryStatus, EndpointStatus, OutboxStatus
from xolotl.platform.sqltypes import enum_column
from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_fk, tenant_table_args


class OutboxRow(TenantTableMixin, Base):
    """One event, written in the publisher's transaction and dispatched later.

    ``seq`` orders dispatch across the table. It is an identity column rather than a
    ``bigserial``: the application role may insert without any grant on an identity
    column's sequence, which a serial's sequence would need.
    """

    __tablename__: str = "outbox"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "outbox",
        UniqueConstraint("seq"),
        Index(
            "ix_outbox_pending",
            "status",
            "created_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    seq: Mapped[int] = mapped_column(BigInteger, Identity(), autoincrement=True)
    type: Mapped[str] = mapped_column(Text)
    key: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    status: Mapped[OutboxStatus] = mapped_column(
        enum_column(OutboxStatus),
        default=OutboxStatus.PENDING,
        server_default=OutboxStatus.PENDING.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventConsumption(Base):
    """A consumer handled an event; its presence makes redelivery a no-op.

    The key is natural, so the table has no ``id`` and is not the target of any
    ``(tenant_id, id)`` foreign key.
    """

    __tablename__: str = "event_consumption"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args("event_consumption")

    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    consumer: Mapped[str] = mapped_column(Text, primary_key=True)
    event_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    consumed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class WebhookEndpoint(TenantTableMixin, Base):
    __tablename__: str = "webhook_endpoint"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args("webhook_endpoint")

    url: Mapped[str] = mapped_column(Text)
    # bytea: plan 1b task B3 (EncryptedStr) flips this type; no schema change.
    secret: Mapped[bytes] = mapped_column(LargeBinary)
    event_types: Mapped[list[str]] = mapped_column(ARRAY(Text))
    status: Mapped[EndpointStatus] = mapped_column(enum_column(EndpointStatus))
    created_by: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class WebhookDelivery(TenantTableMixin, Base):
    """One event owed to one endpoint, retried until delivered or dead."""

    __tablename__: str = "webhook_delivery"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "webhook_delivery",
        tenant_fk("endpoint_id", "webhook_endpoint"),
        UniqueConstraint("tenant_id", "endpoint_id", "event_id"),
        Index("ix_webhook_delivery_next", "status", "next_attempt_at"),
    )

    endpoint_id: Mapped[UUID] = mapped_column(Uuid)
    event_id: Mapped[UUID] = mapped_column(Uuid)
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    status: Mapped[DeliveryStatus] = mapped_column(enum_column(DeliveryStatus))
    response_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Notification(TenantTableMixin, Base):
    """An in-app notice for one user or for everyone holding a role."""

    __tablename__: str = "notification"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args("notification")

    recipient_user_id: Mapped[UUID | None] = mapped_column(Uuid)
    recipient_role: Mapped[str | None] = mapped_column(Text)
    template: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
