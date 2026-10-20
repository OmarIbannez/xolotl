"""The canonical transaction, its field-level validation and the enriched view.

One Pydantic model serves the API batch, each file row and the decide request
(SP-04 spec §4, ``FR-TM-001``). ``parse_item`` turns one submitted item into a
``TransactionInput`` or the list of its ``FieldError``s; ``validate_batch`` adds
the in-batch duplicate check. ``EnrichedTransaction`` is what enrichment hands
the aggregates and the evaluator.

Pure: no database, no web framework and no other module except the party
module's value types and the counterparty input (contract
``monitoring-core-is-pure``).
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
)

from xolotl.modules.party.schemas import CountryCode
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyInput,
    CounterpartyScreeningStatus,
)

_CENT = Decimal("0.01")


class Direction(StrEnum):
    IN = "IN"
    OUT = "OUT"
    INTERNAL = "INTERNAL"


class Rail(StrEnum):
    SPEI = "SPEI"
    CODI = "CODI"
    CASH = "CASH"
    CARD = "CARD"
    WALLET = "WALLET"
    SWIFT = "SWIFT"
    CRYPTO_ONCHAIN = "CRYPTO_ONCHAIN"
    OTHER = "OTHER"


class Instrument(StrEnum):
    CASH = "CASH"
    TRANSFER = "TRANSFER"
    CARD = "CARD"
    CHEQUE = "CHEQUE"
    CRYPTO = "CRYPTO"
    FX = "FX"
    FEE = "FEE"


class Channel(StrEnum):
    APP = "APP"
    WEB = "WEB"
    API = "API"
    BRANCH = "BRANCH"
    ATM = "ATM"
    AGENT = "AGENT"
    OTHER = "OTHER"


class KeyKind(StrEnum):
    """What an aggregate is kept per."""

    PARTY = "PARTY"
    RELATIONSHIP = "RELATIONSHIP"
    ACCOUNT = "ACCOUNT"
    COUNTERPARTY = "COUNTERPARTY"
    DEVICE = "DEVICE"


# The currencies the unit conversion can value (plan 4a Deviation 6).
SUPPORTED_CURRENCIES: frozenset[str] = frozenset({"MXN", "USD"})


class CryptoContext(BaseModel):
    """``address`` is the counterparty's wallet when the counterparty names none."""

    network: str
    tx_hash: str | None = None
    address: str | None = None
    self_custody: bool | None = None
    asset_code: str
    asset_qty: Decimal = Field(gt=0)


class FxContext(BaseModel):
    from_ccy: str
    to_ccy: str
    rate: Decimal = Field(gt=0)
    from_amount_minor: int
    to_amount_minor: int


class DeviceContext(BaseModel):
    device_id: str | None = None
    ip: str | None = None
    geo_country: CountryCode | None = None
    geo_admin_area: str | None = None


class TransactionInput(BaseModel):
    """One submitted transaction; ``raw`` carries whatever else the client sent.

    ``occurred_at`` is kept in UTC and the amounts with exactly two decimal
    places, so two spellings of one transaction share a content hash.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=128)
    occurred_at: AwareDatetime
    customer_ref: str
    account_ref: str | None = None
    direction: Direction
    rail: Rail
    instrument: Instrument
    amount: Decimal = Field(gt=0, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    counterparty: CounterpartyInput | None = None
    channel: Channel = Channel.OTHER
    device: DeviceContext | None = None
    crypto: CryptoContext | None = None
    fx: FxContext | None = None
    purpose_code: str | None = None
    fee_amount: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    raw: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])

    @field_validator("occurred_at")
    @classmethod
    def _in_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("amount", "fee_amount")
    @classmethod
    def _in_cents(cls, value: Decimal | None) -> Decimal | None:
        return None if value is None else value.quantize(_CENT)


class BatchRequest(BaseModel):
    """The body of ``POST /v1/transactions``; the route checks the item maximum."""

    transactions: list[TransactionInput] = Field(min_length=1)


class FieldErrorCode(StrEnum):
    REQUIRED = "REQUIRED"
    INVALID = "INVALID"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    CUSTOMER_REF_UNKNOWN = "CUSTOMER_REF_UNKNOWN"
    ACCOUNT_REF_CONFLICT = "ACCOUNT_REF_CONFLICT"
    DUPLICATE_IN_BATCH = "DUPLICATE_IN_BATCH"
    FUTURE = "FUTURE"
    NAIVE_DATETIME = "NAIVE_DATETIME"
    CONTEXT_REQUIRED = "CONTEXT_REQUIRED"
    COUNTERPARTY_EMPTY = "COUNTERPARTY_EMPTY"
    TOO_LONG = "TOO_LONG"


class FieldError(BaseModel):
    """One rejected field: the item's index, its dotted path and why; no value."""

    index: int
    field: str
    code: FieldErrorCode


_CODE_BY_TYPE: dict[str, FieldErrorCode] = {
    "missing": FieldErrorCode.REQUIRED,
    "string_too_long": FieldErrorCode.TOO_LONG,
    "greater_than": FieldErrorCode.OUT_OF_RANGE,
    "greater_than_equal": FieldErrorCode.OUT_OF_RANGE,
    "less_than": FieldErrorCode.OUT_OF_RANGE,
    "less_than_equal": FieldErrorCode.OUT_OF_RANGE,
    "decimal_max_digits": FieldErrorCode.OUT_OF_RANGE,
    "decimal_max_places": FieldErrorCode.OUT_OF_RANGE,
    "decimal_whole_digits": FieldErrorCode.OUT_OF_RANGE,
    "timezone_aware": FieldErrorCode.NAIVE_DATETIME,
}


def _code_of(error_type: str, message: str) -> FieldErrorCode:
    if error_type == "value_error" and message.endswith("COUNTERPARTY_EMPTY"):
        return FieldErrorCode.COUNTERPARTY_EMPTY
    return _CODE_BY_TYPE.get(error_type, FieldErrorCode.INVALID)


def _rule_errors(
    index: int, item: TransactionInput, *, now: datetime, max_future_minutes: int
) -> list[FieldError]:
    found: list[tuple[str, FieldErrorCode]] = []
    if item.occurred_at > now + timedelta(minutes=max_future_minutes):
        found.append(("occurred_at", FieldErrorCode.FUTURE))
    if item.currency not in SUPPORTED_CURRENCIES:
        found.append(("currency", FieldErrorCode.INVALID))
    if item.instrument is Instrument.CRYPTO and item.crypto is None:
        found.append(("crypto", FieldErrorCode.CONTEXT_REQUIRED))
    if item.instrument is Instrument.FX and item.fx is None:
        found.append(("fx", FieldErrorCode.CONTEXT_REQUIRED))
    if item.rail is Rail.CRYPTO_ONCHAIN and (
        item.crypto is None or not item.crypto.network.strip()
    ):
        found.append(("crypto.network", FieldErrorCode.CONTEXT_REQUIRED))
    return [FieldError(index=index, field=f, code=c) for f, c in found]


def parse_item(
    index: int,
    data: Mapping[str, object],
    *,
    now: datetime,
    max_future_minutes: int,
) -> TransactionInput | list[FieldError]:
    """Validate one submitted item.

    Args:
        index: The item's position in its batch or file page, echoed in errors.
        data: The item as decoded from JSON or read from a file row.
        now: The receiving instant (aware).
        max_future_minutes: How far ahead of ``now`` ``occurred_at`` may be.

    Returns:
        The validated input, or every field error found, sorted by field. The
        schema's errors come first; the cross-field rules (future, currency,
        context) run only on an item the schema accepts.

    """
    try:
        item = TransactionInput.model_validate(data)
    except ValidationError as exc:
        errors = [
            FieldError(
                index=index,
                field=".".join(str(part) for part in error["loc"]),
                code=_code_of(error["type"], error["msg"]),
            )
            for error in exc.errors()
        ]
        return sorted(errors, key=lambda error: error.field)
    errors = _rule_errors(index, item, now=now, max_future_minutes=max_future_minutes)
    return sorted(errors, key=lambda error: error.field) if errors else item


def validate_batch(
    items: Sequence[Mapping[str, object]],
    *,
    now: datetime,
    max_future_minutes: int,
) -> tuple[list[TransactionInput], list[FieldError]]:
    """Validate a batch: every item, then ``external_id`` unique within it.

    Args:
        items: The submitted items, in order.
        now: The receiving instant (aware).
        max_future_minutes: How far ahead of ``now`` ``occurred_at`` may be.

    Returns:
        The items that passed, in order, and every error sorted by index then
        field. A repeated ``external_id`` is reported on the later item, which is
        then left out of the accepted list.

    """
    accepted: list[TransactionInput] = []
    errors: list[FieldError] = []
    seen: set[str] = set()
    for index, data in enumerate(items):
        result = parse_item(index, data, now=now, max_future_minutes=max_future_minutes)
        external_id = data.get("external_id")
        duplicate = isinstance(external_id, str) and external_id in seen
        if isinstance(external_id, str):
            seen.add(external_id)
        if duplicate:
            errors.append(
                FieldError(
                    index=index,
                    field="external_id",
                    code=FieldErrorCode.DUPLICATE_IN_BATCH,
                )
            )
        if isinstance(result, TransactionInput):
            if not duplicate:
                accepted.append(result)
        else:
            errors.extend(result)
    errors.sort(key=lambda error: (error.index, error.field))
    return accepted, errors


def content_hash(item: TransactionInput) -> bytes:
    """Hash the item's canonical JSON with SHA-256; idempotent re-sends compare it.

    Returns:
        The 32-byte digest.

    """
    canonical = json.dumps(
        item.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).digest()


def amount_minor(amount: Decimal) -> int:
    """Convert an amount to minor units (cents); the schema enforces two places.

    Returns:
        ``amount`` times 100.

    """
    return int(amount.scaleb(2))


class UnitAmount(BaseModel):
    """A transaction's amount in one threshold unit under one conversion rule."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    amount: Decimal
    unit_value_id: int | None
    resolved_date: date


class PeriodKeys(BaseModel):
    """The Mexico City periods a transaction falls in (``2027-02-03``, ``2027-Q1``)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    DAY: str
    MONTH: str
    QUARTER: str
    BUSINESS_DAY: bool


class EnrichedTransaction(BaseModel):
    """A transaction as enrichment leaves it: what aggregates and rules read.

    ``account_id`` is None only on the decide path, for an ``account_ref`` never
    seen (reconciliation finding 10); a stored transaction always has one.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    external_id: str
    occurred_at: datetime
    received_at: datetime
    party_id: UUID
    relationship_id: UUID
    account_id: UUID | None
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
    unit_amounts: dict[str, UnitAmount]
    counterparty_id: UUID | None
    counterparty_key: str | None
    counterparty_country: str | None
    counterparty_screening_status: CounterpartyScreeningStatus | None
    device_key: str | None
    geo_country: str | None
    geo_admin_area: str | None
    crypto: dict[str, JsonValue] | None
    fx: dict[str, JsonValue] | None
    purpose_code: str | None
    period_keys: PeriodKeys
    tier: str | None
    risk_rating: str | None
    monitoring_profile: str
    policy_snapshot_id: UUID
    policy_country: str
    late: bool

    def key_for(self, kind: KeyKind) -> str | None:
        """Return the aggregate key of this transaction for ``kind``.

        Returns:
            The id as text for a party, relationship or account, the hex blind
            index for a counterparty or device, or None when there is none.

        """
        match kind:
            case KeyKind.PARTY:
                return str(self.party_id)
            case KeyKind.RELATIONSHIP:
                return str(self.relationship_id)
            case KeyKind.ACCOUNT:
                return None if self.account_id is None else str(self.account_id)
            case KeyKind.COUNTERPARTY:
                return self.counterparty_key
            case KeyKind.DEVICE:
                return self.device_key

    def unit_amount(self, unit_key: str) -> Decimal | None:
        """Return the amount under ``unit_key`` (``"UDI/SAME_DAY"``).

        Returns:
            The amount, or None when enrichment recorded no such unit.

        """
        found = self.unit_amounts.get(unit_key)
        return None if found is None else found.amount
