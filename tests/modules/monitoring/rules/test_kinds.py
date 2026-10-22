"""The pure evaluator: the seven ALERT kinds of spec §9.1, at their boundaries."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.modules.monitoring.rules.conftest import (
    OCCURRED,
    USD,
    FakeReader,
    context,
    declaration,
    enriched,
    itf_policy,
    profile,
    rule,
    usd_amounts,
    value,
)
from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.rules.definitions import (
    HitOutcome,
    RuleKind,
    Severity,
)
from xolotl.modules.monitoring.rules.kinds import (
    INCOMPLETE,
    AggregateSnapshot,
    DeclaredProfile,
    applies,
    evaluate,
)
from xolotl.modules.monitoring.txn.schema import Direction, Instrument, KeyKind, Rail
from xolotl.platform.units.quantity import Quantity
from xolotl.platform.units.rules import ConversionRule

MONTH_START = datetime(2026, 12, 1, tzinfo=MEXICO_CITY_ZONE).astimezone(UTC)


def _threshold_rule(**changes: object):
    data: dict[str, object] = {
        "on": {"direction": ["IN"]},
        "params": {"threshold": {"value": "5000", "unit": "USD"}},
    }
    data.update(changes)
    return rule(RuleKind.THRESHOLD, **data)


def test_a_threshold_fires_at_the_threshold_and_not_below() -> None:
    definition = _threshold_rule()
    at = context(txn=enriched(unit_amounts=usd_amounts("5000")))
    firing = evaluate(definition, at)
    assert firing is not None
    assert firing.window_start == firing.window_end == OCCURRED
    assert firing.aggregates == {}
    below = context(txn=enriched(unit_amounts=usd_amounts("4999.99")))
    assert evaluate(definition, below) is None
    no_unit = context(txn=enriched(unit_amounts={}))
    assert evaluate(definition, no_unit) is None


def test_a_threshold_profile_gate_reads_the_profile() -> None:
    blocked = rule(
        RuleKind.THRESHOLD,
        on={},
        params={
            "party_screening_status": {"value": "TRUE_MATCH", "choices": ["TRUE_MATCH"]}
        },
    )
    matched = context(who=profile(party_screening_status="TRUE_MATCH"))
    assert evaluate(blocked, matched) is not None
    assert (
        evaluate(blocked, context(who=profile(party_screening_status="CLEAR"))) is None
    )
    assert evaluate(blocked, context(who=profile(party_screening_status=None))) is None

    pep = rule(
        RuleKind.THRESHOLD,
        on={},
        params={
            "pep_status_not": {"value": "NONE", "choices": ["NONE"]},
            "threshold": {"value": "10000", "unit": "USD"},
        },
    )
    large = enriched(unit_amounts=usd_amounts("10000"))
    assert evaluate(pep, context(txn=large, who=profile(pep_status="NONE"))) is None
    assert evaluate(pep, context(txn=large, who=profile(pep_status="DOMESTIC")))
    small = enriched(unit_amounts=usd_amounts("9999.99"))
    assert evaluate(pep, context(txn=small, who=profile(pep_status="FOREIGN"))) is None


def test_the_profile_gates_hold_for_every_kind() -> None:
    fired = {"near_count": value("3", count=3), "near_sum": value("5100", count=3)}
    base = rule(RuleKind.STRUCTURING).params
    screened = rule(
        RuleKind.STRUCTURING,
        params={
            **{name: param.model_dump() for name, param in base.items()},
            "party_screening_status": {
                "value": "TRUE_MATCH",
                "choices": ["TRUE_MATCH"],
            },
        },
    )
    matched = profile(party_screening_status="TRUE_MATCH")
    assert evaluate(screened, context(fired, who=matched)) is not None
    assert evaluate(screened, context(fired)) is None
    pep = rule(
        RuleKind.STRUCTURING,
        params={
            **{name: param.model_dump() for name, param in base.items()},
            "pep_status_not": {"value": "NONE", "choices": ["NONE"]},
        },
    )
    assert evaluate(pep, context(fired, who=profile(pep_status="DOMESTIC")))
    assert evaluate(pep, context(fired)) is None


def test_a_rule_does_not_apply_outside_its_segment_or_filter() -> None:
    txn = enriched(unit_amounts=usd_amounts("6000"))
    segmented = _threshold_rule(segment={"tier": ["N1"]})
    assert evaluate(segmented, context(txn=txn, who=profile(tier="N2"))) is None
    assert evaluate(segmented, context(txn=txn, who=profile(tier=None))) is None
    assert evaluate(segmented, context(txn=txn, who=profile(tier="N1"))) is not None
    risky = _threshold_rule(segment={"risk_rating": ["HIGH"]})
    assert not applies(risky, context(txn=txn))
    assert applies(risky, context(txn=txn, who=profile(risk_rating="HIGH")))
    enhanced = _threshold_rule(segment={"monitoring_profile": ["ENHANCED"]})
    assert not applies(enhanced, context(txn=txn))
    assert applies(
        enhanced, context(txn=txn, who=profile(monitoring_profile="ENHANCED"))
    )

    cash = _threshold_rule(on={"instrument": ["CASH"]})
    transfer = enriched(
        unit_amounts=usd_amounts("6000"),
        instrument=Instrument.TRANSFER,
        rail=Rail.SPEI,
    )
    assert evaluate(cash, context(txn=transfer)) is None
    assert evaluate(cash, context(txn=txn)) is not None

    capture = _threshold_rule(family="CAPTURE")
    assert not applies(capture, context(txn=txn))
    per_counterparty = _threshold_rule(key_kind="COUNTERPARTY")
    assert evaluate(per_counterparty, context(txn=txn)) is None


def test_an_incomplete_aggregate_never_fires() -> None:
    definition = rule(RuleKind.STRUCTURING)
    near_count = value("3", count=3)
    assert evaluate(definition, context({"near_count": near_count})) is None
    pending = context({"near_count": near_count, "near_sum": INCOMPLETE})
    assert evaluate(definition, pending) is None
    unfinished = value("6000", count=3, complete=False)
    partial = context({"near_count": near_count, "near_sum": unfinished})
    assert evaluate(definition, partial) is None


def test_structuring_needs_the_count_and_the_sum() -> None:
    definition = rule(RuleKind.STRUCTURING)
    three = value("3", count=3)
    short = context({"near_count": three, "near_sum": value("4800", count=3)})
    assert evaluate(definition, short) is None
    few = context({"near_count": value("2", count=2), "near_sum": value("5100")})
    assert evaluate(definition, few) is None
    at_limits = context({"near_count": three, "near_sum": value("5000", count=3)})
    assert evaluate(definition, at_limits) is not None

    firing = evaluate(
        definition, context({"near_count": three, "near_sum": value("5100", count=3)})
    )
    assert firing is not None
    assert firing.aggregates == {
        "near_count": AggregateSnapshot(Decimal(3), None, "P7D", 3),
        "near_sum": AggregateSnapshot(Decimal(5100), USD, "P7D", 3),
    }
    assert firing.window_start == OCCURRED - timedelta(days=7)
    assert firing.window_end == OCCURRED


def _funnel():
    return rule(
        RuleKind.AGGREGATE,
        params={
            "min_counterparties": {"value": 10},
            "min_in": {"value": "5000", "unit": "USD"},
        },
        aggregates=[
            declaration(
                "distinct_counterparties_in",
                "DISTINCT_COUNT",
                "P7D",
                measure="ONE",
                distinct_of="COUNTERPARTY",
                where={"direction": ["IN"]},
            ),
            declaration("in_sum", "SUM", "P7D", where={"direction": ["IN"]}),
        ],
        conditions=[
            {
                "aggregate": "distinct_counterparties_in",
                "op": "GTE",
                "threshold": "min_counterparties",
            },
            {"aggregate": "in_sum", "op": "GTE", "threshold": "min_in"},
        ],
    )


def test_an_aggregate_rule_evaluates_every_condition() -> None:
    definition = _funnel()
    in_sum = value("5000", count=12)
    ten = value("12", count=12, distinct=10)
    firing = evaluate(
        definition, context({"distinct_counterparties_in": ten, "in_sum": in_sum})
    )
    assert firing is not None
    assert firing.aggregates["distinct_counterparties_in"].value == 10
    assert firing.window_start == OCCURRED - timedelta(days=7)
    nine = value("12", count=12, distinct=9)
    assert (
        evaluate(
            definition, context({"distinct_counterparties_in": nine, "in_sum": in_sum})
        )
        is None
    )
    low = value("4999.99", count=12)
    assert (
        evaluate(
            definition, context({"distinct_counterparties_in": ten, "in_sum": low})
        )
        is None
    )


def test_a_ratio_condition_compares_against_another_aggregate() -> None:
    definition = rule(RuleKind.AGGREGATE)
    in_sum = value("2000", count=1)
    passed = context({"in_sum": in_sum, "out_sum": value("1800", count=1)})
    firing = evaluate(definition, passed)
    assert firing is not None
    assert firing.window_start == OCCURRED - timedelta(days=2)
    kept = context({"in_sum": in_sum, "out_sum": value("1799.99", count=1)})
    assert evaluate(definition, kept) is None

    ratio_only = rule(
        RuleKind.AGGREGATE,
        params={},
        conditions=[
            {"aggregate": "out_sum", "op": "GTE", "ratio_of": "in_sum", "ratio": "0.9"}
        ],
    )
    empty = context({"in_sum": value("0"), "out_sum": value("5", count=1)})
    assert evaluate(ratio_only, empty) is None
    below = rule(
        RuleKind.AGGREGATE,
        params={},
        conditions=[
            {"aggregate": "out_sum", "op": "LT", "ratio_of": "in_sum", "ratio": "0.5"}
        ],
    )
    assert evaluate(below, context({"in_sum": in_sum, "out_sum": value("999")}))
    assert (
        evaluate(below, context({"in_sum": in_sum, "out_sum": value("1000")})) is None
    )


def test_the_strict_and_inclusive_condition_ops() -> None:
    def single(op: str):
        return rule(
            RuleKind.AGGREGATE,
            params={"limit": {"value": "1000", "unit": "USD"}},
            conditions=[{"aggregate": "in_sum", "op": op, "threshold": "limit"}],
        )

    def at(total: str):
        return context({"in_sum": value(total, count=1), "out_sum": value("0")})

    above = single("GT")
    assert evaluate(above, at("1000.01")) is not None
    assert evaluate(above, at("1000")) is None
    upto = single("LTE")
    assert evaluate(upto, at("1000")) is not None
    assert evaluate(upto, at("1000.01")) is None


def test_velocity_gates_on_account_age_and_dormancy() -> None:
    definition = rule(RuleKind.VELOCITY)
    busy = {"window_count": value("10", count=10), "window_sum": value("100", count=10)}

    def started(days: int):
        return profile(relationship_started_at=OCCURRED - timedelta(days=days))

    firing = evaluate(definition, context(busy, who=started(10)))
    assert firing is not None
    assert firing.window_start == OCCURRED - timedelta(days=1)
    assert evaluate(definition, context(busy, who=started(30))) is not None
    assert evaluate(definition, context(busy, who=started(40))) is None
    unknown = profile(relationship_started_at=None)
    assert evaluate(definition, context(busy, who=unknown)) is None
    quiet = {
        "window_count": value("9", count=9),
        "window_sum": value("2999.99", count=9),
    }
    assert evaluate(definition, context(quiet, who=started(10))) is None
    large = {"window_count": value("9", count=9), "window_sum": value("3000", count=9)}
    assert evaluate(definition, context(large, who=started(10))) is not None

    dormant = rule(
        RuleKind.VELOCITY,
        params={
            "window": {"value": "P2D"},
            "sum": {"value": "2000", "unit": "USD"},
            "dormant_days": {"value": 180},
        },
        aggregates=[
            declaration("window_sum", "SUM", "P2D"),
            declaration("last_seen", "LAST_SEEN", "P6M", measure="ONE"),
        ],
    )
    start = OCCURRED - timedelta(days=2)

    def seen(days: int | None):
        last = None if days is None else start - timedelta(days=days)
        return {
            "window_sum": value("2000", count=2),
            "last_seen": value("40", count=40, last_at=last),
        }

    reactivated = context(seen(200))
    firing = evaluate(dormant, reactivated)
    assert firing is not None
    # The read before the window ends at the last instant before its first bucket.
    assert isinstance(reactivated.aggregates, FakeReader)
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    assert (
        "last_seen",
        day - timedelta(microseconds=1),
    ) in reactivated.aggregates.calls
    # LAST_SEEN is a time, not a value: the snapshot carries the sum only.
    assert set(firing.aggregates) == {"window_sum"}
    assert evaluate(dormant, context(seen(180))) is None
    assert evaluate(dormant, context(seen(181))) is not None
    assert evaluate(dormant, context(seen(None), who=profile(history_months=6)))
    assert evaluate(dormant, context(seen(None), who=profile(history_months=5))) is None
    assert evaluate(dormant, context({**seen(200), "last_seen": INCOMPLETE})) is None
    small = {**seen(200), "window_sum": value("1999.99", count=2)}
    assert evaluate(dormant, context(small)) is None


def _transfer(country: str | None, usd: str = "1000", geo: str = "MX"):
    return enriched(
        instrument=Instrument.TRANSFER,
        rail=Rail.SPEI,
        counterparty_country=country,
        geo_country=geo,
        unit_amounts=usd_amounts(usd),
    )


def test_high_risk_jurisdiction_resolves_list_names() -> None:
    definition = rule(RuleKind.HIGH_RISK_JURISDICTION)
    firing = evaluate(definition, context(txn=_transfer("IR")))
    assert firing is not None
    assert firing.window_start == firing.window_end == OCCURRED
    assert evaluate(definition, context(txn=_transfer("VE"))) is not None
    assert evaluate(definition, context(txn=_transfer("MX"))) is None
    assert evaluate(definition, context(txn=_transfer("IR", "999.99"))) is None
    assert evaluate(definition, context(txn=_transfer(None, geo="KP"))) is not None

    windowed = rule(
        RuleKind.HIGH_RISK_JURISDICTION,
        params={
            "threshold": {"value": "1000", "unit": "USD"},
            "lists": {"value": "FATF_BLACK,FATF_GREY"},
            "window": {"value": "P7D"},
            "count": {"value": 3},
        },
        aggregates=[
            declaration(
                "to_listed_count",
                "COUNT",
                "P7D",
                measure="ONE",
                where={"counterparty_country_in": ["IR", "KP"]},
            ),
            declaration(
                "to_listed_sum",
                "SUM",
                "P7D",
                where={"counterparty_country_in": ["IR", "KP"]},
            ),
        ],
    )
    domestic = _transfer("MX")
    three = {"to_listed_count": value("3", count=3), "to_listed_sum": value("30")}
    firing = evaluate(windowed, context(three, txn=domestic))
    assert firing is not None
    assert firing.window_start == OCCURRED - timedelta(days=7)
    summed = {"to_listed_count": value("1", count=1), "to_listed_sum": value("1000")}
    assert evaluate(windowed, context(summed, txn=domestic)) is not None
    neither = {"to_listed_count": value("2", count=2), "to_listed_sum": value("999")}
    assert evaluate(windowed, context(neither, txn=domestic)) is None
    pending = {**three, "to_listed_sum": INCOMPLETE}
    assert evaluate(windowed, context(pending, txn=domestic)) is None

    # A declared sum, not the per-transaction threshold, bounds the window's total.
    summing = rule(
        RuleKind.HIGH_RISK_JURISDICTION,
        params={
            **{name: param.model_dump() for name, param in windowed.params.items()},
            "sum": {"value": "5000", "unit": "USD"},
        },
        aggregates=windowed.model_dump()["aggregates"],
    )
    assert evaluate(summing, context(summed, txn=domestic)) is None
    large = {"to_listed_count": value("1", count=1), "to_listed_sum": value("5000")}
    assert evaluate(summing, context(large, txn=domestic)) is not None


def test_profile_deviation_is_silent_without_a_declaration_and_history() -> None:
    definition = rule(RuleKind.PROFILE_DEVIATION)
    mtd = {"mtd_in": value("250000", count=5), "trailing_in": value("250000")}
    young = profile(declared_profile=None, history_months=2)
    assert evaluate(definition, context(mtd, who=young)) is None

    # Declared 100,000.00 pesos a month, in centavos; the sums are in pesos.
    declared = DeclaredProfile(
        monthly_inflow_minor=10_000_000, monthly_outflow_minor=None, currency="MXN"
    )
    who = profile(declared_profile=declared, history_months=2)
    firing = evaluate(definition, context(mtd, who=who))
    assert firing is not None
    assert firing.params["ratio"] == "2.0"
    assert firing.window_start == MONTH_START
    assert firing.aggregates["mtd_in"].unit == "MXN/SAME_DAY"
    exactly = {"mtd_in": value("200000", count=5)}
    assert evaluate(definition, context(exactly, who=who)) is not None
    under = {"mtd_in": value("199999", count=5)}
    assert evaluate(definition, context(under, who=who)) is None
    in_usd = profile(
        declared_profile=DeclaredProfile(10_000_000, None, "USD"), history_months=2
    )
    assert evaluate(definition, context(mtd, who=in_usd)) is None
    # A USD transaction compares its peso sums with the MXN declaration.
    dollars = enriched(currency="USD", amount_minor=500_000)
    assert evaluate(definition, context(mtd, txn=dollars, who=who)) is not None
    assert evaluate(definition, context(under, txn=dollars, who=who)) is None
    outflow = enriched(direction=Direction.OUT)
    assert evaluate(definition, context(mtd, txn=outflow, who=who)) is None


def test_profile_deviation_fires_on_activity_against_a_declared_zero() -> None:
    # Declaring nothing in a direction and moving money in it is unbounded
    # deviation (plan 4c C8 fix round, I-2); no activity there is none.
    definition = rule(RuleKind.PROFILE_DEVIATION)
    zero = profile(
        declared_profile=DeclaredProfile(0, 10_000_000, "MXN"), history_months=2
    )
    some = {"mtd_in": value("0.01", count=1)}
    assert evaluate(definition, context(some, who=zero)) is not None
    none = {"mtd_in": value("0", count=0)}
    assert evaluate(definition, context(none, who=zero)) is None
    unset = profile(
        declared_profile=DeclaredProfile(None, 10_000_000, "MXN"), history_months=2
    )
    assert evaluate(definition, context(some, who=unset)) is None


def test_profile_deviation_compares_with_the_trailing_average() -> None:
    definition = rule(RuleKind.PROFILE_DEVIATION, on={})
    seasoned = profile(history_months=4)
    # (525,000 - 225,000) / 4 months = 75,000 a month; 225,000 is 3.0 times that.
    tripled = {"mtd_in": value("225000"), "trailing_in": value("525000")}
    assert evaluate(definition, context(tripled, who=seasoned)) is not None
    almost = {"mtd_in": value("224999"), "trailing_in": value("524999")}
    assert evaluate(definition, context(almost, who=seasoned)) is None
    nothing_before = {"mtd_in": value("225000"), "trailing_in": value("225000")}
    assert evaluate(definition, context(nothing_before, who=seasoned)) is None
    pending = {"mtd_in": value("225000"), "trailing_in": INCOMPLETE}
    assert evaluate(definition, context(pending, who=seasoned)) is None
    outflow = enriched(direction=Direction.OUT)
    spent = {"mtd_out": value("225000"), "trailing_out": value("525000")}
    assert evaluate(definition, context(spent, txn=outflow, who=seasoned))
    internal = enriched(direction=Direction.INTERNAL)
    assert evaluate(definition, context(tripled, txn=internal, who=seasoned)) is None


def test_profile_deviation_averages_over_at_most_six_months_of_history() -> None:
    definition = rule(RuleKind.PROFILE_DEVIATION, on={})
    year = profile(history_months=12)
    # (525,000 - 225,000) / 6 = 50,000 a month; 225,000 is 4.5 times that.
    assert evaluate(
        definition,
        context({"mtd_in": value("225000"), "trailing_in": value("525000")}, who=year),
    )
    # (725,000 - 225,000) / 6 = 83,333 a month, a ratio of 2.7: silent. Dividing
    # by twelve months would give 41,667 and a ratio of 5.4, a false alert.
    wide = {"mtd_in": value("225000"), "trailing_in": value("725000")}
    assert evaluate(definition, context(wide, who=year)) is None

    # From three months of history the comparison runs; with two it never does.
    tripled = {"mtd_in": value("225000"), "trailing_in": value("450000")}
    three = profile(declared_profile=None, history_months=3)
    assert evaluate(definition, context(tripled, who=three)) is not None
    two = profile(declared_profile=None, history_months=2)
    assert evaluate(definition, context(tripled, who=two)) is None


def test_tier_limit_reads_the_snapshots_tier_and_includes_the_transaction() -> None:
    definition = rule(RuleKind.TIER_LIMIT)
    over = {"month_in": value("750.000001", count=4)}
    firing = evaluate(definition, context(over))
    assert firing is not None
    assert firing.outcome is HitOutcome.BLOCK
    assert firing.severity is Severity.HIGH
    assert firing.window_start == MONTH_START
    assert firing.aggregates["month_in"].unit == "UDI/LAST_DAY_PREV_MONTH"
    at_limit = {"month_in": value("750", count=4)}
    assert evaluate(definition, context(at_limit)) is None
    assert evaluate(definition, context(over, who=profile(tier="N3"))) is None
    assert evaluate(definition, context(over, who=profile(tier=None))) is None
    assert evaluate(definition, context(over, who=profile(tier="N2"))) is None
    n2_over = {"month_in": value("3000.01")}
    assert evaluate(definition, context(n2_over, who=profile(tier="N2")))
    assert evaluate(definition, context({"month_in": INCOMPLETE})) is None

    policy = itf_policy()
    rule_value = policy.units.tier_limit_rule
    assert rule_value is not None
    same_day = policy.model_copy(
        update={
            "units": policy.units.model_copy(
                update={
                    "tier_limit_rule": rule_value.model_copy(
                        update={"value": ConversionRule.SAME_DAY}
                    )
                }
            )
        }
    )
    assert evaluate(definition, context(over, policy=same_day)) is None
    n1 = policy.kyc.tiers[0]
    monthly = n1.limits.monthly_ops
    assert monthly is not None
    in_uma = monthly.model_copy(
        update={"value": Quantity(amount=Decimal(750), unit="UMA")}
    )
    tiers = [
        n1.model_copy(
            update={"limits": n1.limits.model_copy(update={"monthly_ops": in_uma})}
        ),
        *policy.kyc.tiers[1:],
    ]
    uma = policy.model_copy(
        update={"kyc": policy.kyc.model_copy(update={"tiers": tiers})}
    )
    assert evaluate(definition, context(over, policy=uma)) is None
    no_rule = policy.model_copy(
        update={"units": policy.units.model_copy(update={"tier_limit_rule": None})}
    )
    assert evaluate(definition, context(over, policy=no_rule)) is None


def test_a_firing_carries_rendered_params_and_the_explanation() -> None:
    definition = rule(RuleKind.STRUCTURING)
    firing = evaluate(
        definition,
        context({
            "near_count": value("3", count=3),
            "near_sum": value("5100", count=3),
        }),
    )
    assert firing is not None
    assert firing.params == {
        "threshold": "5000",
        "near_ratio": "0.70",
        "window": "P7D",
        "min_count": "3",
    }
    assert firing.explanation == definition.render("es-MX")
    assert firing.rule_code == "CASH_STRUCTURING"
    assert firing.key_kind is KeyKind.RELATIONSHIP
    assert firing.key == str(enriched().relationship_id)
    assert firing.outcome is HitOutcome.ALERT
    assert firing.severity is Severity.MEDIUM

    flagged = _threshold_rule(
        params={
            "threshold": {"value": "5000", "unit": "USD"},
            "strict": {"value": True},
        }
    )
    txn = enriched(unit_amounts=usd_amounts("5000"))
    firing = evaluate(flagged, context(txn=txn))
    assert firing is not None
    assert firing.params == {"threshold": "5000", "strict": "true"}
