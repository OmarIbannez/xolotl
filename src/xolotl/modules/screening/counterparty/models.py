"""The ``counterparty`` table (SP-04 spec §5.2, Decision 18; migration 0012).

A counterparty is the other side of a transaction and a screening subject, so its
row is screening's although plan 4a's migration creates it. One row per distinct
counterparty: ``dedup_hash`` is the HMAC over the four component hashes, each a
blind index under the tenant's INDEX key. The name, account, wallet address and
identifier are ciphertext under the tenant's DATA key (the kernel convention, no
``_enc`` suffix). ``screening_status`` is the synchronous cache the decide path
reads (DR-308); ``screening_subject_id`` gains its foreign key in migration 0013.
``counterparty_name_index`` (0013) holds the normalized tokens of a counterparty's
name in plaintext, as ``party_name_index`` does (D7, PRV-301): no route serializes it.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
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

from xolotl.modules.party.enums import IdentifierType
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyKind,
    CounterpartyScreeningStatus,
)
from xolotl.platform import clock
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
        # Reverse blocking looks a delta entry's identifiers up here (0014).
        Index("ix_counterparty_identifier_hash", "tenant_id", "identifier_hash"),
        # screening_subject references this table too; the cycle is closed by ALTER.
        ForeignKeyConstraint(
            ["tenant_id", "screening_subject_id"],
            ["screening_subject.tenant_id", "screening_subject.id"],
            name="fk_counterparty_subject",
            use_alter=True,
        ),
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


class CounterpartyNameIndex(TenantTableMixin, Base):
    __tablename__: str = "counterparty_name_index"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "counterparty_name_index",
        ForeignKeyConstraint(
            ["tenant_id", "counterparty_id"],
            ["counterparty.tenant_id", "counterparty.id"],
            name="fk_counterparty_name_index_counterparty",
        ),
        UniqueConstraint("tenant_id", "counterparty_id"),
        Index("ix_cni_phonetic", "phonetic_keys", postgresql_using="gin"),
        Index(
            "ix_cni_trgm",
            "norm_name",
            postgresql_using="gin",
            postgresql_ops={"norm_name": "gin_trgm_ops"},
        ),
    )

    counterparty_id: Mapped[UUID] = mapped_column(Uuid)
    # Normalized tokens only (D7): never the name as the client sent it.
    norm_name: Mapped[str] = mapped_column(Text)
    tokens: Mapped[list[str]] = mapped_column(ARRAY(Text))
    phonetic_keys: Mapped[list[str]] = mapped_column(ARRAY(Text))
    matcher_version: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=clock.now
    )
