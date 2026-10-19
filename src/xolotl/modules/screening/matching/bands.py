"""Bands from a score and the policy's thresholds (SP-03 spec §4.2, FR-SC-005)."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from xolotl.modules.screening.matching.scorer import MatchScore


class Band(StrEnum):
    ALERT = "ALERT"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    AUTO_DISCARDED = "AUTO_DISCARDED"
    BELOW_CUTOFF = "BELOW_CUTOFF"


@dataclass(frozen=True, slots=True)
class BandThresholds:
    alert: Decimal
    review: Decimal


def band_for(result: MatchScore, thresholds: BandThresholds) -> Band:
    """Band a scored hit.

    A hit capped by a conflicting identifier whose uncapped score would have reached
    ``review`` is ``AUTO_DISCARDED`` (stored, never queued). On an identifier-only
    list a subject without the identifier reaches ``LOW_CONFIDENCE`` at most.

    Returns:
        The band.

    """
    explanation = result.explanation
    codes = {rule.code for rule in explanation.rules}
    if "ID_CONFLICT_CAP" in codes:
        uncapped = explanation.name_score + sum(
            q.adjustment for q in explanation.qualifiers if q.attribute != "IDENTIFIER"
        )
        if Decimal(repr(uncapped)) >= thresholds.review:
            return Band.AUTO_DISCARDED
    if result.score >= thresholds.alert:
        capped = "IDENTIFIER_ONLY_LIST" in codes and "ID_MATCH_FLOOR" not in codes
        return Band.LOW_CONFIDENCE if capped else Band.ALERT
    if result.score >= thresholds.review:
        return Band.LOW_CONFIDENCE
    return Band.BELOW_CUTOFF
