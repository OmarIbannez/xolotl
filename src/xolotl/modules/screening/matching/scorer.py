"""The explaining scorer (SP-03 spec §4.2; FR-SC-004, FR-SC-006).

``score`` compares a query with one list entry and says why:

1. **Name score.** Every query name is compared with every entry name; the best
   pair wins. Tokens are aligned one to one, best first, by exact equality,
   nickname equivalence, phonetic key, initial or Jaro-Winkler similarity. Each
   token weighs its IDF weight times its role (surnames above given names);
   particles, honorifics, generic words and legal forms weigh nothing. The score is
   the aligned similarity over the aligned weight plus the unaligned weight. When
   every token of the shorter name is aligned, the longer name's extra tokens count
   at a fraction (a dropped second surname or middle name is tolerated). A match
   that carries little information (only common tokens) is scaled down, and a
   low-quality alias scales it down again.
2. **Qualifiers** adjust additively within a bounded total: birth date by
   precision, nationality, country and gender.
3. **Rules:** person against organization scores 0; the same identifier type with
   the same value floors the score at ``id_match_floor``, with a different value
   caps it at ``id_conflict_cap``; on identifier-only lists (69-B) an equal RFC
   scores 1.0 and a subject without an RFC can reach ``LOW_CONFIDENCE`` at most
   (``band_for`` applies that cap, because the alert threshold is policy).

Every parameter here is frozen per ``MATCHER_VERSION``; T6's corpus tunes them.
"""

import hashlib
import json
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from operator import itemgetter
from typing import Literal

from rapidfuzz.distance import JaroWinkler

from xolotl.modules.screening.lists import (
    EntityType,
    IdType,
    JsonValue,
    ListEntry,
    NameQuality,
    PartialDate,
)
from xolotl.modules.screening.matching.idf import WEIGHTED_ROLES, IdfTable
from xolotl.modules.screening.matching.names import entry_names
from xolotl.modules.screening.matching.nicknames import are_nicknames
from xolotl.modules.screening.matching.normalize import (
    NormalizedName,
    Token,
    TokenRole,
)
from xolotl.modules.screening.matching.query import NameQuery, compatible, query_names

ROLE_WEIGHT = {TokenRole.SURNAME: 1.0, TokenRole.UNKNOWN: 0.85, TokenRole.GIVEN: 0.7}
# Unaligned tokens of the longer name count at this fraction when the shorter name
# is fully aligned.
DROP_WEIGHT = {TokenRole.SURNAME: 0.3, TokenRole.GIVEN: 0.5, TokenRole.UNKNOWN: 1.0}
NICKNAME_SIMILARITY = 0.95
PHONETIC_SIMILARITY = 0.9
INITIAL_SIMILARITY = 0.8
FUZZY_FLOOR = 0.82
# A leftover token is a misspelt weightless word at this Jaro-Winkler similarity.
ABSORB_FLOOR = 0.75
SHORT_PHONETIC_SIMILARITY = 0.85
# Aligned weight at which a match carries full information, and the factor applied
# to a match carrying none.
INFO_FULL = 1.5
INFO_BASE = 0.6
WEAK_ALIAS_FACTOR = 0.9
QUALIFIER_RANGE = (-0.25, 0.12)
_PLACES = Decimal("0.0001")
_BIRTH_YEAR_TOLERANCE = 2
_MASK_KEEP = 4


class MatchKind(StrEnum):
    EXACT = "EXACT"
    PHONETIC = "PHONETIC"
    NICKNAME = "NICKNAME"
    INITIAL = "INITIAL"
    FUZZY = "FUZZY"


class QualifierOutcome(StrEnum):
    MATCH = "MATCH"
    PARTIAL = "PARTIAL"
    CONFLICT = "CONFLICT"
    ABSENT = "ABSENT"


type Attribute = Literal[
    "BIRTH_DATE", "NATIONALITY", "COUNTRY", "GENDER", "ENTITY_TYPE", "IDENTIFIER"
]
type RuleCode = Literal[
    "ID_MATCH_FLOOR", "ID_CONFLICT_CAP", "IDENTIFIER_ONLY_LIST", "ENTITY_TYPE_MISMATCH"
]


@dataclass(frozen=True, slots=True)
class TokenAlignment:
    query: str
    entry: str
    kind: MatchKind
    similarity: float
    weight: float
    role: TokenRole


@dataclass(frozen=True, slots=True)
class QualifierResult:
    attribute: Attribute
    outcome: QualifierOutcome
    adjustment: float
    detail: str


@dataclass(frozen=True, slots=True)
class RuleApplied:
    code: RuleCode
    detail: str


@dataclass(frozen=True, slots=True)
class MatchExplanation:
    query_name: str
    entry_name: str
    entry_name_quality: NameQuality
    name_score: float
    alignment: tuple[TokenAlignment, ...]
    unmatched_query: tuple[str, ...]
    unmatched_entry: tuple[str, ...]
    qualifiers: tuple[QualifierResult, ...]
    rules: tuple[RuleApplied, ...]

    def to_json(self) -> dict[str, JsonValue]:
        """Serialize for ``screening_hit.features``.

        Returns:
            A JSON object with every field; floats rounded to 4 places.

        """
        return {
            "query_name": self.query_name,
            "entry_name": self.entry_name,
            "entry_name_quality": self.entry_name_quality.value,
            "name_score": round(self.name_score, 4),
            "alignment": [
                {
                    "query": a.query,
                    "entry": a.entry,
                    "kind": a.kind.value,
                    "similarity": round(a.similarity, 4),
                    "weight": round(a.weight, 4),
                    "role": a.role.value,
                }
                for a in self.alignment
            ],
            "unmatched_query": list(self.unmatched_query),
            "unmatched_entry": list(self.unmatched_entry),
            "qualifiers": [
                {
                    "attribute": q.attribute,
                    "outcome": q.outcome.value,
                    "adjustment": round(q.adjustment, 4),
                    "detail": q.detail,
                }
                for q in self.qualifiers
            ],
            "rules": [{"code": r.code, "detail": r.detail} for r in self.rules],
        }


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    """Matcher parameters from the effective policy. Bands are not here."""

    identifier_only_lists: frozenset[str] = field(
        default_factory=lambda: frozenset({"MX_SAT_69B"})
    )
    id_match_floor: Decimal = Decimal("0.95")
    id_conflict_cap: Decimal = Decimal("0.30")

    def config_hash(self) -> bytes:
        """Hash the configuration; stored on every run.

        Returns:
            The 32-byte SHA-256 digest of the canonical JSON.

        """
        data = {
            "identifier_only_lists": sorted(self.identifier_only_lists),
            "id_match_floor": str(self.id_match_floor),
            "id_conflict_cap": str(self.id_conflict_cap),
        }
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).digest()


@dataclass(frozen=True, slots=True)
class MatchScore:
    score: Decimal
    explanation: MatchExplanation


# ---- tokens ----------------------------------------------------------------------


def _weighted(name: NormalizedName) -> list[Token]:
    return [t for t in name.tokens if t.role in WEIGHTED_ROLES]


def _token_weight(token: Token, idf: IdfTable) -> float:
    return idf.weight(token.folded) * ROLE_WEIGHT.get(token.role, 0.0)


def compare_tokens(query: Token, entry: Token) -> tuple[MatchKind, float] | None:
    """Compare two tokens.

    Returns:
        How they match and their similarity in ``(0, 1]``, or ``None``.

    """
    if query.folded == entry.folded:
        return MatchKind.EXACT, 1.0
    if are_nicknames(query.folded, entry.folded):
        return MatchKind.NICKNAME, NICKNAME_SIMILARITY
    fuzzy = JaroWinkler.similarity(query.folded, entry.folded)
    phonetic = _phonetic_match(query, entry, fuzzy)
    if phonetic is not None:
        return phonetic
    short, long_ = sorted((query.folded, entry.folded), key=len)
    if len(short) == 1 and long_.startswith(short):
        return MatchKind.INITIAL, INITIAL_SIMILARITY
    if fuzzy >= FUZZY_FLOOR:
        return MatchKind.FUZZY, fuzzy
    return None


def _phonetic_match(
    query: Token, entry: Token, fuzzy: float
) -> tuple[MatchKind, float] | None:
    if query.phonetic != entry.phonetic:
        return None
    if len(query.phonetic) > 1:
        return MatchKind.PHONETIC, max(PHONETIC_SIMILARITY, fuzzy)
    # One-sound keys ("su" and "zu", "ho" and "o") only count between tokens whose
    # lengths differ by one letter at most.
    if abs(len(query.folded) - len(entry.folded)) <= 1:
        return MatchKind.PHONETIC, max(SHORT_PHONETIC_SIMILARITY, fuzzy)
    return None


@dataclass(frozen=True, slots=True)
class _PairResult:
    score: float
    alignment: tuple[TokenAlignment, ...]
    unmatched_query: tuple[str, ...]
    unmatched_entry: tuple[str, ...]


def _align(
    query: list[Token], entry: list[Token], idf: IdfTable
) -> tuple[list[tuple[int, int, MatchKind, float, float]], list[float], list[float]]:
    q_weights = [_token_weight(t, idf) for t in query]
    e_weights = [_token_weight(t, idf) for t in entry]
    options: list[tuple[float, int, int, MatchKind, float, float]] = []
    for qi, q in enumerate(query):
        for ei, e in enumerate(entry):
            found = compare_tokens(q, e)
            if found is not None:
                kind, similarity = found
                weight = (q_weights[qi] + e_weights[ei]) / 2
                options.append((similarity * weight, qi, ei, kind, similarity, weight))
    # Best pairs first; ties keep query order for determinism.
    options.sort(key=lambda o: (-o[0], o[1], o[2]))
    used_q: set[int] = set()
    used_e: set[int] = set()
    chosen: list[tuple[int, int, MatchKind, float, float]] = []
    for _value, qi, ei, kind, similarity, weight in options:
        if qi in used_q or ei in used_e:
            continue
        used_q.add(qi)
        used_e.add(ei)
        chosen.append((qi, ei, kind, similarity, weight))
    return chosen, q_weights, e_weights


def _unaligned_weight(
    tokens: list[Token], weights: list[float], used: set[int], *, tolerate: bool
) -> float:
    total = 0.0
    for index, token in enumerate(tokens):
        if index in used:
            continue
        fraction = DROP_WEIGHT.get(token.role, 1.0) if tolerate else 1.0
        total += weights[index] * fraction
    return total


def _loosely(token: Token, other: Token) -> tuple[MatchKind, float] | None:
    similarity = JaroWinkler.similarity(token.folded, other.folded)
    return (MatchKind.FUZZY, similarity) if similarity >= ABSORB_FLOOR else None


def _absorb(
    tokens: list[Token], used: set[int], others: NormalizedName
) -> list[TokenAlignment]:
    # A weighted token left over that resembles a weightless word on the other side
    # ("corporatino" against "corporation", "sr" against "sa") is a misspelling of a
    # word that carries no weight; it costs nothing either.
    weightless = [t for t in others.tokens if t.role not in WEIGHTED_ROLES]
    absorbed: list[TokenAlignment] = []
    for index, token in enumerate(tokens):
        if index in used:
            continue
        for other in weightless:
            found = compare_tokens(token, other) or _loosely(token, other)
            if found is not None:
                used.add(index)
                absorbed.append(
                    TokenAlignment(
                        token.folded, other.folded, found[0], found[1], 0.0, other.role
                    )
                )
                break
    return absorbed


def _pair_score(
    query: NormalizedName, entry: NormalizedName, idf: IdfTable
) -> _PairResult:
    q_tokens, e_tokens = _weighted(query), _weighted(entry)
    chosen, q_weights, e_weights = _align(q_tokens, e_tokens, idf)
    used_q = {c[0] for c in chosen}
    used_e = {c[1] for c in chosen}
    shorter_done = len(chosen) == min(len(q_tokens), len(e_tokens))
    absorbed = _absorb(q_tokens, used_q, entry) + [
        TokenAlignment(a.entry, a.query, a.kind, a.similarity, a.weight, a.role)
        for a in _absorb(e_tokens, used_e, query)
    ]
    aligned_weight = sum(c[4] for c in chosen)
    aligned_value = sum(c[3] * c[4] for c in chosen)
    denominator = (
        aligned_weight
        + _unaligned_weight(q_tokens, q_weights, used_q, tolerate=shorter_done)
        + _unaligned_weight(e_tokens, e_weights, used_e, tolerate=shorter_done)
    )
    raw = aligned_value / denominator if denominator > 0 else 0.0
    information = INFO_BASE + (1 - INFO_BASE) * min(1.0, aligned_value / INFO_FULL)
    aligned = [
        TokenAlignment(
            query=q_tokens[qi].folded,
            entry=e_tokens[ei].folded,
            kind=kind,
            similarity=similarity,
            weight=weight,
            role=e_tokens[ei].role,
        )
        for qi, ei, kind, similarity, weight in sorted(chosen)
    ]
    return _PairResult(
        score=raw * information,
        alignment=tuple(aligned + absorbed),
        unmatched_query=tuple(
            t.folded for i, t in enumerate(q_tokens) if i not in used_q
        ),
        unmatched_entry=tuple(
            t.folded for i, t in enumerate(e_tokens) if i not in used_e
        ),
    )


@dataclass(frozen=True, slots=True)
class _BestName:
    score: float
    query: NormalizedName
    entry: NormalizedName
    quality: NameQuality
    pair: _PairResult


def _best_name(query: NameQuery, entry: ListEntry, idf: IdfTable) -> _BestName | None:
    best: _BestName | None = None
    for q_name in query_names(query):
        for e_name, quality in entry_names(entry):
            pair = _pair_score(q_name, e_name, idf)
            value = pair.score * (
                WEAK_ALIAS_FACTOR if quality is NameQuality.WEAK else 1
            )
            if best is None or value > best.score:
                best = _BestName(value, q_name, e_name, quality, pair)
    return best


# ---- qualifiers --------------------------------------------------------------------


def _birth_date(query: NameQuery, entry: ListEntry) -> QualifierResult | None:
    if query.birth_date is None or query.entity_type is not EntityType.PERSON:
        return None
    if not entry.birth_dates:
        return QualifierResult("BIRTH_DATE", QualifierOutcome.ABSENT, 0.0, "not listed")
    born = query.birth_date
    results = [
        _compare_date(born.year, born.month, born.day, d) for d in entry.birth_dates
    ]
    outcome, adjustment, detail = max(results, key=itemgetter(1))
    return QualifierResult("BIRTH_DATE", outcome, adjustment, detail)


def _compare_date(
    year: int, month: int, day: int, listed: PartialDate
) -> tuple[QualifierOutcome, float, str]:
    if listed.year == year:
        if listed.month is None:
            return QualifierOutcome.MATCH, 0.05, f"year {year} matches"
        if listed.month == month and listed.day is None:
            return QualifierOutcome.MATCH, 0.06, f"{year}-{month:02d} matches"
        if listed.month == month and listed.day == day:
            return QualifierOutcome.MATCH, 0.08, "exact date matches"
        return QualifierOutcome.PARTIAL, 0.02, f"year {year} matches, day differs"
    if abs(listed.year - year) <= _BIRTH_YEAR_TOLERANCE:
        return QualifierOutcome.PARTIAL, 0.0, f"year {year} vs listed {listed.year}"
    return QualifierOutcome.CONFLICT, -0.15, f"year {year} vs listed {listed.year}"


def _set_qualifier(
    attribute: Attribute,
    ours: tuple[str, ...],
    theirs: set[str],
    adjustments: tuple[float, float],
) -> QualifierResult | None:
    if not ours:
        return None
    if not theirs:
        return QualifierResult(attribute, QualifierOutcome.ABSENT, 0.0, "not listed")
    common = sorted(set(ours) & theirs)
    if common:
        return QualifierResult(
            attribute, QualifierOutcome.MATCH, adjustments[0], ", ".join(common)
        )
    detail = f"{', '.join(sorted(ours))} vs listed {', '.join(sorted(theirs))}"
    outcome = QualifierOutcome.CONFLICT if adjustments[1] else QualifierOutcome.ABSENT
    return QualifierResult(attribute, outcome, adjustments[1], detail)


def _gender(query: NameQuery, entry: ListEntry) -> QualifierResult | None:
    if query.gender is None or entry.gender is None:
        return None
    if query.gender == entry.gender:
        return QualifierResult("GENDER", QualifierOutcome.MATCH, 0.02, entry.gender)
    detail = f"{query.gender} vs listed {entry.gender}"
    return QualifierResult("GENDER", QualifierOutcome.CONFLICT, -0.10, detail)


def _qualifiers(query: NameQuery, entry: ListEntry) -> list[QualifierResult]:
    places = {a.country for a in entry.addresses if a.country} | set(
        entry.nationalities
    )
    found = [
        _birth_date(query, entry),
        _set_qualifier(
            "NATIONALITY", query.nationalities, set(entry.nationalities), (0.03, -0.05)
        ),
        _set_qualifier("COUNTRY", query.countries, places, (0.02, 0.0)),
        _gender(query, entry),
    ]
    return [q for q in found if q is not None]


# ---- rules -------------------------------------------------------------------------


def _mask(value: str) -> str:
    return "****" + value[-_MASK_KEEP:]


@dataclass(slots=True)
class _Outcome:
    value: float
    rules: list[RuleApplied]
    qualifiers: list[QualifierResult]


def _identifier_rules(
    query: NameQuery, entry: ListEntry, config: ScoringConfig, outcome: _Outcome
) -> None:
    listed = {(i.id_type, i.normalized) for i in entry.identifiers}
    listed_types = {i.id_type for i in entry.identifiers}
    same_type = [q for q in query.identifiers if q.id_type in listed_types]
    matches = [q for q in same_type if (q.id_type, q.normalized) in listed]
    if matches:
        hit = matches[0]
        detail = f"{hit.id_type} {_mask(hit.normalized)} is listed"
        floor = float(config.id_match_floor)
        outcome.qualifiers.append(
            QualifierResult(
                "IDENTIFIER",
                QualifierOutcome.MATCH,
                max(0.0, floor - outcome.value),
                detail,
            )
        )
        outcome.rules.append(RuleApplied("ID_MATCH_FLOOR", detail))
        outcome.value = max(outcome.value, floor)
    elif same_type:
        miss = same_type[0]
        detail = f"{miss.id_type} {_mask(miss.normalized)} differs from the listed one"
        cap = float(config.id_conflict_cap)
        outcome.qualifiers.append(
            QualifierResult(
                "IDENTIFIER",
                QualifierOutcome.CONFLICT,
                min(0.0, cap - outcome.value),
                detail,
            )
        )
        outcome.rules.append(RuleApplied("ID_CONFLICT_CAP", detail))
        outcome.value = min(outcome.value, cap)


def _identifier_only_rule(
    query: NameQuery, entry: ListEntry, config: ScoringConfig, outcome: _Outcome
) -> None:
    if entry.list_code not in config.identifier_only_lists:
        return
    rfcs = {q.normalized for q in query.identifiers if q.id_type is IdType.RFC}
    listed = {i.normalized for i in entry.identifiers if i.id_type is IdType.RFC}
    if rfcs & listed:
        outcome.rules.append(RuleApplied("IDENTIFIER_ONLY_LIST", "RFC matches"))
        outcome.value = 1.0
    elif not rfcs:
        outcome.rules.append(
            RuleApplied(
                "IDENTIFIER_ONLY_LIST", "no RFC on the subject; LOW_CONFIDENCE at most"
            )
        )


def _quantize(value: float) -> Decimal:
    return Decimal(repr(min(1.0, max(0.0, value)))).quantize(_PLACES, ROUND_HALF_UP)


def score(
    query: NameQuery, entry: ListEntry, *, idf: IdfTable, config: ScoringConfig
) -> MatchScore:
    """Score one entry against a query and explain the score.

    Returns:
        The score in ``[0, 1]`` (4 decimal places) with its explanation.

    """
    best = _best_name(query, entry, idf)
    outcome = _Outcome(value=0.0, rules=[], qualifiers=[])
    name_score = 0.0 if best is None else best.score
    if not compatible(query.entity_type, entry.entity_type):
        detail = f"{query.entity_type} against {entry.entity_type}"
        outcome.rules.append(RuleApplied("ENTITY_TYPE_MISMATCH", detail))
        outcome.qualifiers.append(
            QualifierResult(
                "ENTITY_TYPE", QualifierOutcome.CONFLICT, -name_score, detail
            )
        )
    else:
        outcome.qualifiers = _qualifiers(query, entry)
        low, high = QUALIFIER_RANGE
        adjustment = min(high, max(low, sum(q.adjustment for q in outcome.qualifiers)))
        outcome.value = min(1.0, max(0.0, name_score + adjustment))
        _identifier_rules(query, entry, config, outcome)
        _identifier_only_rule(query, entry, config, outcome)
    return MatchScore(
        score=_quantize(outcome.value),
        explanation=MatchExplanation(
            query_name="" if best is None else best.query.folded,
            entry_name="" if best is None else best.entry.folded,
            entry_name_quality=NameQuality.PRIMARY if best is None else best.quality,
            name_score=name_score,
            alignment=() if best is None else best.pair.alignment,
            unmatched_query=() if best is None else best.pair.unmatched_query,
            unmatched_entry=() if best is None else best.pair.unmatched_entry,
            qualifiers=tuple(outcome.qualifiers),
            rules=tuple(outcome.rules),
        ),
    )
