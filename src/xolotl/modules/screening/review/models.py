"""The review tables (SP-03 spec §5.2 and §9; migration 0010).

``match_disposition`` is append-only (``APPEND_ONLY_TABLES``): a decision is never
changed, only answered by a later one. Its rationale is ciphertext. A constraint
trigger, ``match_disposition_four_eyes``, refuses a row whose ``decided_by`` equals the
one of the disposition it answers (DR-305: two distinct people, in the database too).
``screening_whitelist`` suppresses one entry content for one subject until revoked.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    LargeBinary,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.modules.screening.engine.enums import DecidedByType, Decision
from xolotl.platform.crypto.encrypted import Encrypted, EncryptedStr
from xolotl.platform.db import Base
from xolotl.platform.sqltypes import enum_column
from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_fk, tenant_table_args


class MatchDisposition(TenantTableMixin, Base):
    __tablename__: str = "match_disposition"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "match_disposition",
        tenant_fk("match_id", "screening_match"),
        tenant_fk("decided_by", "staff_user"),
        ForeignKeyConstraint(
            ["tenant_id", "prior_disposition_id"],
            ["match_disposition.tenant_id", "match_disposition.id"],
            name="fk_match_disposition_prior",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_snapshot_id"],
            ["policy_snapshot.tenant_id", "policy_snapshot.id"],
            name="fk_match_disposition_policy_snapshot",
        ),
        CheckConstraint(
            "(decided_by_type = 'STAFF') = (decided_by IS NOT NULL)", name="decided_by"
        ),
        CheckConstraint(
            "decision <> 'TRUE_MATCH' OR prior_disposition_id IS NOT NULL",
            name="true_match_prior",
        ),
    )

    match_id: Mapped[UUID] = mapped_column(Uuid)
    decision: Mapped[Decision] = mapped_column(enum_column(Decision))
    # At least ten characters (spec rationale_enc, Deviation 1).
    rationale: Mapped[Encrypted[str]] = mapped_column(EncryptedStr)
    # SYSTEM only for SUPERSEDED.
    decided_by_type: Mapped[DecidedByType] = mapped_column(enum_column(DecidedByType))
    # staff_user.id; NULL for SYSTEM.
    decided_by: Mapped[UUID | None] = mapped_column(Uuid)
    # The escalation a TRUE_MATCH or second-review decision answers.
    prior_disposition_id: Mapped[UUID | None] = mapped_column(Uuid)
    policy_snapshot_id: Mapped[UUID] = mapped_column(Uuid)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ScreeningWhitelist(TenantTableMixin, Base):
    __tablename__: str = "screening_whitelist"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "screening_whitelist",
        tenant_fk("subject_id", "screening_subject"),
        ForeignKeyConstraint(
            ["tenant_id", "disposition_id"],
            ["match_disposition.tenant_id", "match_disposition.id"],
            name="fk_screening_whitelist_disposition",
        ),
        tenant_fk("revoked_by", "staff_user"),
        UniqueConstraint(
            "tenant_id",
            "subject_id",
            "list_code",
            "source_entry_key",
            "entry_content_hash",
            name="uq_screening_whitelist_key",
        ),
    )

    subject_id: Mapped[UUID] = mapped_column(Uuid)
    list_code: Mapped[str] = mapped_column(Text)
    source_entry_key: Mapped[str] = mapped_column(Text)
    entry_content_hash: Mapped[bytes] = mapped_column(LargeBinary)
    disposition_id: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[UUID | None] = mapped_column(Uuid)
