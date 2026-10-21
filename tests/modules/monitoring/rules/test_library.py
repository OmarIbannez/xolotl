"""The shipped Mexico typology library: its sixteen templates, sentences and hashes."""

import difflib
import shutil
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
import yaml

from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    AggregateFunction,
    Measure,
    TxnFilter,
    WindowKind,
)
from xolotl.modules.monitoring.rules.definitions import (
    MONTH,
    RuleDefinition,
    RuleKind,
    Severity,
    resolve_definition,
)
from xolotl.modules.monitoring.rules.library import (
    TYPOLOGIES_DIR,
    read_library,
    read_typology,
)
from xolotl.modules.monitoring.rules.render import Locale
from xolotl.modules.monitoring.txn.schema import Direction, KeyKind
from xolotl.platform.errors import ValidationFailedError
from xolotl.platform.policy import load_jurisdictions

GOLDEN = Path(__file__).resolve().parents[3] / "golden/monitoring/rules"
ITF = "MX/ITF-IFPE"
AVI = "MX/LFPIORPI-AVI"
USD = "USD/PRIOR_BANKING_DAY"
# Spec §9.3: code -> (kind, typology).
SPEC_TABLE: dict[str, tuple[RuleKind, str]] = {
    "CASH_STRUCTURING": (RuleKind.STRUCTURING, "STRUCTURING"),
    "RAPID_PASS_THROUGH": (RuleKind.AGGREGATE, "MULE"),
    "NEW_ACCOUNT_VELOCITY": (RuleKind.VELOCITY, "VELOCITY"),
    "MANY_TO_ONE_FUNNEL": (RuleKind.AGGREGATE, "MULE"),
    "DECLARED_VS_ACTUAL_VOLUME": (RuleKind.PROFILE_DEVIATION, "PROFILE"),
    "HIGH_RISK_JURISDICTION_TRANSFER": (
        RuleKind.HIGH_RISK_JURISDICTION,
        "JURISDICTION",
    ),
    "CRYPTO_OFF_RAMP": (RuleKind.AGGREGATE, "CRYPTO"),
    "SELF_CUSTODY_OUTFLOW": (RuleKind.THRESHOLD, "CRYPTO"),
    "BORDER_ZONE_CASH": (RuleKind.AGGREGATE, "STRUCTURING"),
    "ROUND_AMOUNT_FX_LAYERING": (RuleKind.AGGREGATE, "LAYERING"),
    "DORMANT_REACTIVATION": (RuleKind.VELOCITY, "DORMANCY"),
    "COUNTERPARTY_TRUE_MATCH": (RuleKind.THRESHOLD, "SCREENING"),
    "BLOCKED_PERSON_PARTY": (RuleKind.THRESHOLD, "SCREENING"),
    "PEP_LARGE_OPERATION": (RuleKind.THRESHOLD, "PEP"),
    "TIER_N1_MONTHLY_OPS": (RuleKind.TIER_LIMIT, "LIMIT"),
    "TIER_N2_MONTHLY_OPS": (RuleKind.TIER_LIMIT, "LIMIT"),
}
# Plan 4b B3: the templates the AVI pack also carries (Decisions in the PR).
AVI_TOO = frozenset({
    "RAPID_PASS_THROUGH",
    "NEW_ACCOUNT_VELOCITY",
    "CRYPTO_OFF_RAMP",
    "SELF_CUSTODY_OUTFLOW",
    "COUNTERPARTY_TRUE_MATCH",
    "BLOCKED_PERSON_PARTY",
    "PEP_LARGE_OPERATION",
})
SEVERITY = {
    "COUNTERPARTY_TRUE_MATCH": Severity.CRITICAL,
    "BLOCKED_PERSON_PARTY": Severity.CRITICAL,
    "PEP_LARGE_OPERATION": Severity.HIGH,
    "TIER_N1_MONTHLY_OPS": Severity.HIGH,
    "TIER_N2_MONTHLY_OPS": Severity.HIGH,
}
LIBRARY = read_library()


def _template(code: str) -> RuleDefinition:
    return next(t.definition for t in LIBRARY if t.code == code)


def test_the_shipped_library_has_the_sixteen_templates_of_the_spec() -> None:
    assert [t.code for t in LIBRARY] == sorted(SPEC_TABLE)
    lists = load_jurisdictions()
    for template in LIBRARY:
        definition = template.definition
        kind, typology = SPEC_TABLE[template.code]
        assert (definition.kind, definition.typology) == (kind, [typology])
        assert (template.version, template.country_code) == (1, "MX")
        expected = (ITF, AVI) if template.code in AVI_TOO else (ITF,)
        assert template.packs == expected, template.code
        assert definition.severity == SEVERITY.get(template.code, Severity.MEDIUM)
        assert definition.dedup.key is KeyKind.RELATIONSHIP
        assert template.definition_hash == definition.definition_hash()
        assert template.source_path == str(TYPOLOGIES_DIR / f"{template.code}.yaml")
        for name, param in definition.params.items():
            assert param.problem() is None, (template.code, name)
            if param.unit == "USD":
                assert definition.unit_key == USD, template.code
        declared = {declaration.name for declaration in definition.aggregates}
        assert set(definition.evidence.aggregates) == declared, template.code
        # Every list name resolves against the shipped lists (no $TAX_HAVEN).
        _ = resolve_definition(definition, lists)


def test_cash_structuring_carries_the_spec_bounds() -> None:
    rule = _template("CASH_STRUCTURING")
    threshold = rule.params["threshold"]
    assert (threshold.value, threshold.min, threshold.tighten, threshold.unit) == (
        Decimal(5000),
        Decimal(1000),
        "LOWER",
        "USD",
    )
    near = rule.params["near_ratio"]
    assert (near.value, near.min, near.max) == (
        Decimal("0.70"),
        Decimal("0.50"),
        Decimal("0.95"),
    )
    assert rule.params["window"].value == "P7D"
    assert (rule.params["min_count"].value, rule.params["min_count"].min) == (3, 2)
    band = {(d.name, d.where.amount_gte, d.where.amount_lt) for d in rule.aggregates}
    assert band == {
        ("near_count", Decimal(3500), Decimal(5000)),
        ("near_sum", Decimal(3500), Decimal(5000)),
    }


def test_the_shipped_declarations_follow_their_params() -> None:
    """The declared band and windows are the parameters' defaults.

    A tuned version rewrites them from its params in B5's ``build_definition``;
    until then, these pin the coupling in the shipped defaults.
    """
    structuring = _template("CASH_STRUCTURING")
    threshold = cast("Decimal", structuring.params["threshold"].value)
    near_ratio = cast("Decimal", structuring.params["near_ratio"].value)
    window = structuring.params["window"].value
    for declaration in structuring.aggregates:
        assert declaration.where.amount_gte == near_ratio * threshold
        assert declaration.where.amount_lt == threshold
        assert declaration.window == window
    for code in ("NEW_ACCOUNT_VELOCITY", "DORMANT_REACTIVATION"):
        rule = _template(code)
        windowed = [
            declaration
            for declaration in rule.aggregates
            if declaration.function in {AggregateFunction.COUNT, AggregateFunction.SUM}
        ]
        assert windowed, code
        for declaration in windowed:
            assert declaration.window == rule.params["window"].value, code


def test_dormancy_cannot_outrun_the_last_seen_lookback() -> None:
    rule = _template("DORMANT_REACTIVATION")
    last_seen = next(d for d in rule.aggregates if d.name == "last_seen")
    assert last_seen.window == "P6M"
    assert rule.params["dormant_days"].max == 180


@pytest.mark.parametrize(
    ("edit", "replacement", "problem"),
    [
        ("version: 1", 'version: "1"', "version: Input should be a valid integer"),
        ("version: 1", "version: true", "version: Input should be a valid integer"),
        (
            "packs: [MX/ITF-IFPE]",
            "packs: [ITF-IFPE]",
            "packs.0: String should match pattern '^[A-Z]{2}/[A-Z0-9-]+$'",
        ),
        (
            "packs: [MX/ITF-IFPE]",
            "packs: [MX/ITF-IFPE, MX/ITF-IFPE]",
            "packs repeats a pack",
        ),
        (
            "packs: [MX/ITF-IFPE]",
            "packs: [MX/ITF-IFPE, CO/SARLAFT]",
            "pack CO/SARLAFT is not of country MX",
        ),
    ],
)
def test_the_document_fields_are_strict(
    tmp_path: Path, edit: str, replacement: str, problem: str
) -> None:
    path = _copy(tmp_path, "CASH_STRUCTURING")
    _rewrite(path, edit, replacement)
    with pytest.raises(ValidationFailedError) as caught:
        _ = read_typology(path)
    assert caught.value.detail == f"CASH_STRUCTURING.yaml: {problem}"


@pytest.mark.parametrize(
    ("code", "outcome"),
    [("TIER_N1_MONTHLY_OPS", "BLOCK"), ("TIER_N2_MONTHLY_OPS", "REVIEW")],
)
def test_the_tier_templates_read_nothing_but_the_outcome(
    code: str, outcome: str
) -> None:
    rule = _template(code)
    assert set(rule.params) == {"outcome"}
    assert rule.params["outcome"].value == outcome
    assert rule.params["outcome"].choices == ["BLOCK", "REVIEW"]
    assert (rule.kind, rule.sync) == (RuleKind.TIER_LIMIT, True)
    assert (rule.dedup.window, rule.dedup.cooldown) == (MONTH, "P0D")
    assert rule.segment is not None
    assert rule.segment.tier == [code.split("_")[1]]
    assert rule.aggregates == [
        AggregateDeclaration(
            name="month_in",
            function=AggregateFunction.SUM,
            measure=Measure.UNIT_AMOUNT,
            unit_key="UDI/LAST_DAY_PREV_MONTH",
            key_kind=KeyKind.RELATIONSHIP,
            window_kind=WindowKind.MONTH,
            window="MONTH",
            where=TxnFilter(direction=frozenset({Direction.IN})),
        )
    ]


def _copy(tmp_path: Path, code: str, name: str | None = None) -> Path:
    target = tmp_path / f"{name or code}.yaml"
    _ = shutil.copy(TYPOLOGIES_DIR / f"{code}.yaml", target)
    return target


def test_a_file_whose_code_differs_from_its_name_is_refused(tmp_path: Path) -> None:
    path = _copy(tmp_path, "CASH_STRUCTURING", "CASH_STRUCTURING_V2")
    with pytest.raises(ValidationFailedError) as caught:
        _ = read_typology(path)
    assert caught.value.detail == (
        "CASH_STRUCTURING_V2.yaml: the code CASH_STRUCTURING differs from the file name"
    )
    with pytest.raises(ValidationFailedError, match=r"^CASH_STRUCTURING_V2\.yaml: "):
        _ = read_library(tmp_path)


def _rewrite(path: Path, edit: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert edit in text
    _ = path.write_text(text.replace(edit, replacement), encoding="utf-8")


def test_an_invalid_file_is_refused_naming_it(tmp_path: Path) -> None:
    path = _copy(tmp_path, "CASH_STRUCTURING")
    _rewrite(path, 'value: "0.70"', "value: 0.70")
    with pytest.raises(ValidationFailedError) as caught:
        _ = read_typology(path)
    assert caught.value.detail == (
        "CASH_STRUCTURING.yaml: definition: params.near_ratio.value: "
        "write a decimal as a string, never a float"
    )
    unquoted = _copy(tmp_path, "SELF_CUSTODY_OUTFLOW")
    _rewrite(unquoted, '"on":', "on:")
    with pytest.raises(ValidationFailedError, match=r"^SELF_CUSTODY_OUTFLOW\.yaml: "):
        _ = read_typology(unquoted)
    for name, text in (("LIST.yaml", "- 1\n"), ("BROKEN.yaml", "a: [\n")):
        bad = tmp_path / name
        _ = bad.write_text(text, encoding="utf-8")
        with pytest.raises(ValidationFailedError) as refused:
            _ = read_typology(bad)
        assert refused.value.detail == f"{name}: not a typology file"


def test_a_capture_family_template_is_refused(tmp_path: Path) -> None:
    path = _copy(tmp_path, "SELF_CUSTODY_OUTFLOW")
    _rewrite(path, "family: ALERT", "family: CAPTURE")
    with pytest.raises(ValidationFailedError) as caught:
        _ = read_typology(path)
    assert caught.value.detail == (
        "SELF_CUSTODY_OUTFLOW.yaml: a template is an ALERT rule, not CAPTURE"
    )


@pytest.mark.parametrize("code", sorted(SPEC_TABLE))
@pytest.mark.parametrize("locale", ["es-MX", "en"])
def test_every_template_renders_its_golden_sentence(code: str, locale: Locale) -> None:
    name = f"{code}.{locale}.txt"
    expected = (GOLDEN / "render" / name).read_text(encoding="utf-8")
    actual = _template(code).render(locale)
    if actual != expected.removesuffix("\n"):
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


def test_hashes_are_pinned() -> None:
    pinned = cast(
        "dict[str, dict[int, str]]",
        yaml.safe_load((GOLDEN / "hashes.yaml").read_text(encoding="utf-8")),
    )
    assert {
        template.code: {template.version: template.definition_hash.hex()}
        for template in LIBRARY
    } == pinned
