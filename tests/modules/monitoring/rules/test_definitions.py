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
    HitOutcome,
    Param,
    RuleDefinition,
    RuleKind,
    resolve_definition,
    resolve_list_names,
)
from xolotl.modules.monitoring.txn.schema import Direction
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
