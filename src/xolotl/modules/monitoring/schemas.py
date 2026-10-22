"""Request and response models of the monitoring API (SP-04 spec §15, plan 4a A10).

Two audiences read a transaction (D20). An API client gets
``TransactionClientView``: identity and status only, never a unit amount, a snapshot,
a counterparty or its screening status (tipping-off, spec §15). Staff get
``TransactionStaffView``, with the counterparty masked, and
``TransactionRevealedView`` only through an audited reveal. The counterparty types
are screening's (reconciliation finding 13). Plans 4b and 4c append their models.

The rules models (plan 4b B9) serve both audiences too: an API client with
``rules:read`` reads templates, rules and simulations, but a simulation's
``sample`` and ``overlap`` say which customers a rule would hit, so the client's
copy carries neither (PRV-502), and only staff get ``allowed_actions``.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from xolotl.modules.monitoring.decide.enums import (
    DecisionMode,
    DecisionOutcome,
    FailMode,
)
from xolotl.modules.monitoring.enums import (
    BatchSource,
    FileStatus,
    ParkReason,
    TransactionStatus,
)
from xolotl.modules.monitoring.rules.definitions import (
    Dedup,
    HitOutcome,
    RuleFamily,
    RuleKind,
    SegmentFilter,
    Severity,
)
from xolotl.modules.monitoring.rules.enums import (
    HitTransactionRole,
    RuleAction,
    RuleBlockedReason,
    RuleVersionStatus,
    SimulationStatus,
)
from xolotl.modules.monitoring.rules.versions import VersionInput
from xolotl.modules.monitoring.txn.schema import (
    Channel,
    Direction,
    Instrument,
    KeyKind,
    PeriodKeys,
    Rail,
)
from xolotl.modules.party.schemas import PartyPiiResponse, PartyResponse
from xolotl.modules.screening.counterparty.schema import CounterpartyScreeningStatus
from xolotl.modules.screening.counterparty.schemas import (
    CounterpartySummary,
    RevealedCounterparty,
)
from xolotl.platform.units.rules import ConversionRule


class BatchAccepted(BaseModel):
    batch_id: UUID
    accepted: int
    duplicates: int
    transaction_ids: list[UUID]


class BatchSubmission(BaseModel):
    """The body of ``POST /v1/transactions``, items read as sent.

    The items are validated by ``validate_batch`` after the size check, so a bad item
    is a located ``TRANSACTION_BATCH_INVALID`` error rather than a framework one.
    """

    transactions: list[dict[str, JsonValue]] = Field(min_length=1)


class FileStatusResponse(BaseModel):
    file_id: UUID
    status: FileStatus
    rows_total: int | None
    rows_accepted: int
    rows_duplicate: int
    rows_rejected: int
    uploaded_at: datetime
    finished_at: datetime | None
    last_error: str | None


class BatchResponse(BaseModel):
    id: UUID
    source: BatchSource
    item_count: int
    accepted: int
    duplicates: int
    received_at: datetime
    request_id: str | None
    file_id: UUID | None


class TransactionClientView(BaseModel):
    """What an API client is shown of a transaction: identity and status."""

    id: UUID
    external_id: str
    occurred_at: datetime
    status: TransactionStatus
    late: bool


class TransactionQuery(BaseModel):
    """The filters of ``GET /v1/transactions``.

    ``from`` and ``to`` are Mexico City days, both included.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    party_id: UUID | None = None
    relationship_id: UUID | None = None
    external_id: str | None = Field(default=None, max_length=128)
    from_: date | None = Field(default=None, alias="from")
    to: date | None = None
    status: TransactionStatus | None = None
    instrument: Instrument | None = None
    rail: Rail | None = None
    late: bool | None = None


class UnitAmountView(BaseModel):
    key: str
    unit: str
    rule: ConversionRule
    amount: Decimal
    unit_value_id: int | None
    resolved_date: date


class ParkedInfo(BaseModel):
    reason: ParkReason
    unit_code: str | None
    needed_on: date | None
    attempts: int
    parked_at: datetime
    last_error: str | None


class TransactionStaffView(TransactionClientView):
    """A transaction for staff, the counterparty masked; ``parked`` when parked."""

    received_at: datetime
    batch_id: UUID
    party_id: UUID
    relationship_id: UUID
    account_id: UUID
    account_ref: str
    product: str | None
    direction: Direction
    rail: Rail
    instrument: Instrument
    channel: Channel
    amount_minor: int
    currency: str
    fee_minor: int | None
    asset_qty: Decimal | None
    asset_code: str | None
    unit_amounts: list[UnitAmountView]
    period_keys: PeriodKeys | None
    counterparty: CounterpartySummary | None
    counterparty_screening_status_at_processing: CounterpartyScreeningStatus | None
    device_present: bool
    geo_country: str | None
    geo_admin_area: str | None
    crypto: dict[str, JsonValue] | None
    fx: dict[str, JsonValue] | None
    purpose_code: str | None
    tier: str | None
    risk_rating: str | None
    monitoring_profile: str
    policy_snapshot_id: UUID | None
    processed_at: datetime | None
    decision_id: UUID | None
    parked: ParkedInfo | None


class TransactionRevealedView(TransactionStaffView):
    """The staff view plus the decrypted counterparty and payload, audited."""

    counterparty_revealed: RevealedCounterparty | None
    raw: dict[str, JsonValue]


class BackfillRequest(BaseModel):
    """Mexico City days, both included; ``party_id`` narrows it to one party."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", populate_by_name=True
    )

    from_: date = Field(alias="from")
    to: date
    party_id: UUID | None = None


class BackfillAccepted(BaseModel):
    job_id: UUID


class MonitoringStatus(BaseModel):
    """The OPS-501 counters of a tenant's processing.

    ``received_by_age`` counts ``RECEIVED`` rows by time since receipt in disjoint
    bands: ``lt_1m``, ``lt_10m`` (1 to 10 minutes), ``lt_1h`` and ``gte_1h``.
    ``partitions_ahead`` is, per partitioned table, how many consecutive months
    after the current UTC month have their partition.
    """

    received_by_age: dict[str, int]
    parked: int
    backfill: int
    oldest_received_at: datetime | None
    running_process_jobs: int
    partitions_ahead: dict[str, int]


class RailSettingResponse(BaseModel):
    """A rail's fail mode; ``explicit`` is false when it is the configured default."""

    rail: Rail
    fail_mode: FailMode
    explicit: bool
    changed_by: UUID | None
    updated_at: datetime | None


class RailSettingRequest(BaseModel):
    """The body of ``PUT /v1/monitoring/rails/{rail}``."""

    fail_mode: FailMode


class DecisionReason(BaseModel):
    """Why a decision is not ``ALLOW``, as an API client may read it (§21 Q5).

    ``code`` is ``TIER_LIMIT`` (with ``tier`` and ``limit_code``), ``RISK_REVIEW``
    or ``DECIDE_UNAVAILABLE``; never a rule, a hit or a screening status (D20).
    """

    code: str
    tier: str | None = None
    limit_code: str | None = None


class DecideResponse(BaseModel):
    """The answer of ``POST /v1/transactions/decide`` (spec §12.1)."""

    decision: DecisionOutcome
    decision_id: UUID
    reasons: list[DecisionReason]
    latency_ms: int


class DecisionSummary(BaseModel):
    """A decision in one line: what was answered, how and when."""

    decision_id: UUID
    decision: DecisionOutcome
    mode: DecisionMode
    decided_at: datetime


class DecisionClientResponse(DecisionSummary):
    """A decision as its API client reads it: the answer and the mode, no reason."""

    external_id: str


class DecisionTransaction(BaseModel):
    """The transaction a decision was linked to when it arrived through ingest."""

    id: UUID
    occurred_at: datetime
    status: TransactionStatus


class DecisionStaffResponse(DecisionClientResponse):
    """A decision with what it read, for staff only.

    The rules that fired with their aggregates, the rule-set hash, the timings and
    the linked transaction.
    """

    rail: Rail
    relationship_id: UUID
    account_id: UUID | None
    reasons: list[dict[str, JsonValue]]
    ruleset_hash: str
    policy_snapshot_id: UUID | None
    latency_ms: int
    db_ms: int
    actor: str
    transaction: DecisionTransaction | None


class HitQuery(BaseModel):
    """The filters of ``GET /v1/monitoring/hits`` (plan 4b B12).

    ``transaction_id`` finds the hits a transaction is linked to in any role;
    ``created_before`` ages the queue.
    """

    severity: Severity | None = None
    rule_code: str | None = Field(default=None, max_length=64)
    typology: str | None = Field(default=None, max_length=64)
    outcome: HitOutcome | None = None
    party_id: UUID | None = None
    transaction_id: UUID | None = None
    created_before: datetime | None = None


class HitSummary(BaseModel):
    """A rule hit in the staff queue, its party masked (spec §9.6; staff only).

    ``decision_id`` is the triggering transaction's decision, if it had one.
    """

    id: UUID
    rule_code: str
    rule_version_id: UUID
    typology: list[str]
    severity: Severity
    outcome: HitOutcome
    key_kind: KeyKind
    key: str
    party: PartyResponse
    relationship_id: UUID
    window_start: datetime
    window_end: datetime
    fired_count: int
    cooldown_until: datetime | None
    first_transaction_id: UUID
    first_occurred_at: datetime
    decision_id: UUID | None
    created_at: datetime


class EvidenceAggregate(BaseModel):
    """An aggregate's value when the rule fired, in its unit and window."""

    value: str
    unit: str | None
    window: str | None


class EvidenceView(BaseModel):
    """What the rule read: aggregates, parameters and the rendered explanation."""

    aggregates: dict[str, EvidenceAggregate]
    params: dict[str, str]
    explanation: str
    lists_hash: str


class HitTransactionView(BaseModel):
    """A transaction behind a hit, its counterparty masked."""

    transaction_id: UUID
    occurred_at: datetime
    role: HitTransactionRole
    direction: Direction
    instrument: Instrument
    rail: Rail
    amount_minor: int
    currency: str
    counterparty: CounterpartySummary | None


class HitDetail(HitSummary):
    """A hit with its evidence and provenance.

    ``transactions`` holds the newest 500 links, newest first; ``transaction_count``
    counts every link, and ``GET /v1/monitoring/hits/{id}/transactions`` pages them.
    ``decision`` is the triggering transaction's decision (plan 4c C5), if any.
    """

    evidence: EvidenceView
    transactions: list[HitTransactionView]
    transaction_count: int
    assignment_id: UUID
    policy_snapshot_id: UUID
    definition_hash: str
    dedup_key: str
    sentence: str
    decision: DecisionSummary | None


class HitReveal(BaseModel):
    """A hit's party and counterparties in plaintext, audited as ``VIEW_PII``.

    ``counterparties`` holds each counterparty of the hit's transactions by id.
    """

    party: PartyPiiResponse
    counterparties: dict[UUID, RevealedCounterparty]


class BehaviourProfileResponse(BaseModel):
    """One month of a relationship's behaviour profile (staff only, spec §13.3).

    ``metrics`` holds MXN amounts as strings of minor units, counts and shares;
    ``deviation`` the actual-over-declared ratios, null without a declaration.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

    month: str
    metrics: dict[str, JsonValue]
    declared_profile: dict[str, JsonValue] | None
    deviation: dict[str, JsonValue] | None
    deviated: bool
    computed_at: datetime


class ParamView(BaseModel):
    """A rule parameter: its value and the bounds a tenant may move it within.

    Values and bounds are spelled as text (decimals exactly, booleans ``true`` or
    ``false``); ``source`` names where the bounds come from, ``template <code>
    v<n>`` or, for a custom rule, ``rule <code> v<n>``.
    """

    name: str
    value: str
    type: Literal["DECIMAL", "INT", "STR", "BOOL"]
    min: str | None
    max: str | None
    choices: list[str] | None
    tighten: Literal["LOWER", "RAISE"] | None
    unit: str | None
    source: str


class TemplateResponse(BaseModel):
    """The latest version of a library template (spec §9.3).

    ``sentences`` holds the rendered sentence by locale (``es-MX``, ``en``).
    """

    code: str
    family: RuleFamily
    kind: RuleKind
    title: str
    typology: list[str]
    country_code: str
    packs: list[str]
    version: int
    params: list[ParamView]
    definition: dict[str, JsonValue]
    sentences: dict[str, str]


class SimulationResponse(BaseModel):
    """A simulation of a rule version and its tallies (spec §13.1).

    ``valid_until`` is the last Mexico City day on which a completed simulation
    still counts for submission and approval: it counts until ``finished_at``
    plus ``MONITORING_SIMULATION_MAX_AGE_DAYS``, which falls on that day.
    ``notes`` are codes of what the simulation could not see
    (``PROFILE_HISTORY_SHORT``: a range under about four months never fires a
    profile rule's history branch).

    Only staff read a simulation itself (spec §15). A rule read by an API client
    shows its simulations' status and dates only: every tally, the sample, the
    overlap and the requester are null, since even a count can tell which
    customer a rule would hit (tipping-off, PRV-502).
    """

    id: UUID
    rule_version_id: UUID
    status: SimulationStatus
    range_start: date
    range_end: date
    definition_hash: str
    transactions_read: int | None
    hit_count: int | None
    customers_hit: int | None
    hits_per_week: list[dict[str, JsonValue]] | None
    overlap: dict[str, int] | None
    sample: list[dict[str, JsonValue]] | None
    requested_by: UUID | None
    started_at: datetime | None
    finished_at: datetime | None
    last_error: str | None
    valid_until: date | None
    notes: list[str]


class RuleAssignmentResponse(BaseModel):
    """Where and from when a rule version applies (spec §9.5)."""

    id: UUID
    rule_version_id: UUID
    rule_code: str
    product: str | None
    segment_filter: dict[str, list[str]] | None
    effective_from: date
    effective_to: date | None
    # Null for an API client: which staff member approved is staff business.
    approved_by: UUID | None


class VersionResponse(BaseModel):
    """A rule version under the D19 lifecycle (spec §9.4).

    ``simulation`` is the version's latest simulation, whatever its status;
    ``allowed_actions`` and ``blocked_reasons`` are for the caller. An API client
    gets no actions and no staff user ids (``author_id``, ``approved_by``).
    """

    id: UUID
    rule_id: UUID
    version: int
    status: RuleVersionStatus
    definition_hash: str
    sync: bool
    params: list[ParamView]
    segment: SegmentFilter | None
    severity: Severity
    dedup: Dedup
    sentence: str
    author_id: UUID | None
    submitted_at: datetime | None
    approved_by: UUID | None
    approved_at: datetime | None
    simulation: SimulationResponse | None
    assignment: RuleAssignmentResponse | None
    activated_at: datetime | None
    retired_at: datetime | None
    retired_by_version_id: UUID | None
    allowed_actions: list[RuleAction]
    blocked_reasons: dict[RuleAction, list[RuleBlockedReason]]
    created_at: datetime


class RuleResponse(BaseModel):
    """A tenant rule with every version, oldest first.

    ``created_by`` is null for an API client.
    """

    id: UUID
    code: str
    family: RuleFamily
    kind: RuleKind
    template_code: str | None
    title: str
    typology: list[str]
    created_by: UUID | None
    created_at: datetime
    versions: list[VersionResponse]
    active_version_id: UUID | None
    last_simulation: SimulationResponse | None


class RuleQuery(BaseModel):
    """The filters of ``GET /v1/monitoring/rules``.

    ``status`` finds the rules with any version in that status.
    """

    kind: RuleKind | None = None
    typology: str | None = Field(default=None, max_length=64)
    status: RuleVersionStatus | None = None


class VersionRequest(VersionInput):
    """What a new or edited version tunes; a field left out keeps its value."""


class RuleCreateRequest(VersionRequest):
    """A rule from a library template, or a custom rule from a whole definition.

    Exactly one of ``template_code`` and ``definition``; a custom definition
    carries its own parameters, segment, severity and dedup, so none of those
    may come with it.
    """

    template_code: str | None = Field(default=None, max_length=64)
    definition: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        if (self.template_code is None) == (self.definition is None):
            msg = "give exactly one of template_code and definition"
            raise ValueError(msg)
        tuned = (self.params, self.segment, self.severity, self.dedup)
        if self.definition is not None and any(item is not None for item in tuned):
            msg = (
                "a custom definition carries its own params, segment, severity "
                "and dedup"
            )
            raise ValueError(msg)
        return self


class SimulateRequest(BaseModel):
    """Mexico City days, both included."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    range_start: date
    range_end: date


class SimulateAccepted(BaseModel):
    simulation_id: UUID


class ActivateRequest(BaseModel):
    """``product`` null applies the version to every product."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    effective_from: date
    product: str | None = Field(default=None, max_length=64)


class RejectRequest(BaseModel):
    """The reason is required but its text is kept nowhere (plan 4b Deviation 18)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)
