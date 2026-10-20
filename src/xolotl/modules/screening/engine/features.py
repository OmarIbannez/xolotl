"""The stored explanation of a hit (``screening_hit.features``, SP-03 spec §8.4).

The library's explanation plus what a reviewer and a replay need: the list kind, the
thresholds the band was cut with, the matcher version, the scoring configuration's
hash and the exact entry. The query side holds normalized tokens only (D7). A
whitelisted hit also keeps the band it would have had, so a replay can compare it.
"""

from collections.abc import Mapping
from decimal import Decimal

from pydantic import JsonValue

from xolotl.modules.screening.matching import (
    MATCHER_VERSION,
    BandThresholds,
    MatchScore,
)

FEATURES_VERSION = 1
# Thresholds are shown with at least two decimals ("0.70", not the policy's "0.7").
_CENTS = Decimal("0.01")
_CENTS_EXPONENT = -2


def threshold_text(value: Decimal) -> str:
    """Write a threshold with at least two decimals.

    Returns:
        ``"0.70"`` for 0.7; a value with more decimals unchanged.

    """
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent > _CENTS_EXPONENT:
        return str(value.quantize(_CENTS))
    return str(value)


def hit_features(
    result: MatchScore,
    *,
    list_kind: str,
    thresholds: BandThresholds,
    config_hash: bytes,
    entry_ref: Mapping[str, JsonValue],
    band_before_whitelist: str | None = None,
) -> dict[str, JsonValue]:
    """Build the JSON stored with a hit.

    Returns:
        ``MatchExplanation.to_json()`` with ``v``, ``list_kind``, ``thresholds``
        (as strings), ``matcher_version``, ``config_hash`` (hex) and ``entry_ref``,
        plus ``band_before_whitelist`` when it is given (a whitelisted hit).

    """
    thresholds_json: dict[str, JsonValue] = {
        "alert": threshold_text(thresholds.alert),
        "review": threshold_text(thresholds.review),
    }
    extra: dict[str, JsonValue] = {
        "v": FEATURES_VERSION,
        "list_kind": list_kind,
        "thresholds": thresholds_json,
        "matcher_version": MATCHER_VERSION,
        "config_hash": config_hash.hex(),
        "entry_ref": dict(entry_ref),
    }
    if band_before_whitelist is not None:
        extra["band_before_whitelist"] = band_before_whitelist
    features = result.explanation.to_json()
    features.update(extra)
    return features
