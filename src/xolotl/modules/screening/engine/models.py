"""The screening engine's tables (SP-03 spec §5.2, second block; migration 0010).

``party_name_index`` holds the normalized tokens of each party's names in plaintext
(program spec D7, PRV-301): no route ever serializes it. A ``screening_subject`` is
one party, one counterparty (migration 0013), or one ad hoc query whose input is
ciphertext. A run records the exact list set it screened (``screening_list_set``
and its members), and every non-trivial score it produced (``screening_hit``). A
``screening_match`` is a finding: one per subject and entry content. Parties are referenced by table name, so this module does
not import the party models. The foreign keys whose conventional name would pass
Postgres's 63 bytes are named by hand.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.modules.screening.engine.enums import (
    AutoReason,
    BatchKind,
    BatchStatus,
    HitBand,
    MatchStatus,
    NameKind,
    RunMode,
    SubjectKind,
    SubjectStatus,
    SuggestedDisposition,
    TriggerType,
)
from xolotl.modules.screening.enums import ListKind
from xolotl.platform import clock
from xolotl.platform.crypto.encrypted import Encrypted, EncryptedJson
from xolotl.platform.db import Base
from xolotl.platform.sqltypes import enum_column
from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_fk, tenant_table_args

SUBJECT_KIND_CHECK = (
    "(kind = 'PARTY' AND party_id IS NOT NULL AND counterparty_id IS NULL "
    "AND input IS NULL) "
    "OR (kind = 'AD_HOC' AND party_id IS NULL AND counterparty_id IS NULL "
    "AND input IS NOT NULL) "
    "OR (kind = 'COUNTERPARTY' AND party_id IS NULL AND counterparty_id IS NOT NULL "
    "AND input IS NULL)"
)
ONE_ENTRY_CHECK = "(list_entry_id IS NULL) <> (private_list_entry_id IS NULL)"
ONE_VERSION_CHECK = "(list_version_id IS NULL) <> (private_list_version_id IS NULL)"


def _private_entry_fk(table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "private_list_entry_id"],
        ["private_list_entry.tenant_id", "private_list_entry.id"],
        name=f"fk_{table}_private_entry",
    )


class PartyNameIndex(TenantTableMixin, Base):
    __tablename__: str = "party_name_index"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "party_name_index",
        tenant_fk("party_id", "party"),
        UniqueConstraint("tenant_id", "party_id", "name_kind"),
        Index("ix_pni_phonetic", "phonetic_keys", postgresql_using="gin"),
        Index(
            "ix_pni_trgm",
            "norm_name",
            postgresql_using="gin",
            postgresql_ops={"norm_name": "gin_trgm_ops"},
        ),
    )

    party_id: Mapped[UUID] = mapped_column(Uuid)
    name_kind: Mapped[NameKind] = mapped_column(enum_column(NameKind))
    # Normalized tokens only (D7): never a name as the party wrote it.
    norm_name: Mapped[str] = mapped_column(Text)
    tokens: Mapped[list[str]] = mapped_column(ARRAY(Text))
    phonetic_keys: Mapped[list[str]] = mapped_column(ARRAY(Text))
    matcher_version: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=clock.now
    )


class ScreeningSubject(TenantTableMixin, Base):
    __tablename__: str = "screening_subject"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "screening_subject",
        tenant_fk("party_id", "party"),
        UniqueConstraint("tenant_id", "party_id"),
        # Migration 0013: a counterparty is a subject too (SP-04 spec §11.2).
        ForeignKeyConstraint(
            ["tenant_id", "counterparty_id"],
            ["counterparty.tenant_id", "counterparty.id"],
            name="fk_screening_subject_counterparty",
        ),
        UniqueConstraint(
            "tenant_id", "counterparty_id", name="uq_screening_subject_counterparty"
        ),
        CheckConstraint(SUBJECT_KIND_CHECK, name="kind_input"),
    )

    kind: Mapped[SubjectKind] = mapped_column(enum_column(SubjectKind))
    party_id: Mapped[UUID | None] = mapped_column(Uuid)
    counterparty_id: Mapped[UUID | None] = mapped_column(Uuid)
    # AD_HOC only: the NameQuery input, encrypted (spec input_enc, Deviation 1).
    input: Mapped[Encrypted[dict[str, object]] | None] = mapped_column(EncryptedJson)
    status: Mapped[SubjectStatus] = mapped_column(
        enum_column(SubjectStatus),
        default=SubjectStatus.CLEAR,
        server_default=SubjectStatus.CLEAR.value,
    )
    last_screened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_id: Mapped[UUID | None] = mapped_column(Uuid)
    retention_until: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ScreeningListSet(TenantTableMixin, Base):
    """The exact list versions a run used, shared by every run that used them."""

    __tablename__: str = "screening_list_set"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "screening_list_set", UniqueConstraint("tenant_id", "content_hash")
    )

    content_hash: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ScreeningListSetMember(Base):
    __tablename__: str = "screening_list_set_member"
    __table_args__: tuple[SchemaItem, ...] = (
        PrimaryKeyConstraint("tenant_id", "list_set_id", "list_code"),
        ForeignKeyConstraint(
            ["tenant_id", "list_set_id"],
            ["screening_list_set.tenant_id", "screening_list_set.id"],
            name="fk_screening_list_set_member_list_set",
        ),
        ForeignKeyConstraint(
            ["list_version_id"],
            ["list_version.id"],
            name="fk_screening_list_set_member_list_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "private_list_version_id"],
            ["private_list_version.tenant_id", "private_list_version.id"],
            name="fk_screening_list_set_member_private_version",
        ),
        CheckConstraint(ONE_VERSION_CHECK, name="one_version"),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid)
    list_set_id: Mapped[UUID] = mapped_column(Uuid)
    list_code: Mapped[str] = mapped_column(Text)
    list_version_id: Mapped[UUID | None] = mapped_column(Uuid)
    private_list_version_id: Mapped[UUID | None] = mapped_column(Uuid)


class ScreeningRun(TenantTableMixin, Base):
    __tablename__: str = "screening_run"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "screening_run",
        tenant_fk("subject_id", "screening_subject"),
        tenant_fk("list_set_id", "screening_list_set"),
        tenant_fk("policy_snapshot_id", "policy_snapshot"),
        Index(
            "ix_screening_run_subject",
            "tenant_id",
            "subject_id",
            text("created_at DESC"),
        ),
    )

    subject_id: Mapped[UUID] = mapped_column(Uuid)
    mode: Mapped[RunMode] = mapped_column(enum_column(RunMode))
    trigger_type: Mapped[TriggerType] = mapped_column(enum_column(TriggerType))
    # The request id, event id, job id or batch id.
    trigger_ref: Mapped[str | None] = mapped_column(Text)
    list_set_id: Mapped[UUID] = mapped_column(Uuid)
    policy_snapshot_id: Mapped[UUID] = mapped_column(Uuid)
    matcher_version: Mapped[str] = mapped_column(Text)
    scoring_config_hash: Mapped[bytes] = mapped_column(LargeBinary)
    query_fingerprint: Mapped[bytes] = mapped_column(LargeBinary)
    # LIST_DELTA and PRIVATE_LIST runs score only the batch's entries.
    restricted_to_delta: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    candidate_count: Mapped[int] = mapped_column(Integer)
    hit_count: Mapped[int] = mapped_column(Integer)
    new_match_count: Mapped[int] = mapped_column(Integer)
    # The subject's status after the run.
    outcome: Mapped[SubjectStatus] = mapped_column(enum_column(SubjectStatus))
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    batch_id: Mapped[UUID | None] = mapped_column(Uuid)
    # The workflow run (program spec D9); unused in SP-03.
    run_id: Mapped[UUID | None] = mapped_column(Uuid)


class ScreeningMatch(TenantTableMixin, Base):
    """A finding: one per subject and entry content."""

    __tablename__: str = "screening_match"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "screening_match",
        tenant_fk("subject_id", "screening_subject"),
        _private_entry_fk("screening_match"),
        tenant_fk("origin_run_id", "screening_run"),
        ForeignKeyConstraint(
            ["tenant_id", "superseded_by_match_id"],
            ["screening_match.tenant_id", "screening_match.id"],
            name="fk_screening_match_superseded_by",
        ),
        UniqueConstraint(
            "tenant_id",
            "subject_id",
            "list_code",
            "source_entry_key",
            "entry_content_hash",
            name="uq_screening_match_finding",
        ),
        CheckConstraint(ONE_ENTRY_CHECK, name="one_entry"),
        Index("ix_screening_match_queue", "tenant_id", "status", "band", "created_at"),
        Index("ix_screening_match_entry", "tenant_id", "list_code", "source_entry_key"),
    )

    subject_id: Mapped[UUID] = mapped_column(Uuid)
    list_code: Mapped[str] = mapped_column(Text)
    list_kind: Mapped[ListKind] = mapped_column(enum_column(ListKind))
    list_entry_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("list_entry.id")
    )
    private_list_entry_id: Mapped[UUID | None] = mapped_column(Uuid)
    source_entry_key: Mapped[str] = mapped_column(Text)
    # The entry's content_hash, or its content_tag for a private entry.
    entry_content_hash: Mapped[bytes] = mapped_column(LargeBinary)
    # At creation: ALERT or LOW_CONFIDENCE.
    band: Mapped[HitBand] = mapped_column(enum_column(HitBand))
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    origin_run_id: Mapped[UUID] = mapped_column(Uuid)
    status: Mapped[MatchStatus] = mapped_column(
        enum_column(MatchStatus),
        default=MatchStatus.OPEN,
        server_default=MatchStatus.OPEN.value,
    )
    # DELISTED when the entry is removed or becomes non-screenable.
    suggested_disposition: Mapped[SuggestedDisposition | None] = mapped_column(
        enum_column(SuggestedDisposition)
    )
    superseded_by_match_id: Mapped[UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=clock.now
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScreeningHit(TenantTableMixin, Base):
    __tablename__: str = "screening_hit"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "screening_hit",
        tenant_fk("run_id", "screening_run"),
        _private_entry_fk("screening_hit"),
        tenant_fk("match_id", "screening_match"),
        CheckConstraint(ONE_ENTRY_CHECK, name="one_entry"),
        Index("ix_screening_hit_match", "tenant_id", "match_id"),
        Index("ix_screening_hit_run", "tenant_id", "run_id"),
    )

    run_id: Mapped[UUID] = mapped_column(Uuid)
    list_code: Mapped[str] = mapped_column(Text)
    list_entry_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("list_entry.id")
    )
    private_list_entry_id: Mapped[UUID | None] = mapped_column(Uuid)
    source_entry_key: Mapped[str] = mapped_column(Text)
    entry_content_hash: Mapped[bytes] = mapped_column(LargeBinary)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    # BELOW_CUTOFF is counted on the run, never stored.
    band: Mapped[HitBand] = mapped_column(enum_column(HitBand))
    auto_reason: Mapped[AutoReason | None] = mapped_column(enum_column(AutoReason))
    # MatchExplanation.to_json() with the thresholds and the list kind (spec §8.4):
    # normalized tokens only.
    features: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    # The finding this hit raised or re-confirmed.
    match_id: Mapped[UUID | None] = mapped_column(Uuid)


class ScreeningBatch(TenantTableMixin, Base):
    __tablename__: str = "screening_batch"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "screening_batch", UniqueConstraint("tenant_id", "kind", "ref")
    )

    kind: Mapped[BatchKind] = mapped_column(enum_column(BatchKind))
    # A list_version id, a private_list_version id, or the period or matcher version.
    ref: Mapped[str] = mapped_column(Text)
    status: Mapped[BatchStatus] = mapped_column(enum_column(BatchStatus))
    total: Mapped[int | None] = mapped_column(Integer)
    done: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    runs: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    new_matches: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    cursor: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A code and a short reason only.
    last_error: Mapped[str | None] = mapped_column(Text)
