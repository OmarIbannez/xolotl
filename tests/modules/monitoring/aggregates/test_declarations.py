"""Declarations, ``agg_id``, the filter and the measure (spec §8.2)."""

import json
from dataclasses import replace
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import JsonValue

from tests.modules.monitoring.aggregates.scenarios import C1, T1, declaration, txn
from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    AggregateFunction,
    Measure,
    TxnFilter,
    WindowKind,
    matches,
    measure_of,
    member_of,
)
from xolotl.modules.monitoring.txn.schema import (
    Channel,
    Direction,
    EnrichedTransaction,
    Instrument,
    KeyKind,
    Rail,
)
from xolotl.modules.screening.counterparty.schema import CounterpartyScreeningStatus

_WHERE: dict[str, JsonValue] = {
    "direction": ["OUT", "IN"],
    "rail": ["SWIFT", "SPEI"],
    "counterparty_country_in": ["KY", "PA", "BS"],
    "amount_unit_key": "USD/PRIOR_BANKING_DAY",
    "amount_gte": "1000.00",
}
_DATA: dict[str, JsonValue] = {
    "function": "SUM",
    "measure": "UNIT_AMOUNT",
    "unit_key": "USD/PRIOR_BANKING_DAY",
    "key_kind": "PARTY",
    "window_kind": "DURATION",
    "window": "P30D",
}


def test_agg_id_ignores_the_name_and_is_order_independent():
    one = AggregateDeclaration.from_json("a", {**_DATA, "where": _WHERE})
    reordered: dict[str, JsonValue] = {
        **_WHERE,
        "direction": ["IN", "OUT"],
        "rail": ["SPEI", "SWIFT"],
        "counterparty_country_in": ["BS", "KY", "PA"],
        "amount_gte": "1000",
    }
    other = AggregateDeclaration.from_json("b", {**_DATA, "where": reordered})
    assert one.agg_id() == other.agg_id()
    assert len(one.agg_id()) == 16
    changed = replace(one, where=replace(one.where, rail=frozenset({Rail.SWIFT})))
    assert changed.agg_id() != one.agg_id()


def test_lists_hash_changes_the_id():
    one = AggregateDeclaration.from_json("a", {**_DATA, "where": _WHERE})
    assert replace(one, lists_hash="ab" * 32).agg_id() != one.agg_id()


def test_from_json_round_trips():
    one = AggregateDeclaration.from_json(
        "a",
        {
            **_DATA,
            "function": "DISTINCT_COUNT",
            "distinct_of": "COUNTRY",
            "where": _WHERE,
        },
    )
    again = AggregateDeclaration.from_json("a", json.loads(one.canonical_json()))
    assert again == one
    assert "name" not in json.loads(one.canonical_json())


def test_from_json_refuses_an_unknown_function():
    with pytest.raises(ValueError, match="function"):
        _ = AggregateDeclaration.from_json("a", {**_DATA, "function": "MEDIAN"})


_VALID = declaration(AggregateFunction.SUM)


@pytest.mark.parametrize(
    ("changed", "problem"),
    [
        ({"measure": Measure.UNIT_AMOUNT}, "unit_key"),
        ({"function": AggregateFunction.DISTINCT_COUNT}, "distinct_of"),
        ({"window": "PT0H"}, "DURATION"),
        ({"window": "P7M"}, "DURATION"),
        ({"window": "P1Y"}, "DURATION"),
        ({"window": "MONTH"}, "DURATION"),
        ({"window_kind": WindowKind.ACCUM, "window": "P3M"}, "ACCUM"),
        ({"window_kind": WindowKind.MONTH, "window": "QUARTER"}, "MONTH"),
        ({"window_kind": WindowKind.QUARTER, "window": "P3M"}, "QUARTER"),
        ({"where": TxnFilter(amount_gte=Decimal(1))}, "amount_unit_key"),
    ],
)
def test_validate_refuses_the_spec_violations(changed: dict[str, object], problem: str):
    _VALID.validate()
    with pytest.raises(ValueError, match=problem):
        replace(_VALID, **changed).validate()


_SWIFT = {"rail": Rail.SWIFT, "counterparty_country": None}
_FOREIGN = {"rail": Rail.SPEI, "counterparty_country": "US"}
_DOMESTIC = {"rail": Rail.SPEI, "counterparty_country": "MX"}
_CRYPTO = {"crypto": {"network": "bitcoin", "self_custody": True}}


@pytest.mark.parametrize(
    ("where", "passing", "failing"),
    [
        (
            TxnFilter(direction=frozenset({Direction.IN})),
            {},
            {"direction": Direction.OUT},
        ),
        (
            TxnFilter(instrument=frozenset({Instrument.CASH})),
            {"instrument": Instrument.CASH},
            {"instrument": Instrument.TRANSFER},
        ),
        (
            TxnFilter(rail=frozenset({Rail.CASH})),
            {"rail": Rail.CASH},
            {"rail": Rail.SPEI},
        ),
        (
            TxnFilter(channel=frozenset({Channel.ATM})),
            {"channel": Channel.ATM},
            {"channel": Channel.APP},
        ),
        (
            TxnFilter(currency=frozenset({"USD"})),
            {"currency": "USD"},
            {"currency": "MXN"},
        ),
        (
            TxnFilter(counterparty_country_in=frozenset({"KY"})),
            {"counterparty_country": "KY"},
            {"counterparty_country": None},
        ),
        (
            TxnFilter(counterparty_country_not_in=frozenset({"MX"})),
            {"counterparty_country": "US"},
            {"counterparty_country": "MX"},
        ),
        (
            TxnFilter(counterparty_country_not_in=frozenset({"MX"})),
            {"counterparty_country": "US"},
            {"counterparty_country": None},
        ),
        (
            TxnFilter(counterparty_screening_status=frozenset({"TRUE_MATCH"})),
            {"counterparty_screening_status": CounterpartyScreeningStatus.TRUE_MATCH},
            {"counterparty_screening_status": CounterpartyScreeningStatus.CLEAR},
        ),
        (TxnFilter(self_custody=True), _CRYPTO, {"crypto": None}),
        (
            TxnFilter(self_custody=False),
            {"crypto": {"self_custody": False}},
            _CRYPTO,
        ),
        (TxnFilter(cross_border=True), _SWIFT, _DOMESTIC),
        (TxnFilter(cross_border=True), _FOREIGN, _DOMESTIC),
        (TxnFilter(cross_border=False), _DOMESTIC, _FOREIGN),
        (
            TxnFilter(geo_admin_area_in=frozenset({"BCN"})),
            {"geo_admin_area": "BCN"},
            {"geo_admin_area": "CMX"},
        ),
        (
            TxnFilter(amount_round_multiple=1000),
            {"amount_minor": 500_000},
            {"amount_minor": 500_050},
        ),
        (
            TxnFilter(amount_unit_key="MXN/SAME_DAY", amount_gte=Decimal(100)),
            {"amount_minor": 10_000},
            {"amount_minor": 9_999},
        ),
        (
            TxnFilter(amount_unit_key="MXN/SAME_DAY", amount_lt=Decimal(100)),
            {"amount_minor": 9_999},
            {"amount_minor": 10_000},
        ),
        (
            TxnFilter(amount_unit_key="EUR/SAME_DAY", amount_lt=Decimal(100)),
            {"never": True},
            {"amount_minor": 1},
        ),
    ],
)
def test_matches_each_filter_field(
    where: TxnFilter, passing: dict[str, object], failing: dict[str, object]
):
    if "never" not in passing:
        assert matches(where, _priced(**passing))
    assert not matches(where, _priced(**failing))
    assert matches(TxnFilter(), _priced(**failing))


def _priced(**changes: object) -> EnrichedTransaction:
    """Build the base transaction with ``MXN/SAME_DAY`` equal to its amount.

    Returns:
        The transaction.

    """
    amount_minor = changes.pop("amount_minor", 10_000)
    assert isinstance(amount_minor, int)
    base = txn(T1, amount_minor, **changes)
    units = dict(base.unit_amounts)
    units["MXN/SAME_DAY"] = units["MXN/SAME_DAY"].model_copy(
        update={"amount": Decimal(amount_minor) / 100}
    )
    return base.model_copy(update={"unit_amounts": units})


def test_measure_and_member_per_kind():
    row = txn(
        T1,
        12_345,
        fee_minor=250,
        asset_qty=Decimal("0.5"),
        device_key="d1" * 32,
        counterparty_country="US",
        account_id=UUID(int=7),
    )
    measured = {
        measure: measure_of(
            replace(
                declaration(AggregateFunction.SUM),
                measure=measure,
                unit_key="UDI/SAME_DAY",
            ),
            row,
        )
        for measure in Measure
    }
    assert measured == {
        Measure.AMOUNT_MINOR: Decimal(12_345),
        Measure.UNIT_AMOUNT: row.unit_amount("UDI/SAME_DAY"),
        Measure.FEE_MINOR: Decimal(250),
        Measure.ASSET_QTY: Decimal("0.5"),
        Measure.ONE: Decimal(1),
    }
    bare = txn(T1, 1, fee_minor=None, asset_qty=None, unit_amounts={})
    for measure in (Measure.UNIT_AMOUNT, Measure.FEE_MINOR, Measure.ASSET_QTY):
        declared = replace(_VALID, measure=measure, unit_key="UDI/SAME_DAY")
        assert measure_of(declared, bare) is None
    members = {
        of: member_of(
            declaration(AggregateFunction.DISTINCT_COUNT, distinct_of=of), row
        )
        for of in ("COUNTERPARTY", "DEVICE", "COUNTRY", "ACCOUNT")
    }
    assert members == {
        "COUNTERPARTY": C1,
        "DEVICE": "d1" * 32,
        "COUNTRY": "US",
        "ACCOUNT": str(UUID(int=7)),
    }
    assert member_of(_VALID, row) is None
    assert row.key_for(KeyKind.DEVICE) == "d1" * 32
