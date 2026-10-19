"""Policy pack tables (spec §4.1 and §4.2).

``policy_pack`` and ``policy_pack_version`` are shared reference data, written by
the owner engine only. Assignments, override versions and snapshots belong to a
tenant and follow the kernel's row-level security conventions.
"""

from datetime import date, datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.platform.db import Base
from xolotl.platform.ids import uuid7
from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_fk, tenant_table_args


class PolicyPack(Base):
    __tablename__: str = "policy_pack"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    code: Mapped[str] = mapped_column(Text, unique=True)
    country_code: Mapped[str] = mapped_column(String(2))
    sector: Mapped[str] = mapped_column(Text)
    licence: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)


class PolicyPackVersion(Base):
    __tablename__: str = "policy_pack_version"
    __table_args__: tuple[SchemaItem, ...] = (UniqueConstraint("pack_id", "version"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    pack_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("policy_pack.id"))
    version: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    config: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    content_hash: Mapped[bytes] = mapped_column(LargeBinary)
    loaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    loaded_by: Mapped[str] = mapped_column(Text)
    source_path: Mapped[str] = mapped_column(Text)


class TenantPackAssignment(TenantTableMixin, Base):
    __tablename__: str = "tenant_pack_assignment"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "tenant_pack_assignment", tenant_fk("approved_by", "staff_user")
    )

    pack_version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("policy_pack_version.id")
    )
    # Null applies the pack to every product of the tenant.
    product: Mapped[str | None] = mapped_column(Text)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    approved_by: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class TenantOverrideVersion(TenantTableMixin, Base):
    __tablename__: str = "tenant_override_version"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "tenant_override_version",
        tenant_fk("approved_by", "staff_user"),
        UniqueConstraint("tenant_id", "version"),
    )

    version: Mapped[int] = mapped_column(Integer)
    overrides: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    effective_from: Mapped[date] = mapped_column(Date)
    approved_by: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class PolicySnapshot(TenantTableMixin, Base):
    __tablename__: str = "policy_snapshot"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "policy_snapshot", UniqueConstraint("tenant_id", "content_hash")
    )

    content_hash: Mapped[bytes] = mapped_column(LargeBinary)
    content: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
