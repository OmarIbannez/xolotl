"""The name matcher (SP-03 spec §4.2).

Normalization, phonetic keys, nicknames, IDF weights, the in-memory candidate index,
the explaining scorer and bands.

Pure: imports only the standard library, its string libraries, ``screening.lists``
and ``xolotl.platform.errors``/``xolotl.platform.enums``.
"""

from xolotl.modules.screening.matching.bands import Band, BandThresholds, band_for
from xolotl.modules.screening.matching.idf import IdfTable
from xolotl.modules.screening.matching.index import (
    BlockingPlan,
    Candidate,
    IndexedEntry,
    MemoryIndex,
    blocking_plan,
    name_keys,
    token_keys,
)
from xolotl.modules.screening.matching.nicknames import are_nicknames
from xolotl.modules.screening.matching.normalize import (
    MATCHER_VERSION,
    NormalizedName,
    Token,
    TokenRole,
    normalize_name,
)
from xolotl.modules.screening.matching.phonetic import phonetic_key
from xolotl.modules.screening.matching.query import NameQuery, QueryIdentifier
from xolotl.modules.screening.matching.scorer import (
    MatchExplanation,
    MatchKind,
    MatchScore,
    QualifierOutcome,
    QualifierResult,
    RuleApplied,
    ScoringConfig,
    TokenAlignment,
    score,
)

__all__ = [
    "MATCHER_VERSION",
    "Band",
    "BandThresholds",
    "BlockingPlan",
    "Candidate",
    "IdfTable",
    "IndexedEntry",
    "MatchExplanation",
    "MatchKind",
    "MatchScore",
    "MemoryIndex",
    "NameQuery",
    "NormalizedName",
    "QualifierOutcome",
    "QualifierResult",
    "QueryIdentifier",
    "RuleApplied",
    "ScoringConfig",
    "Token",
    "TokenAlignment",
    "TokenRole",
    "are_nicknames",
    "band_for",
    "blocking_plan",
    "name_keys",
    "normalize_name",
    "phonetic_key",
    "score",
    "token_keys",
]
