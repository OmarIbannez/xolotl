"""The ``tenant_key`` table: one wrapped data key per tenant, purpose and version."""

from datetime import datetime
from typing import final, override

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem
from sqlalchemy.types import TypeDecorator

from xolotl.platform.crypto.primitives import SecretBytes
from xolotl.platform.db import Base
from xolotl.platform.enums import KeyPurpose
from xolotl.platform.sqltypes import enum_column
from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_table_args


@final
class SecretBinary(TypeDecorator[bytes]):
    """A ``bytea`` column whose values come back as ``SecretBytes``.

    SQLAlchemy logs every result row at DEBUG and ``hide_parameters`` only hides the
    bound parameters, so a plain ``bytes`` column would print its content there, and
    in the repr of any ``Row`` a traceback shows. Here the values print as a mask.
    """

    impl = LargeBinary
    cache_ok = True

    @override
    def process_result_value(
        self, value: bytes | None, dialect: Dialect
    ) -> bytes | None:
        del dialect  # part of the hook's signature, not needed here
        return None if value is None else SecretBytes(value)


class TenantKey(TenantTableMixin, Base):
    """A data-encryption key, wrapped by the key-encryption key.

    ``wrapped_dek`` is NULL once the key is destroyed: that is crypto-shredding, the
    data encrypted under it stays but can no longer be read. ``retired_at`` marks a
    version that is no longer used for new writes but must stay readable.
    """

    __tablename__: str = "tenant_key"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "tenant_key",
        ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        UniqueConstraint("tenant_id", "purpose", "version"),
    )

    purpose: Mapped[KeyPurpose] = mapped_column(enum_column(KeyPurpose))
    version: Mapped[int] = mapped_column(Integer)
    wrapped_dek: Mapped[bytes | None] = mapped_column(SecretBinary)
    kek_ref: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
