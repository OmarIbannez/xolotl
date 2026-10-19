"""Tables owned by the tenants module (spec §5)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.platform.crypto.encrypted import Encrypted, EncryptedStr
from xolotl.platform.db import Base
from xolotl.platform.enums import ApiKeyStatus, StaffStatus, TenantEnv, TenantStatus
from xolotl.platform.ids import uuid7
from xolotl.platform.sqltypes import enum_column
from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_fk, tenant_table_args


class Tenant(Base):
    __tablename__: str = "tenant"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    legal_name: Mapped[str] = mapped_column(Text)
    country_code: Mapped[str] = mapped_column(String(2))
    cell: Mapped[str] = mapped_column(Text, default="mx-1", server_default="mx-1")
    env: Mapped[TenantEnv] = mapped_column(enum_column(TenantEnv))
    status: Mapped[TenantStatus] = mapped_column(enum_column(TenantStatus))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ApiKeyLookup(Base):
    """Shared table: resolves a key prefix to its tenant.

    It must work before any RLS context exists.
    """

    __tablename__: str = "api_key_lookup"

    key_prefix: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("tenant.id"))


class StaffUser(TenantTableMixin, Base):
    __tablename__: str = "staff_user"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "staff_user",
        ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        UniqueConstraint("tenant_id", "email"),
    )

    email: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    status: Mapped[StaffStatus] = mapped_column(enum_column(StaffStatus))
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class StaffCredential(TenantTableMixin, Base):
    __tablename__: str = "staff_credential"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "staff_credential",
        tenant_fk("staff_user_id", "staff_user"),
        UniqueConstraint("tenant_id", "staff_user_id"),
    )

    staff_user_id: Mapped[UUID] = mapped_column(Uuid)
    password_hash: Mapped[str] = mapped_column(Text)
    # Encrypted under the tenant's DATA key. The enrolment response shows the base32
    # secret once; nothing else holds it in the clear.
    totp_secret: Mapped[Encrypted[str] | None] = mapped_column(EncryptedStr)
    totp_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    failed_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ApiKey(TenantTableMixin, Base):
    __tablename__: str = "api_key"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "api_key",
        tenant_fk("created_by", "staff_user"),
        UniqueConstraint("key_prefix"),
    )

    name: Mapped[str] = mapped_column(Text)
    key_prefix: Mapped[str] = mapped_column(Text)
    key_hash: Mapped[bytes] = mapped_column(LargeBinary)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    env: Mapped[TenantEnv] = mapped_column(enum_column(TenantEnv))
    status: Mapped[ApiKeyStatus] = mapped_column(enum_column(ApiKeyStatus))
    created_by: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
