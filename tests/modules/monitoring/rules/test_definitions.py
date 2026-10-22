"""Typed rule definitions, their hash, bounds and list-name resolution (§9.1)."""

import json
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from tests.modules.monitoring.rules.samples import definition, document
from xolotl.modules.monitoring.aggregates.declarations import TxnFilter
from xolotl.modules.monitoring.rules.definitions import (
    REQUIRED_PARAMS,
    Dedup,
    HitOutcome,
    Param,
    RuleDefinition,
    RuleKind,
    canonical_json,
    resolve_definition,
    resolve_list_names,
)
from xolotl.modules.monitoring.txn.schema import Direction
from xolotl.platform.audit import chain
from xolotl.platform.policy import load_jurisdictions


def _validate(data: dict[str, Any]) -> RuleDefinition:
    return RuleDefinition.model_validate(data)


def test_a_definition_hash_is_stable_and_changes_with_a_param() -> None:
    first = definition(RuleKind.STRUCTURING)
    again = definition(RuleKind.STRUCTURING)
    assert first.definition_hash() == again.definition_hash()
    assert len(first.definition_hash()) == 32
    reordered = dict(reversed(list(document(RuleKind.STRUCTURING).items())))
    reordered["params"] = dict(reversed(list(reordered["params"].items())))
    assert _validate(reordered).definition_hash() == first.definition_hash()
    lower = first.with_params({"threshold": Decimal(4999)})
    assert lower.definition_hash() != first.definition_hash()
    # The stored JSON reloads to the same definition and the same hash.
    stored = json.loads(json.dumps(first.model_dump(mode="json")))
    assert _validate(stored) == first
    assert _validate(stored).definition_hash() == first.definition_hash()


def test_params_are_strict_numbers() -> None:
    rule = definition(RuleKind.STRUCTURING)
    assert rule.params["threshold"].value == Decimal(5000)
    assert type(rule.params["threshold"].value) is Decimal
    assert type(rule.params["min_count"].value) is int
    assert type(rule.params["window"].value) is str
    data = document(RuleKind.STRUCTURING)
    data["params"]["near_ratio"]["value"] = 0.75
    with pytest.raises(ValidationError, match="float"):
        _ = _validate(data)
    data = document(RuleKind.STRUCTURING)
    data["params"]["min_count"] = {"value": "3"}
    with pytest.raises(ValidationError, match="min_count"):
        _ = _validate(data)
    data = document(RuleKind.THRESHOLD)
    data["params"]["flag"] = {"value": True}
    data["params"]["threshold"] = Param[Decimal](value=Decimal(2000))
    rule = _validate(data)
    assert rule.params["flag"].value is True
    assert rule.params["threshold"].value == Decimal(2000)
    data["params"]["flag"] = "yes"
    with pytest.raises(ValidationError, match="valid dictionary"):
        _ = _validate(data)


def test_a_param_default_outside_its_bounds_is_refused() -> None:
    data = document(RuleKind.STRUCTURING)
    data["params"]["threshold"]["value"] = "900"
    with pytest.raises(ValidationError, match="threshold"):
        _ = _validate(data)
    data = document(RuleKind.STRUCTURING)
    data["params"]["near_ratio"]["value"] = "0.99"
    with pytest.raises(ValidationError, match="maximum"):
        _ = _validate(data)
    data = document(RuleKind.TIER_LIMIT)
    data["params"]["outcome"]["value"] = "ALERT"
    with pytest.raises(ValidationError, match="one of"):
        _ = _validate(data)


def test_each_kind_requires_its_parameters() -> None:
    assert REQUIRED_PARAMS[RuleKind.STRUCTURING] == {
        "threshold",
        "near_ratio",
        "window",
        "min_count",
    }
    for kind in RuleKind:
        assert definition(kind).kind is kind
    data = document(RuleKind.STRUCTURING)
    del data["params"]["min_count"]
    with pytest.raises(ValidationError, match="min_count"):
        _ = _validate(data)
    data = document(RuleKind.TIER_LIMIT)
    data["params"]["outcome"] = {"value": "BLOCK"}
    with pytest.raises(ValidationError, match="BLOCK and REVIEW"):
        _ = _validate(data)
    data = document(RuleKind.VELOCITY)
    del data["params"]["count"]
    del data["params"]["sum"]
    with pytest.raises(ValidationError, match="count or sum"):
        _ = _validate(data)
    data = document(RuleKind.THRESHOLD)
    del data["params"]["threshold"]
    with pytest.raises(ValidationError, match="threshold"):
        _ = _validate(data)
    # A screening gate lets a THRESHOLD rule fire on any amount.
    data["on"] = {"counterparty_screening_status": ["TRUE_MATCH"]}
    assert _validate(data).params == {}
    data["on"] = {}
    data["params"] = {"party_screening_status": {"value": "TRUE_MATCH"}}
    assert "threshold" not in _validate(data).params


def test_conditions_name_declared_aggregates_and_params() -> None:
    data = document(RuleKind.AGGREGATE)
    data["conditions"][0]["aggregate"] = "nowhere"
    with pytest.raises(ValidationError, match="undeclared aggregate 'nowhere'"):
        _ = _validate(data)
    data = document(RuleKind.AGGREGATE)
    data["conditions"][0]["threshold"] = "missing"
    with pytest.raises(ValidationError, match="unknown parameter 'missing'"):
        _ = _validate(data)
    data = document(RuleKind.AGGREGATE)
    data["conditions"][0]["ratio_of"] = "out_sum"
    data["conditions"][0]["ratio"] = "0.5"
    with pytest.raises(ValidationError, match="either threshold or ratio_of"):
        _ = _validate(data)
    data = document(RuleKind.AGGREGATE)
    del data["conditions"][1]["ratio"]
    with pytest.raises(ValidationError, match="either threshold or ratio_of"):
        _ = _validate(data)
    data = document(RuleKind.AGGREGATE)
    data["conditions"] = []
    with pytest.raises(ValidationError, match="at least one condition"):
        _ = _validate(data)
    data = document(RuleKind.AGGREGATE)
    data["evidence"] = {"aggregates": ["in_sum", "ghost"]}
    with pytest.raises(ValidationError, match="ghost"):
        _ = _validate(data)
    data = document(RuleKind.AGGREGATE)
    data["aggregates"][1]["name"] = "in_sum"
    data["conditions"] = data["conditions"][:1]
    with pytest.raises(ValidationError, match="unique"):
        _ = _validate(data)
    data = document(RuleKind.AGGREGATE)
    data["aggregates"][0]["window"] = "P1M2D"
    with pytest.raises(ValidationError, match="months or days"):
        _ = _validate(data)


def test_a_dedup_window_is_never_zero() -> None:
    # A zero window has no bucket: processing would divide by it (B7 review I3).
    for window in ("P0D", "P0M", "P0Y", "P0Y0M0D"):
        with pytest.raises(ValidationError, match="dedup window"):
            _ = Dedup(window=window)
        data = document(RuleKind.THRESHOLD)
        data["dedup"] = {"window": window}
        with pytest.raises(ValidationError, match="dedup window"):
            _ = _validate(data)
    assert Dedup(window="P1D", cooldown="P0D").cooldown == "P0D"


def test_durations_are_iso_or_the_calendar_month() -> None:
    data = document(RuleKind.THRESHOLD)
    data["dedup"] = {"window": "MONTH", "cooldown": "P0D"}
    data["evidence"] = {"transactions_window": "P30D"}
    rule = _validate(data)
    assert rule.dedup.window == "MONTH"
    assert rule.evidence.transactions_window == "P30D"
    for dedup in ({"window": "weekly"}, {"cooldown": "MONTH"}):
        data["dedup"] = dedup
        with pytest.raises(ValidationError, match="ISO-8601"):
            _ = _validate(data)
    data["dedup"] = {}
    data["evidence"] = {"transactions_window": "7 days"}
    with pytest.raises(ValidationError, match="ISO-8601"):
        _ = _validate(data)
    data["evidence"] = {}
    data["language"] = "EXPR/1"
    with pytest.raises(ValidationError, match="language"):
        _ = _validate(data)


def test_with_params_replaces_values_and_keeps_bounds() -> None:
    rule = definition(RuleKind.STRUCTURING)
    tuned = rule.with_params({"threshold": 4000, "min_count": 4})
    assert tuned.params["threshold"].value == Decimal(4000)
    assert type(tuned.params["threshold"].value) is Decimal
    assert tuned.params["threshold"].min == Decimal(1000)
    assert tuned.params["threshold"].tighten == "LOWER"
    assert tuned.params["min_count"].value == 4
    assert tuned.params["near_ratio"] == rule.params["near_ratio"]
    assert rule.params["threshold"].value == Decimal(5000)
    with pytest.raises(ValidationError, match="threshold"):
        _ = rule.with_params({"threshold": Decimal(999)})
    with pytest.raises(ValueError, match="no parameter ghost"):
        _ = rule.with_params({"ghost": 1})


def test_a_tier_limit_definition_is_sync_and_monthly() -> None:
    rule = definition(RuleKind.TIER_LIMIT)
    assert rule.sync is True
    assert rule.dedup.window == "MONTH"
    assert rule.params["outcome"].value == HitOutcome.BLOCK
    assert rule.with_params({"outcome": "REVIEW"}).params["outcome"].value == "REVIEW"
    data = document(RuleKind.TIER_LIMIT)
    data["sync"] = False
    with pytest.raises(ValidationError, match="sync"):
        _ = _validate(data)
    data = document(RuleKind.TIER_LIMIT)
    data["dedup"] = {"window": "P1D"}
    with pytest.raises(ValidationError, match="MONTH"):
        _ = _validate(data)


def test_list_names_resolve_to_codes_and_change_the_agg_id() -> None:
    lists = load_jurisdictions()
    grey = lists.countries["FATF_GREY"]
    where = TxnFilter(
        direction=frozenset({Direction.OUT}),
        counterparty_country_in=frozenset({"$FATF_GREY", "PA"}),
        counterparty_country_not_in=frozenset({"$FATF_BLACK"}),
        geo_admin_area_in=frozenset({"$BORDER_ZONE_MX", "MX-CMX"}),
    )
    resolved = resolve_list_names(where, lists)
    assert resolved.counterparty_country_in == grey | {"PA"}
    assert resolved.counterparty_country_not_in == {"KP", "IR", "MM"}
    assert resolved.geo_admin_area_in == lists.regions["BORDER_ZONE_MX"] | {"MX-CMX"}
    assert resolved.direction == where.direction
    literal = TxnFilter(counterparty_country_in=frozenset({"PA"}))
    assert resolve_list_names(literal, lists) == literal
    with pytest.raises(ValueError, match="NOWHERE"):
        _ = resolve_list_names(
            TxnFilter(counterparty_country_in=frozenset({"$NOWHERE"})), lists
        )

    data = document(RuleKind.AGGREGATE)
    data["on"] = {"counterparty_country_in": ["$FATF_GREY"]}
    data["evidence"] = {
        "transactions_where": {"geo_admin_area_in": ["$BORDER_ZONE_MX"]}
    }
    data["aggregates"][0]["where"] = {"counterparty_country_in": ["$FATF_GREY"]}
    rule = _validate(data)
    loaded = resolve_definition(rule, lists)
    assert loaded.on.counterparty_country_in == grey
    assert (
        loaded.evidence.transactions_where.geo_admin_area_in
        == lists.regions["BORDER_ZONE_MX"]
    )
    assert loaded.aggregates[0].where.counterparty_country_in == grey
    assert {declaration.lists_hash for declaration in loaded.aggregates} == {
        lists.content_hash.hex()
    }
    assert rule.on.counterparty_country_in == {"$FATF_GREY"}
    other = resolve_definition(rule, replace(lists, content_hash=bytes(32)))
    assert other.aggregates[0].agg_id() != loaded.aggregates[0].agg_id()
    assert other.aggregates[1].agg_id() != loaded.aggregates[1].agg_id()


def test_bounds_compare_like_with_like() -> None:
    assert Param[str](value="B", min="A", max="C").problem() is None
    assert Param[str](value="D", max="C").problem() == "D is above the maximum C"
    assert Param[bool](value=True, min=False).problem() is None
    data = document(RuleKind.THRESHOLD)
    data["on"] = {"amount_unit_key": "USD/PRIOR_BANKING_DAY", "amount_gte": "500.0"}
    stored = definition(RuleKind.THRESHOLD).model_validate(data).model_dump(mode="json")
    assert stored["on"]["amount_gte"] == "500.0"
    data["params"] = "threshold"
    with pytest.raises(ValidationError, match="params"):
        _ = _validate(data)


def test_a_named_list_must_have_codes_and_literals_must_be_codes() -> None:
    lists = load_jurisdictions()
    # TAX_HAVEN is empty (Open question 1): naming it is a load error, never a
    # filter that places no condition.
    with pytest.raises(ValueError, match="TAX_HAVEN has no source yet"):
        _ = resolve_list_names(
            TxnFilter(counterparty_country_in=frozenset({"$TAX_HAVEN"})), lists
        )
    for codes in ({"FATF_GREY"}, {"MEX"}, {"mx"}):
        with pytest.raises(ValueError, match="not a country code"):
            _ = resolve_list_names(
                TxnFilter(counterparty_country_not_in=frozenset(codes)), lists
            )
    with pytest.raises(ValueError, match="not a region code"):
        _ = resolve_list_names(
            TxnFilter(geo_admin_area_in=frozenset({"BORDER_ZONE_MX"})), lists
        )


def test_the_lists_parameter_is_checked_when_the_rule_set_loads() -> None:
    lists = load_jurisdictions()
    rule = definition(RuleKind.HIGH_RISK_JURISDICTION)
    assert resolve_definition(rule, lists).params == rule.params
    for value, problem in (
        ("FATF_BLACK,NOWHERE", "NOWHERE"),
        ("FATF_GREY, TAX_HAVEN", "TAX_HAVEN has no source yet"),
        ("", "unknown country list ''"),
    ):
        data = document(RuleKind.HIGH_RISK_JURISDICTION)
        data["params"]["lists"]["value"] = value
        with pytest.raises(ValueError, match=problem):
            _ = resolve_definition(_validate(data), lists)


def test_a_window_parameter_is_a_duration_and_velocity_stays_short() -> None:
    for kind in (RuleKind.STRUCTURING, RuleKind.VELOCITY):
        data = document(kind)
        data["params"]["window"]["value"] = "weekly"
        with pytest.raises(ValidationError, match="runs from PT1H to P6M"):
            _ = _validate(data)
    data = document(RuleKind.VELOCITY)
    for window in ("PT1H", "P7D", "PT12H"):
        data["params"]["window"]["value"] = window
        assert _validate(data).params["window"].value == window
    for window in ("P8D", "P1M"):
        data["params"]["window"]["value"] = window
        with pytest.raises(ValidationError, match=r"PT1H\.\.P7D"):
            _ = _validate(data)
    data["params"]["window"]["value"] = "P99Y"
    with pytest.raises(ValidationError, match="runs from PT1H to P6M"):
        _ = _validate(data)
    # Only VELOCITY is held to the short window; every window stays within A7's.
    data = document(RuleKind.STRUCTURING)
    data["params"]["window"]["value"] = "P30D"
    data["aggregates"][0]["window"] = "P30D"
    assert _validate(data).params["window"].value == "P30D"
    for window in ("P99Y", "P1Y", "P7M", "P200D"):
        data["params"]["window"]["value"] = window
        with pytest.raises(ValidationError, match="runs from PT1H to P6M"):
            _ = _validate(data)
    data["params"]["window"]["value"] = "P1M2D"
    with pytest.raises(ValidationError, match="months or days"):
        _ = _validate(data)
    # A STRUCTURING window is the window of its near_count / near_sum.
    data["params"]["window"]["value"] = "P7D"
    with pytest.raises(ValidationError, match="'near_sum' counts over P30D"):
        _ = _validate(data)


def test_a_float_is_refused_anywhere_in_the_document() -> None:
    data = document(RuleKind.AGGREGATE)
    data["conditions"][1]["ratio"] = 0.9
    with pytest.raises(
        ValidationError, match=r"conditions\[1\]\.ratio: .*never a float"
    ):
        _ = _validate(data)
    data = document(RuleKind.THRESHOLD)
    data["on"] = {"amount_unit_key": "USD/PRIOR_BANKING_DAY", "amount_gte": 500.5}
    with pytest.raises(ValidationError, match=r"on\.amount_gte: .*never a float"):
        _ = _validate(data)
    data = document(RuleKind.STRUCTURING)
    data["aggregates"][0]["where"]["amount_lt"] = 500.5
    with pytest.raises(
        ValidationError, match=r"aggregates\[0\]\.where\.amount_lt: .*never a float"
    ):
        _ = _validate(data)
    data = document(RuleKind.STRUCTURING)
    data["params"]["near_ratio"]["choices"] = ["0.6", 0.7]
    with pytest.raises(ValidationError, match=r"choices\[1\]: .*never a float"):
        _ = _validate(data)


def test_a_decimal_hashes_alike_however_it_is_written() -> None:
    data = document(RuleKind.STRUCTURING)
    first = _validate(data)
    data["params"]["threshold"]["value"] = "5000.00"
    data["params"]["near_ratio"]["value"] = "0.7"
    data["aggregates"][0]["where"]["amount_gte"] = "3500.0"
    respelled = _validate(data)
    assert respelled.definition_hash() == first.definition_hash()
    # The stored JSON keeps the spelling as written.
    assert respelled.model_dump(mode="json")["params"]["threshold"]["value"] == (
        "5000.00"
    )
    threshold = document(RuleKind.THRESHOLD)
    threshold["on"] = {"amount_unit_key": "USD/PRIOR_BANKING_DAY", "amount_gte": "500"}
    hashed = _validate(threshold).definition_hash()
    threshold["on"]["amount_gte"] = "500.0"
    assert _validate(threshold).definition_hash() == hashed
    aggregate = document(RuleKind.AGGREGATE)
    hashed = _validate(aggregate).definition_hash()
    aggregate["conditions"][1]["ratio"] = "0.90"
    assert _validate(aggregate).definition_hash() == hashed
    aggregate["conditions"][1]["ratio"] = "0.91"
    assert _validate(aggregate).definition_hash() != hashed


def test_the_hash_input_is_the_audit_chains_canonical_json() -> None:
    sample = {
        "título": "Depósitos en efectivo — ñandú",
        "nested": {"z": [1, {"b": "2", "a": None}], "a": True},
        "definition": definition(RuleKind.STRUCTURING).model_dump(mode="json"),
    }
    assert canonical_json(sample) == chain.canonical_json(sample)


def test_a_unit_key_names_a_unit_and_a_conversion_rule() -> None:
    data = document(RuleKind.THRESHOLD)
    for unit_key in ("USD/LAST_DAY_PREV_MONTH", "UDI/SAME_DAY"):
        data["unit_key"] = unit_key
        assert _validate(data).unit_key == unit_key
    for unit_key in ("USD", "USD/WHENEVER", "usd/SAME_DAY", "USD/SAME_DAY/X"):
        data["unit_key"] = unit_key
        with pytest.raises(ValidationError, match="<unit>/<rule>"):
            _ = _validate(data)
