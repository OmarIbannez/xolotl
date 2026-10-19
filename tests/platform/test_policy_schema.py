from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from xolotl.platform.policy import (
    PackConfig,
    Quantity,
    ScreeningBands,
    add_iso_duration,
    iso_duration_days,
    unverified_paths,
)


def _prov(value: object, confidence: str = "VERIFIED") -> dict[str, object]:
    return {
        "value": value,
        "legal_basis": "test basis",
        "confidence": confidence,
        "verified_as_of": "2026-10-18",
    }


def _capture(code: str, **extra: object) -> dict[str, object]:
    return {
        "code": code,
        "kind": "THRESHOLD",
        "threshold": _prov({"amount": "100", "unit": "USD"}),
        "conversion_rule": "SAME_DAY",
        "period": _prov("MONTH"),
        "report": "R1",
        "filing": "SERIALIZED",
        **extra,
    }


def _pack(**changes: object) -> dict[str, object]:
    pack: dict[str, object] = {
        "pack": "MX/TEST-ONE",
        "version": 1,
        "title": "Test pack",
        "effective_from": "2026-01-01",
        "country": "MX",
        "regulators": ["CNBV"],
        "products": ["EMONEY"],
        "units": {"quote_ccy": "MXN"},
        "kyc": {
            "tiers": [
                {
                    "code": "N1",
                    "limits": {"monthly_ops": _prov({"amount": "750", "unit": "UDI"})},
                    "required_fields": ["given_names"],
                    "required_checks": ["SCREENING"],
                    "assurance": "A0",
                }
            ],
            "review_frequency": {"HIGH": _prov("P6M"), "LOW": _prov("P24M")},
            "geolocation_required": _prov(value=True),
        },
        "ubo": {
            "ownership_threshold_pct": _prov("25"),
            "control_tests": ["VOTING_RIGHTS"],
            "fallback": "SENIOR_MANAGING_OFFICIAL",
        },
        "screening": {
            "mandatory_lists": [_prov("MX_LPB")],
            "recommended_lists": ["OFAC_SDN"],
            "pep": {"cooling_off": {"DOMESTIC": _prov("P1Y"), "FOREIGN": _prov(None)}},
            "on_true_match": {"MX_LPB": {"action": "SUSPEND_IMMEDIATELY"}},
        },
        "captures": [_capture("C1"), _capture("C2")],
        "reports": [
            {
                "code": "R1",
                "anchor": "period_end",
                "deadline": _prov({"kind": "HOURS_AFTER", "params": {"hours": 24}}),
                "zero_report": False,
            }
        ],
        "retention": {
            "kyc_after_relationship": _prov("P10Y"),
            "transactions": _prov("P10Y"),
        },
        "governance": {"committee_required": _prov(value=True)},
    }
    pack.update(changes)
    return pack


def test_quantity_amounts_are_normalised():
    seven_fifty = Quantity(amount=Decimal("750.0"), unit="UDI").amount
    assert seven_fifty == Decimal(750)
    assert str(seven_fifty) == "750"
    assert str(Quantity(amount=Decimal(1000), unit="UDI").amount) == "1000"
    assert str(Quantity(amount=Decimal("0.50"), unit="USD").amount) == "0.5"
    with pytest.raises(ValidationError):
        _ = Quantity(amount=Decimal(0), unit="UDI")
    with pytest.raises(ValidationError):
        _ = Quantity.model_validate({"amount": "1", "unit": "EUR"})


def test_band_defaults_normalise_like_written_values():
    assert ScreeningBands().model_dump(mode="json") == ScreeningBands.model_validate({
        "sanctions_alert": "0.70"
    }).model_dump(mode="json")
    assert ScreeningBands().model_dump(mode="json")["sanctions_alert"] == "0.7"


def test_iso_durations():
    assert iso_duration_days("P10Y") == 3600
    assert iso_duration_days("P1Y") == iso_duration_days("P12M") == 360
    assert iso_duration_days("P6M") == 180
    assert iso_duration_days("P90D") == 90
    assert iso_duration_days("P1Y2M3D") == 360 + 60 + 3
    for text in ("P1W", "10Y", "P", "PT24H"):
        with pytest.raises(ValueError, match="ISO-8601"):
            _ = iso_duration_days(text)


def test_add_iso_duration():
    assert add_iso_duration(date(2026, 1, 31), "P1M") == date(2026, 2, 28)
    assert add_iso_duration(date(2024, 2, 29), "P1Y") == date(2025, 2, 28)
    assert add_iso_duration(date(2026, 3, 15), "P10Y") == date(2036, 3, 15)
    assert add_iso_duration(date(2026, 12, 31), "P45D") == date(2027, 2, 14)
    assert add_iso_duration(date(2026, 11, 30), "P2M") == date(2027, 1, 30)


def test_the_minimal_pack_is_valid():
    config = PackConfig.model_validate(_pack())
    assert config.kyc.tiers[0].limits.max_balance is None
    assert config.screening.pep.cooling_off["FOREIGN"].value is None
    assert unverified_paths(config) == []


def test_a_pack_with_an_unknown_key_is_refused():
    with pytest.raises(ValidationError, match="extra"):
        _ = PackConfig.model_validate(_pack(colour="red"))
    with pytest.raises(ValidationError, match="extra"):
        _ = PackConfig.model_validate(
            _pack(retention={"kyc_after_relationship": _prov("P1Y"), "x": 1})
        )


def test_duplicate_capture_codes_are_refused():
    with pytest.raises(ValidationError, match="capture codes"):
        _ = PackConfig.model_validate(_pack(captures=[_capture("C1"), _capture("C1")]))


def test_merge_into_must_name_a_capture():
    with pytest.raises(ValidationError, match="merge_into"):
        _ = PackConfig.model_validate(
            _pack(captures=[_capture("C1", merge_into=_prov("NOPE"))])
        )
    with pytest.raises(ValidationError, match="merge_into"):
        _ = PackConfig.model_validate(
            _pack(captures=[_capture("C1", merge_into=_prov("C1"))])
        )
    merged = PackConfig.model_validate(
        _pack(captures=[_capture("C1"), _capture("C2", merge_into=_prov("C1"))])
    )
    assert merged.captures[1].merge_into is not None


@pytest.mark.parametrize(
    "changes",
    [
        {"pack": "US/TEST-ONE"},
        {"pack": "MX/test"},
        {"version": 0},
        {"effective_to": "2025-12-31"},
        {"regulators": []},
        {"products": []},
        {"country": "mx"},
    ],
)
def test_pack_header_rules(changes: dict[str, object]):
    with pytest.raises(ValidationError):
        _ = PackConfig.model_validate(_pack(**changes))


def test_unverified_paths_walk_every_provenanced_field():
    raw = _pack()
    kyc = raw["kyc"]
    screening = raw["screening"]
    reports = raw["reports"]
    assert isinstance(kyc, dict)
    assert isinstance(screening, dict)
    assert isinstance(reports, list)
    kyc["geolocation_required"] = _prov(value=True, confidence="UNVERIFIED")
    kyc["review_frequency"] = {
        "HIGH": _prov("P6M", "UNVERIFIED"),
        "LOW": _prov("P24M"),
    }
    screening["pep"] = {"cooling_off": {"DOMESTIC": _prov("P1Y", "UNVERIFIED")}}
    reports[0]["deadline"] = _prov(
        {"kind": "HOURS_AFTER", "params": {"hours": 24}}, "UNVERIFIED"
    )
    assert unverified_paths(PackConfig.model_validate(raw)) == [
        "kyc.geolocation_required",
        "kyc.review_frequency[HIGH]",
        "reports[R1].deadline",
        "screening.pep.cooling_off[DOMESTIC]",
    ]


def test_unverified_paths_name_list_items_by_code_or_value():
    raw = _pack(
        captures=[
            _capture(
                "C1", threshold=_prov({"amount": "1", "unit": "USD"}, "UNVERIFIED")
            )
        ],
        screening={
            "mandatory_lists": [_prov("MX_LPB", "UNVERIFIED")],
            "recommended_lists": [],
            "pep": {"cooling_off": {}},
            "on_true_match": {},
        },
        units={"tier_limit_rule": _prov("LAST_DAY_PREV_MONTH", "UNVERIFIED")},
    )
    assert unverified_paths(PackConfig.model_validate(raw)) == [
        "captures[C1].threshold",
        "screening.mandatory_lists[MX_LPB]",
        "units.tier_limit_rule",
    ]
