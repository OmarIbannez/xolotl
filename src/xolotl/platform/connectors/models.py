"""The connector tables (SP-02 spec §4.2): stored credentials and metered calls.

``connector_credential.secret`` holds the credential fields as JSON ciphertext
under the tenant's DATA key; ``connector_call`` has one row per connector run.
``subject_party_id`` has no foreign key: ``party`` is a module's table.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.platform.crypto.encrypted import Encrypted, EncryptedJson
from xolotl.platform.db import Base
from xolotl.platform.enums import CallOutcome, CheckType, ConnectorMode, ConnectorRoute
from xolotl.platform.sqltypes import enum_column
from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_table_args


class ConnectorCredential(TenantTableMixin, Base):
    __tablename__: str = "connector_credential"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "connector_credential", UniqueConstraint("tenant_id", "connector", "mode")
    )

    connector: Mapped[str] = mapped_column(Text)
    mode: Mapped[ConnectorMode] = mapped_column(enum_column(ConnectorMode))
    secret: Mapped[Encrypted[dict[str, object]]] = mapped_column(EncryptedJson)
    created_by: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConnectorCall(TenantTableMixin, Base):
    __tablename__: str = "connector_call"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "connector_call",
        Index("ix_connector_call_tenant_day", "tenant_id", "created_at"),
    )

    connector: Mapped[str] = mapped_column(Text)
    check_type: Mapped[CheckType] = mapped_column(enum_column(CheckType))
    mode: Mapped[ConnectorMode] = mapped_column(enum_column(ConnectorMode))
    route: Mapped[ConnectorRoute] = mapped_column(enum_column(ConnectorRoute))
    outcome: Mapped[CallOutcome] = mapped_column(enum_column(CallOutcome))
    duration_ms: Mapped[int] = mapped_column(Integer)
    cost_micros: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
    evidence_key: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    subject_party_id: Mapped[UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
