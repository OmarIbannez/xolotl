"""Request and response models of the screening API (SP-03 spec §12).

The lists models carry public-source facts and counts; a private-list entry is
plaintext and only ever built after the reveal has been audited. The screening
models come in two audiences: an API client gets status fields only (``CheckResponse``,
``RunResponse``, ``PartyScreeningResponse``), never a list code, score, entry or
finding (FR-SC-013); staff get the ``*Staff*`` subclasses with the explanations.
The check request lives in ``screening.api`` with the ad hoc subject it embeds, so
these models stay free of the party module (the list catalog imports them).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue

from xolotl.modules.screening.engine.enums import (
    AutoReason,
    BlockedReason,
    DecidedByType,
    Decision,
    HitBand,
    MatchStatus,
    RunMode,
    SubjectKind,
    SubjectStatus,
    SuggestedDisposition,
    TriggerType,
)
from xolotl.modules.screening.enums import (
    DeltaChange,
    ListKind,
    ListRequirement,
    VersionStatus,
)
from xolotl.modules.screening.lists import EntityType


class ListOverview(BaseModel):
    """A subscribed list with its active version and freshness."""

    code: str
    list_kind: ListKind
    title: str
    jurisdiction: str
    requirement: ListRequirement
    enabled: bool
    active_seq: int | None
    published_at: datetime | None
    fetched_at: datetime | None
    entry_count: int | None
    stale: bool
    failing: bool
    last_error: str | None
    last_success_at: datetime | None


class ListOverviewResponse(BaseModel):
    items: list[ListOverview]


class ListVersionResponse(BaseModel):
    id: UUID
    seq: int
    status: VersionStatus
    published_at: datetime | None
    source_version: str | None
    parser_version: str
    fetched_at: datetime
    loaded_by: str
    file_sha256: str
    file_size: int
    entry_count: int
    added: int
    changed: int
    removed: int


class DeltaRowResponse(BaseModel):
    source_entry_key: str
    change: DeltaChange
    prev_entry_id: UUID | None
    new_entry_id: UUID | None
    prev_name: str | None
    new_name: str | None


class ListEntryResponse(BaseModel):
    id: UUID
    list_code: str
    source_entry_key: str
    from_seq: int
    to_seq: int | None
    entry: dict[str, JsonValue]


class SubscriptionUpdate(BaseModel):
    enabled: bool


class PrivateListCreate(BaseModel):
    list_code: str = Field(max_length=64)
    title: str = Field(min_length=1, max_length=200)
    list_kind: ListKind | None = None


class PrivateListVersionResponse(BaseModel):
    id: UUID
    seq: int
    status: VersionStatus
    uploaded_by: str
    uploaded_at: datetime
    content_type: str
    file_size: int
    entry_count: int
    added: int
    changed: int
    removed: int


class PrivateListResponse(BaseModel):
    id: UUID
    list_code: str
    list_kind: ListKind
    title: str
    created_at: datetime
    active_version: PrivateListVersionResponse | None


class PrivateListEntryResponse(BaseModel):
    id: UUID
    source_entry_key: str
    entity_type: EntityType
    entry: dict[str, JsonValue]


_PII: dict[str, JsonValue] = {"pii": True}


class CheckResponse(BaseModel):
    """What an API client learns of a check: the run and the subject's status."""

    run_id: UUID
    subject_id: UUID
    status: SubjectStatus
    screened_at: datetime


class HitView(BaseModel):
    id: UUID
    list_code: str
    list_kind: str
    band: HitBand
    score: Decimal
    auto_reason: AutoReason | None
    match_id: UUID | None
    # The stored explanation (spec §8.4): normalized tokens, masked identifiers.
    features: dict[str, JsonValue]


class CheckStaffResponse(CheckResponse):
    hits: list[HitView]


class RunResponse(BaseModel):
    id: UUID
    subject_id: UUID
    party_id: UUID | None
    mode: RunMode
    outcome: SubjectStatus
    created_at: datetime


class ListSetMemberView(BaseModel):
    list_code: str
    list_version_id: UUID | None
    private_list_version_id: UUID | None


class RunStaffResponse(RunResponse):
    trigger_type: TriggerType
    trigger_ref: str | None
    list_set: list[ListSetMemberView]
    matcher_version: str
    # Hex.
    scoring_config_hash: str
    policy_snapshot_id: UUID
    candidate_count: int
    hit_count: int
    new_match_count: int
    latency_ms: int
    hits: list[HitView]


class PartyScreeningResponse(BaseModel):
    """A party's screening status; ``status`` is null until its first run."""

    party_id: UUID
    status: SubjectStatus | None
    last_screened_at: datetime | None
    lists_covered: list[str]


class MatchSummary(BaseModel):
    id: UUID
    subject_id: UUID
    party_id: UUID | None
    list_code: str
    list_kind: ListKind
    band: HitBand
    score: Decimal
    status: MatchStatus
    suggested_disposition: SuggestedDisposition | None
    created_at: datetime
    updated_at: datetime


class PartyScreeningStaffResponse(PartyScreeningResponse):
    open_finding_count: int
    findings: list[MatchSummary]


class MaskedSubject(BaseModel):
    kind: SubjectKind
    party_id: UUID | None
    entity_type: str
    initials: str
    # "<ID_TYPE> <masked value>" for each screened identifier.
    identifiers: list[str]


class DispositionView(BaseModel):
    id: UUID
    decision: Decision
    decided_by_type: DecidedByType
    decided_by: UUID | None
    decided_at: datetime


class HitHistoryItem(BaseModel):
    hit_id: UUID
    run_id: UUID
    created_at: datetime
    score: Decimal
    band: HitBand


class MatchDetail(MatchSummary):
    subject: MaskedSubject
    # The shared entry's ListEntry JSON (public data); None for a private list.
    entry: dict[str, JsonValue] | None
    origin_hit: HitView
    latest_hit: HitView
    history: list[HitHistoryItem]
    dispositions: list[DispositionView]
    whitelist_id: UUID | None
    allowed_actions: list[Decision]
    blocked_reasons: dict[Decision, list[BlockedReason]]


class RevealedSubjectIdentifier(BaseModel):
    id_type: str
    value: str = Field(json_schema_extra=_PII)


class MatchReveal(BaseModel):
    subject_names: list[str] = Field(json_schema_extra=_PII)
    birth_date: date | None = Field(json_schema_extra=_PII)
    identifiers: list[RevealedSubjectIdentifier]
    private_entry: dict[str, JsonValue] | None = Field(json_schema_extra=_PII)
    # Disposition id -> rationale.
    rationales: dict[UUID, str] = Field(json_schema_extra=_PII)


class DispositionRequest(BaseModel):
    decision: Literal["FALSE_POSITIVE", "POSSIBLE_TRUE_MATCH", "TRUE_MATCH", "DELISTED"]
    rationale: str = Field(min_length=10, max_length=2000)


class DispositionResponse(BaseModel):
    id: UUID
    match_id: UUID
    decision: Decision
    match_status: MatchStatus
    decided_at: datetime
