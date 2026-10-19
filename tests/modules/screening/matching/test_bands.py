"""``band_for`` edges (SP-03 spec §4.2, FR-SC-005)."""

from decimal import Decimal

import pytest

from tests.modules.screening.matching.support import THRESHOLDS
from xolotl.modules.screening.lists import NameQuality
from xolotl.modules.screening.matching import (
    Band,
    MatchExplanation,
    MatchScore,
    QualifierOutcome,
    QualifierResult,
    RuleApplied,
    band_for,
)


def _result(value, name_score=0.0, rules=(), qualifiers=()):
    explanation = MatchExplanation(
        query_name="a",
        entry_name="a",
        entry_name_quality=NameQuality.PRIMARY,
        name_score=name_score,
        alignment=(),
        unmatched_query=(),
        unmatched_entry=(),
        qualifiers=tuple(qualifiers),
        rules=tuple(rules),
    )
    return MatchScore(Decimal(value), explanation)


@pytest.mark.parametrize(
    ("value", "band"),
    [
        ("0.7000", Band.ALERT),
        ("0.6999", Band.LOW_CONFIDENCE),
        ("0.5500", Band.LOW_CONFIDENCE),
        ("0.5499", Band.BELOW_CUTOFF),
    ],
)
def test_thresholds_are_inclusive(value, band):
    assert band_for(_result(value), THRESHOLDS) is band


def test_a_capped_hit_is_auto_discarded_only_if_it_would_have_reached_review():
    cap = (RuleApplied("ID_CONFLICT_CAP", "CURP ****0000 differs"),)
    identifier = QualifierResult("IDENTIFIER", QualifierOutcome.CONFLICT, -0.6, "x")
    birth = QualifierResult("BIRTH_DATE", QualifierOutcome.MATCH, 0.05, "x")
    high = _result("0.3000", 0.5, cap, (identifier, birth))
    assert band_for(high, THRESHOLDS) is Band.AUTO_DISCARDED
    low = _result("0.3000", 0.4, cap, (identifier, birth))
    assert band_for(low, THRESHOLDS) is Band.BELOW_CUTOFF
