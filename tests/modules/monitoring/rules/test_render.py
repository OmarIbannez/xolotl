"""The rendered sentence of each kind, in both locales, against its golden."""

import difflib
from decimal import Decimal
from pathlib import Path

import pytest

from tests.modules.monitoring.rules.samples import definition, document
from xolotl.modules.monitoring.rules.definitions import RuleDefinition, RuleKind
from xolotl.modules.monitoring.rules.render import Locale, render

GOLDEN = Path(__file__).resolve().parents[3] / "golden/monitoring/rules/render"
_THIN = chr(0x202F)  # U+202F NARROW NO-BREAK SPACE


def _assert_golden(name: str, actual: str) -> None:
    expected = (GOLDEN / name).read_text(encoding="utf-8").removesuffix("\n")
    if actual != expected:
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(),
                actual.splitlines(),
                fromfile=f"golden/{name}",
                tofile="rendered",
                lineterm="",
            )
        )
        pytest.fail(f"{name} differs from its golden:\n{diff}")


def test_the_cash_structuring_sentence_matches_the_spec() -> None:
    rule = definition(RuleKind.STRUCTURING)
    assert rule.render() == (
        "Dispara cuando el cliente recibe 3 o más depósitos en efectivo entre "
        "70 % y 100 % del umbral en 7 días y su suma alcanza el umbral"
    )
    assert rule.render("es-MX") == render(rule, "es-MX")


@pytest.mark.parametrize("kind", list(RuleKind))
@pytest.mark.parametrize("locale", ["es-MX", "en"])
def test_every_kind_renders_in_both_locales(kind: RuleKind, locale: Locale) -> None:
    _assert_golden(f"{kind}.{locale}.txt", definition(kind).render(locale))


def test_amounts_group_thousands_per_locale() -> None:
    data = document(RuleKind.THRESHOLD)
    data["params"]["threshold"]["value"] = "12500.50"
    rule = RuleDefinition.model_validate(data)
    assert f"12{_THIN}500.50 USD" in rule.render("es-MX")
    assert "12,500.50 USD" in rule.render("en")
    unitless = rule.model_copy(update={"unit_key": None})
    tuned = unitless.with_params({"threshold": Decimal(5000)})
    plain = tuned.model_copy(
        update={
            "params": {
                "threshold": tuned.params["threshold"].model_copy(update={"unit": None})
            }
        }
    )
    assert plain.render("en").endswith("of 5,000 or more to a self-custody wallet")


def test_filters_and_gates_read_as_clauses() -> None:
    data = document(RuleKind.THRESHOLD)
    data["on"] = {
        "counterparty_screening_status": ["TRUE_MATCH"],
        "cross_border": True,
        "counterparty_country_in": ["$FATF_GREY", "PA"],
        "counterparty_country_not_in": ["US"],
        "geo_admin_area_in": ["$BORDER_ZONE_MX"],
        "amount_round_multiple": 1000,
        "self_custody": False,
    }
    data["params"] = {
        "party_screening_status": {"value": "TRUE_MATCH"},
        "pep_status_not": {"value": "NONE"},
    }
    rule = RuleDefinition.model_validate(data)
    _assert_golden("clauses.es-MX.txt", rule.render("es-MX"))
    _assert_golden("clauses.en.txt", rule.render("en"))
    data["on"] = {"direction": ["OUT"], "instrument": ["CASH"], "cross_border": False}
    data["params"] = {"pep_status_not": {"value": "FOREIGN"}}
    other = RuleDefinition.model_validate(data)
    assert other.render("es-MX") == (
        "Dispara cuando el cliente realiza un retiro en efectivo con una contraparte "
        "nacional cuando el estado PEP del cliente no es FOREIGN"
    )
    assert other.render("en") == (
        "Fires when the customer makes a cash withdrawal with a domestic "
        "counterparty when the customer's PEP status is not FOREIGN"
    )


def test_aggregate_windows_and_functions_read_in_words() -> None:
    data = document(RuleKind.AGGREGATE)
    data["aggregates"] = [
        {
            "name": "counterparties",
            "function": "DISTINCT_COUNT",
            "measure": "ONE",
            "unit_key": None,
            "key_kind": "RELATIONSHIP",
            "window_kind": "QUARTER",
            "window": "QUARTER",
            "distinct_of": "DEVICE",
        },
        {
            "name": "accum",
            "function": "MAX",
            "measure": "AMOUNT_MINOR",
            "unit_key": None,
            "key_kind": "RELATIONSHIP",
            "window_kind": "ACCUM",
            "window": "P6M",
            "where": {"instrument": ["FX"]},
        },
        {
            "name": "hourly",
            "function": "COUNT",
            "measure": "ONE",
            "unit_key": None,
            "key_kind": "RELATIONSHIP",
            "window_kind": "DURATION",
            "window": "PT1H",
        },
    ]
    data["params"] = {"many": {"value": 3}}
    data["conditions"] = [
        {"aggregate": "counterparties", "op": "GT", "threshold": "many"},
        {"aggregate": "accum", "op": "LT", "ratio_of": "hourly", "ratio": "1.5"},
        {"aggregate": "hourly", "op": "LTE", "threshold": "many"},
    ]
    rule = RuleDefinition.model_validate(data)
    _assert_golden("windows.es-MX.txt", rule.render("es-MX"))
    _assert_golden("windows.en.txt", rule.render("en"))


def test_velocity_by_sum_after_dormancy_and_a_triggered_aggregate() -> None:
    data = document(RuleKind.VELOCITY)
    data["params"] = {
        "window": {"value": "P2D"},
        "sum": {"value": "2000"},
        "dormant_days": {"value": 180},
    }
    rule = RuleDefinition.model_validate(data)
    assert rule.render("es-MX") == (
        f"Dispara cuando el cliente realiza operaciones por un total de 2{_THIN}000 "
        "USD o más en 2 días tras 180 días sin actividad"
    )
    assert rule.render("en") == (
        "Fires when the customer makes operations totalling 2,000 USD or more "
        "within 2 days after 180 days without activity"
    )
    data = document(RuleKind.AGGREGATE)
    data["on"] = {"direction": ["IN"], "instrument": ["CASH"]}
    data["aggregates"][0]["window_kind"] = "MONTH"
    data["aggregates"][0]["window"] = "MONTH"
    data["conditions"] = data["conditions"][:1]
    rule = RuleDefinition.model_validate(data)
    assert rule.render("en") == (
        "Fires when the customer receives a cash deposit and the sum of incoming "
        "operations in the calendar month is at least 2,000 USD"
    )


def test_rails_channels_currencies_and_amount_bands_read_as_clauses() -> None:
    data = document(RuleKind.THRESHOLD)
    data["on"] = {
        "rail": ["SPEI", "CODI"],
        "channel": ["APP"],
        "currency": ["MXN"],
        "amount_unit_key": "USD/PRIOR_BANKING_DAY",
        "amount_gte": "3500",
        "amount_lt": "5000",
    }
    rule = RuleDefinition.model_validate(data)
    _assert_golden("routes.es-MX.txt", rule.render("es-MX"))
    _assert_golden("routes.en.txt", rule.render("en"))
    data["on"] = {
        "rail": ["SWIFT"],
        "channel": ["BRANCH", "ATM"],
        "currency": ["USD", "EUR"],
        "amount_gte": "10000",
    }
    data["params"] = {"party_screening_status": {"value": "TRUE_MATCH"}}
    rule = RuleDefinition.model_validate(data)
    assert rule.render("es-MX") == (
        "Dispara cuando el cliente realiza una operación por la red de pago SWIFT "
        "por los canales ATM y BRANCH en monedas EUR y USD de al menos "
        f"10{_THIN}000 mientras el cliente tiene estado de screening TRUE_MATCH"
    )
    assert rule.render("en") == (
        "Fires when the customer makes an operation on the SWIFT rail through the "
        "ATM and BRANCH channels in EUR and USD of at least 10,000 while the "
        "customer's screening status is TRUE_MATCH"
    )
    data["on"] = {"amount_unit_key": "UDI/SAME_DAY", "amount_lt": "750"}
    rule = RuleDefinition.model_validate(data)
    assert " de menos de 750 UDI " in rule.render("es-MX")
    assert " below 750 UDI " in rule.render("en")


def test_a_jurisdiction_rule_reads_its_window_branch() -> None:
    data = document(RuleKind.HIGH_RISK_JURISDICTION)
    data["params"].update({
        "window": {"value": "P7D"},
        "count": {"value": 3},
        "sum": {"value": "5000", "unit": "USD"},
    })
    rule = RuleDefinition.model_validate(data)
    _assert_golden("HIGH_RISK_JURISDICTION.window.es-MX.txt", rule.render("es-MX"))
    _assert_golden("HIGH_RISK_JURISDICTION.window.en.txt", rule.render("en"))
    del data["params"]["count"]
    rule = RuleDefinition.model_validate(data)
    branch = (
        ", or when within 7 days they make transfer operations totalling 5,000 USD "
        "or more with counterparties on those lists"
    )
    assert rule.render("en").endswith(branch)
    del data["params"]["sum"]
    rule = RuleDefinition.model_validate(data)
    assert "or when" not in rule.render("en")
