"""The ``counterparty`` table (SP-04 spec §5.2, Decision 18; migration 0012).

A counterparty is the other side of a transaction and a screening subject, so its
row is screening's although plan 4a's migration creates it. One row per distinct
counterparty: ``dedup_hash`` is the HMAC over the four component hashes, each a
blind index under the tenant's INDEX key. The name, account, wallet address and
identifier are ciphertext under the tenant's DATA key (the kernel convention, no
``_enc`` suffix). ``screening_status`` is the synchronous cache the decide path
reads (DR-308); ``screening_subject_id`` gains its foreign key in migration 0013.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.modules.party.enums import IdentifierType
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyKind,
    CounterpartyScreeningStatus,
)
from xolotl.platform.crypto.encrypted import Encrypted, EncryptedStr
from xolotl.platform.db import Base
from xolotl.platform.sqltypes import enum_column
from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_table_args

ONE_COMPONENT_CHECK = (
    "name_hash IS NOT NULL OR account_hash IS NOT NULL "
    "OR wallet_hash IS NOT NULL OR identifier_hash IS NOT NULL"
)


class Counterparty(TenantTableMixin, Base):
    __tablename__: str = "counterparty"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "counterparty",
        UniqueConstraint("tenant_id", "dedup_hash"),
        CheckConstraint(ONE_COMPONENT_CHECK, name="one_component"),
        Index("ix_counterparty_name_hash", "tenant_id", "name_hash"),
        Index("ix_counterparty_wallet_hash", "tenant_id", "wallet_hash"),
    )

    kind: Mapped[CounterpartyKind] = mapped_column(enum_column(CounterpartyKind))
    name: Mapped[Encrypted[str] | None] = mapped_column(EncryptedStr)
    name_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    account: Mapped[Encrypted[str] | None] = mapped_column(EncryptedStr)
    account_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    institution: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(2))
    # The address keeps its case inside the ciphertext.
    wallet: Mapped[Encrypted[str] | None] = mapped_column(EncryptedStr)
    wallet_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    wallet_network: Mapped[str | None] = mapped_column(Text)
    identifier_type: Mapped[IdentifierType | None] = mapped_column(
        enum_column(IdentifierType)
    )
    identifier: Mapped[Encrypted[str] | None] = mapped_column(EncryptedStr)
    identifier_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    dedup_hash: Mapped[bytes] = mapped_column(LargeBinary)
    screening_status: Mapped[CounterpartyScreeningStatus] = mapped_column(
        enum_column(CounterpartyScreeningStatus),
        default=CounterpartyScreeningStatus.UNSCREENED,
        server_default=CounterpartyScreeningStatus.UNSCREENED.value,
    )
    screened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    screening_subject_id: Mapped[UUID | None] = mapped_column(Uuid)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    txn_count: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
