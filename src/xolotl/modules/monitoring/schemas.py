"""Request and response models of the monitoring API (SP-04 spec §15, plan 4a A10).

Two audiences read a transaction (D20). An API client gets
``TransactionClientView``: identity and status only, never a unit amount, a snapshot,
a counterparty or its screening status (tipping-off, spec §15). Staff get
``TransactionStaffView``, with the counterparty masked, and
``TransactionRevealedView`` only through an audited reveal. The counterparty types
are screening's (reconciliation finding 13). Plans 4b and 4c append their models.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from xolotl.modules.monitoring.decide.enums import FailMode
from xolotl.modules.monitoring.enums import (
    BatchSource,
    FileStatus,
    ParkReason,
    TransactionStatus,
)
from xolotl.modules.monitoring.txn.schema import (
    Channel,
    Direction,
    Instrument,
    PeriodKeys,
    Rail,
)
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
