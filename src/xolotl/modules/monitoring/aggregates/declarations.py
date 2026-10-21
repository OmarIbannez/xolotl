"""Aggregate declarations, their ``agg_id`` and the one transaction filter (§8.2).

A declaration names what is kept per key (a function over a measure, a window and
a filter); two rules declaring the same aggregate under different names share
state, because ``agg_id`` hashes everything but the name. ``matches`` is the one
filter function: aggregates, rule triggers and captures all call it, and it only
ever sees literal country and region codes (4b resolves list names first,
reconciliation finding 3).

Pure: no database, no web framework and no other module (contract
``monitoring-core-is-pure``).
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from decimal import Decimal
from enum import StrEnum
from typing import Literal, cast

from pydantic import JsonValue, TypeAdapter

from xolotl.modules.monitoring.txn.schema import (
    Channel,
    Direction,
    EnrichedTransaction,
    Instrument,
    KeyKind,
    Rail,
)

# The scale of agg_bucket.value: a measure is kept to ten decimal places, so the
# memory store adds exactly what Postgres stores.
_SCALE = Decimal("1E-10")
_WINDOW = re.compile(r"^P(?:(\d+)M)?(?:(\d+)D)?(?:T(\d+)H)?$")
_HOURS_PER_DAY = 24
_HOURS_PER_MONTH = 30 * _HOURS_PER_DAY
# The longest window an aggregate keeps (spec §8.2): six months.
MAX_WINDOW_HOURS = 6 * _HOURS_PER_MONTH
ACCUM_WINDOW = "P6M"
_CENTS_PER_UNIT = 100

type DistinctOf = Literal["COUNTERPARTY", "DEVICE", "COUNTRY", "ACCOUNT"]


class AggregateFunction(StrEnum):
    SUM = "SUM"
    COUNT = "COUNT"
    AVG = "AVG"
    MAX = "MAX"
    MIN = "MIN"
    DISTINCT_COUNT = "DISTINCT_COUNT"
    FIRST_SEEN = "FIRST_SEEN"
    LAST_SEEN = "LAST_SEEN"


class WindowKind(StrEnum):
    DURATION = "DURATION"
    MONTH = "MONTH"
    QUARTER = "QUARTER"
    ACCUM = "ACCUM"


class Measure(StrEnum):
    AMOUNT_MINOR = "AMOUNT_MINOR"
    UNIT_AMOUNT = "UNIT_AMOUNT"
    FEE_MINOR = "FEE_MINOR"
    ASSET_QTY = "ASSET_QTY"
    ONE = "ONE"


@dataclass(frozen=True, slots=True)
class TxnFilter:
    """The ``where`` of a declaration: every field optional, all of them must hold.

    An empty set places no condition. The band (``amount_gte``, ``amount_lt``) is
    read in the unit ``amount_unit_key`` names (``"USD/PRIOR_BANKING_DAY"``).
    """

    direction: frozenset[Direction] = frozenset()
    instrument: frozenset[Instrument] = frozenset()
    rail: frozenset[Rail] = frozenset()
    channel: frozenset[Channel] = frozenset()
    currency: frozenset[str] = frozenset()
    counterparty_country_in: frozenset[str] = frozenset()
    counterparty_country_not_in: frozenset[str] = frozenset()
    counterparty_screening_status: frozenset[str] = frozenset()
    self_custody: bool | None = None
    cross_border: bool | None = None
    geo_admin_area_in: frozenset[str] = frozenset()
    amount_round_multiple: int | None = None
    amount_unit_key: str | None = None
    amount_gte: Decimal | None = None
    amount_lt: Decimal | None = None


def window_hours(window: str) -> int | None:
    """Measure a ``DURATION`` window (``PT1H``, ``P7D``, ``P1M``) in hours.

    A month counts 30 days, as the policy durations do.

    Returns:
        The length in hours, or None when ``window`` is not ``P[nM][nD][TnH]``
        with at least one part.

    """
    parts = window_parts(window)
    if parts is None:
        return None
    months, days, hours = parts
    return months * _HOURS_PER_MONTH + days * _HOURS_PER_DAY + hours


def window_parts(window: str) -> tuple[int, int, int] | None:
    """Split a ``DURATION`` window into months, days and hours.

    Returns:
        ``(months, days, hours)``, or None when ``window`` is not a duration.

    """
    match = _WINDOW.match(window)
    if match is None or not any(match.groups()):
        return None
    months, days, hours = (int(part or 0) for part in match.groups())
    return months, days, hours


def _canonical_value(value: object) -> JsonValue:
    if isinstance(value, frozenset):
        members: frozenset[object] = value
        spelled: list[JsonValue] = [str(member) for member in members]
        return sorted(spelled, key=str)
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, TxnFilter):
        return {
            f.name: _canonical_value(cast("object", getattr(value, f.name)))
            for f in fields(value)
        }
    if value is None or isinstance(value, bool | int | str):
        return value
    msg = f"no canonical form for {type(value).__name__}"
    raise TypeError(msg)


@dataclass(frozen=True, slots=True)
class AggregateDeclaration:
    """One aggregate kept per key; ``unit_key`` is ``"<unit>/<rule>"``."""

    name: str
    function: AggregateFunction
    measure: Measure
    unit_key: str | None
    key_kind: KeyKind
    window_kind: WindowKind
    window: str
    where: TxnFilter = field(default_factory=TxnFilter)
    distinct_of: DistinctOf | None = None
    # Set by 4b to the jurisdiction lists' hash once list names are resolved, so
    # a changed lists file yields a new agg_id (plan 4a Deviation 8).
    lists_hash: str | None = None

    def canonical_json(self) -> str:
        """Serialise every field but ``name`` in one canonical spelling.

        Keys are sorted, sets become sorted lists and decimals normalised strings,
        so two spellings of one declaration serialise alike.

        Returns:
            The compact JSON text.

        """
        data = {
            f.name: _canonical_value(cast("object", getattr(self, f.name)))
            for f in fields(self)
            if f.name != "name"
        }
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    def agg_id(self) -> str:
        """Identify the aggregate: two rules declaring the same one share state.

        Returns:
            The first 16 hex characters of the SHA-256 of ``canonical_json``.

        """
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()[:16]

    @classmethod
    def from_json(
        cls, name: str, data: Mapping[str, JsonValue]
    ) -> "AggregateDeclaration":
        """Rebuild a declaration from its canonical JSON (or any spelling of it).

        Data that describes no declaration raises pydantic's ``ValidationError``,
        a ``ValueError``.

        Returns:
            The declaration, named ``name``.

        """
        return _ADAPTER.validate_python({**data, "name": name})

    def validate(self) -> None:
        """Refuse a declaration spec §8.2 does not allow.

        Raises:
            ValueError: When ``UNIT_AMOUNT`` has no ``unit_key``, ``DISTINCT_COUNT``
                no ``distinct_of``, a band no ``amount_unit_key``, a ``DURATION``
                window is not between ``PT1H`` and ``P6M``, an ``ACCUM`` window is
                not ``P6M`` or a ``MONTH`` or ``QUARTER`` window is not its kind.

        """
        problem = _problem(self)
        if problem is not None:
            msg = f"aggregate {self.name!r}: {problem}"
            raise ValueError(msg)


def _window_problem(window_kind: WindowKind, window: str) -> str | None:
    match window_kind:
        case WindowKind.DURATION:
            hours = window_hours(window)
            if hours is None or not 1 <= hours <= MAX_WINDOW_HOURS:
                return f"a DURATION window runs from PT1H to P6M, not {window!r}"
        case WindowKind.ACCUM:
            if window != ACCUM_WINDOW:
                return f"an ACCUM window is {ACCUM_WINDOW}, not {window!r}"
        case WindowKind.MONTH | WindowKind.QUARTER:
            if window != window_kind.value:
                return f"a {window_kind} window is named {window_kind}, not {window!r}"
    return None


def _problem(declaration: AggregateDeclaration) -> str | None:
    where = declaration.where
    band = where.amount_gte is not None or where.amount_lt is not None
    problems = (
        (
            declaration.measure is Measure.UNIT_AMOUNT and declaration.unit_key is None,
            "UNIT_AMOUNT needs a unit_key",
        ),
        (
            declaration.function is AggregateFunction.DISTINCT_COUNT
            and declaration.distinct_of is None,
            "DISTINCT_COUNT needs distinct_of",
        ),
        (
            band and where.amount_unit_key is None,
            "an amount band needs amount_unit_key",
        ),
    )
    found = next((problem for failed, problem in problems if failed), None)
    return found or _window_problem(declaration.window_kind, declaration.window)


_ADAPTER = TypeAdapter(AggregateDeclaration)


def _cross_border(txn: EnrichedTransaction) -> bool:
    country = txn.counterparty_country
    foreign = country is not None and country != txn.policy_country
    return foreign or txn.rail is Rail.SWIFT


def _self_custody(txn: EnrichedTransaction) -> bool | None:
    flag = None if txn.crypto is None else txn.crypto.get("self_custody")
    return flag if isinstance(flag, bool) else None


def _in(values: frozenset[str], value: str | None) -> bool:
    return not values or (value is not None and value in values)


def _in_band(where: TxnFilter, txn: EnrichedTransaction) -> bool:
    if where.amount_gte is None and where.amount_lt is None:
        return True
    unit_key = where.amount_unit_key
    amount = None if unit_key is None else txn.unit_amount(unit_key)
    if amount is None:
        return False
    above = where.amount_gte is None or amount >= where.amount_gte
    below = where.amount_lt is None or amount < where.amount_lt
    return above and below


def matches(where: TxnFilter, txn: EnrichedTransaction) -> bool:
    """Tell whether ``txn`` passes every condition of ``where``.

    A transaction with no counterparty country passes neither country set; one
    with no ``crypto.self_custody`` passes neither ``self_custody`` value.
    ``cross_border`` means a counterparty country other than the policy's, or the
    ``SWIFT`` rail.

    Returns:
        True when every condition holds.

    """
    screening = txn.counterparty_screening_status
    country = txn.counterparty_country
    multiple = where.amount_round_multiple
    return (
        _in(where.direction, txn.direction)
        and _in(where.instrument, txn.instrument)
        and _in(where.rail, txn.rail)
        and _in(where.channel, txn.channel)
        and _in(where.currency, txn.currency)
        and _in(where.counterparty_country_in, country)
        and (
            not where.counterparty_country_not_in
            or (
                country is not None and country not in where.counterparty_country_not_in
            )
        )
        and _in(where.counterparty_screening_status, screening)
        and (where.self_custody is None or _self_custody(txn) is where.self_custody)
        and (where.cross_border is None or _cross_border(txn) is where.cross_border)
        and _in(where.geo_admin_area_in, txn.geo_admin_area)
        and (multiple is None or txn.amount_minor % (multiple * _CENTS_PER_UNIT) == 0)
        and _in_band(where, txn)
    )


def measure_of(
    declaration: AggregateDeclaration, txn: EnrichedTransaction
) -> Decimal | None:
    """Read the value ``txn`` adds to the declaration, at the bucket's scale.

    Returns:
        The measure, or None when the row lacks the unit amount, the fee or the
        asset quantity (the transaction then does not contribute).

    """
    match declaration.measure:
        case Measure.AMOUNT_MINOR:
            value: Decimal | None = Decimal(txn.amount_minor)
        case Measure.UNIT_AMOUNT:
            unit_key = declaration.unit_key
            value = None if unit_key is None else txn.unit_amount(unit_key)
        case Measure.FEE_MINOR:
            value = None if txn.fee_minor is None else Decimal(txn.fee_minor)
        case Measure.ASSET_QTY:
            value = txn.asset_qty
        case Measure.ONE:
            value = Decimal(1)
    return None if value is None else value.quantize(_SCALE)


def member_of(
    declaration: AggregateDeclaration, txn: EnrichedTransaction
) -> str | None:
    """Name the member a distinct count counts for ``txn``.

    Returns:
        The counterparty key, device key, counterparty country or account id, or
        None when the declaration counts none or the row has none.

    """
    match declaration.distinct_of:
        case "COUNTERPARTY":
            return txn.counterparty_key
        case "DEVICE":
            return txn.device_key
        case "COUNTRY":
            return txn.counterparty_country
        case "ACCOUNT":
            return None if txn.account_id is None else str(txn.account_id)
        case None:
            return None
