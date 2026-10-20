"""The evaluation corpus and its report (SP-03 spec §4.3; FR-SC-014, NFR-SC-006).

``perturb`` derives the misspellings and rearrangements screening must survive
from a listed name: a dropped maternal surname, swapped order, lost accents, common
letter substitutions, nicknames, initials, particle changes, typos and truncation
at 60 characters, alone or two at a time. ``generate_pairs`` applies it to every
screenable entry with a fixed seed, so the same entries always give the same pairs.

``evaluate`` runs each query the way the engine does, through ``MemoryIndex`` then
``score`` and ``band_for``, and reports per list kind:

- ``recall``: share of true pairs whose target entry is banded ``LOW_CONFIDENCE`` or
  above (FR-SC-004);
- ``retrieval_recall``: share of true pairs whose target is among the candidates at
  ``top_k`` (NFR-SC-006);
- ``precision_at_alert``: share of ``ALERT`` hits that point at the right person,
  over true-pair queries and hard negatives alike;
- ``alerts_per_1000_negatives``: hard-negative queries with any ``ALERT``, per
  thousand.

A hit counts as the right person when its entry is the target or carries one of
the target's names (the same person listed by two sources).
"""

import csv
import hashlib
import string
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID

import yaml

from xolotl.modules.screening.lists import EntityType, ListEntry, NameQuality, parse
from xolotl.modules.screening.lists.types import JsonValue
from xolotl.modules.screening.matching.bands import Band, BandThresholds, band_for
from xolotl.modules.screening.matching.idf import IdfTable
from xolotl.modules.screening.matching.index import IndexedEntry, MemoryIndex
from xolotl.modules.screening.matching.names import entry_names
from xolotl.modules.screening.matching.nicknames import nickname_variants
from xolotl.modules.screening.matching.normalize import MATCHER_VERSION
from xolotl.modules.screening.matching.query import NameQuery
from xolotl.modules.screening.matching.scorer import ScoringConfig, score
from xolotl.modules.screening.matching.tables import word_tables

DEFAULT_LIST_KINDS: Mapping[str, str] = {
    "UN_CONSOLIDATED": "SANCTION",
    "OFAC_SDN": "SANCTION",
    "EU_FSF": "SANCTION",
    "UK_FCDO": "SANCTION",
    "MX_SAT_69B": "FISCAL",
}
TRUNCATE_AT = 60
_SUBSTITUTIONS = (
    ("v", "b"), ("b", "v"), ("z", "s"), ("s", "z"), ("ll", "y"), ("y", "ll"),
    ("j", "g"), ("ph", "f"), ("ce", "se"), ("ci", "si"), ("qu", "k"), ("h", ""),
    ("ou", "u"), ("ee", "i"), ("mm", "m"), ("i", "y"),
)  # fmt: skip
_ACCENTS = {"a": "á", "e": "é", "i": "í", "o": "ó", "u": "ú", "n": "ñ"}
_LETTERS = string.ascii_lowercase
_MIN_WORDS_FOR_INITIAL = 3
_MIN_WORDS_FOR_PARTICLE = 2
_MIN_TYPO_LENGTH = 4
_TYPO_KINDS = 4


# ---- perturbations -------------------------------------------------------------------


class Draw:
    """A seeded, deterministic choice source for the corpus generator.

    Each draw hashes the seed and a counter (SHA-256), so the generated corpus, and
    the hash the tests pin, never depend on the interpreter's ``random`` module.
    Not for anything secret.
    """

    def __init__(self, seed: int) -> None:
        self._seed: int = seed
        self._counter: int = 0

    def below(self, bound: int) -> int:
        """Draw an integer.

        Returns:
            A value in ``[0, bound)``.

        """
        self._counter += 1
        digest = hashlib.sha256(f"{self._seed}:{self._counter}".encode()).digest()
        return int.from_bytes(digest[:8]) % bound

    def randrange(self, start: int, stop: int) -> int:
        """Draw an integer in a range.

        Returns:
            A value in ``[start, stop)``.

        """
        return start + self.below(stop - start)

    def choice[T](self, options: Sequence[T]) -> T:
        """Pick one option.

        Returns:
            The option drawn.

        """
        return options[self.below(len(options))]

    def sample[T](self, options: Sequence[T], count: int) -> list[T]:
        """Pick ``count`` distinct options, in draw order.

        Returns:
            The options drawn.

        """
        pool = list(options)
        return [pool.pop(self.below(len(pool))) for _ in range(count)]


def _ascii_fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _words(text: str) -> list[str]:
    return text.replace(",", " ").split()


def _drop_maternal(text: str, _rng: Draw) -> str:
    words = _words(text)
    return " ".join(words[:-1]) if len(words) >= _MIN_WORDS_FOR_INITIAL else text


def _swap_order(text: str, _rng: Draw) -> str:
    words = _words(text)
    if len(words) < _MIN_WORDS_FOR_PARTICLE:
        return text
    return f"{' '.join(words[1:])}, {words[0]}"


def _lose_accents(text: str, _rng: Draw) -> str:
    folded = _ascii_fold(text)
    if folded != text:
        return folded
    # Already plain: add an accent instead, as a customer form might.
    for index, char in enumerate(text):
        accented = _ACCENTS.get(char.lower())
        if accented:
            return (
                text[:index]
                + (accented.upper() if char.isupper() else accented)
                + text[index + 1 :]
            )
    return text


def _substitute(text: str, rng: Draw) -> str:
    lower = text.lower()
    options = [(a, b) for a, b in _SUBSTITUTIONS if a in lower]
    if not options:
        return text
    old, new = rng.choice(options)
    start = lower.index(old)
    return text[:start] + new + text[start + len(old) :]


def _nickname(text: str, rng: Draw) -> str:
    words = _words(text)
    for index, word in enumerate(words):
        variants = sorted(nickname_variants(_ascii_fold(word).lower()))
        if variants:
            words[index] = rng.choice(variants)
            return " ".join(words)
    return text


def _initials(text: str, _rng: Draw) -> str:
    words = _words(text)
    if len(words) < _MIN_WORDS_FOR_INITIAL:
        return text
    return " ".join([f"{words[0][0]}.", *words[1:]])


def _particles(text: str, rng: Draw) -> str:
    words = _words(text)
    particles = word_tables().particles
    kept = [w for w in words if _ascii_fold(w).lower() not in particles]
    if len(kept) != len(words):
        return " ".join(kept)
    if len(words) < _MIN_WORDS_FOR_PARTICLE:
        return text
    position = rng.randrange(1, len(words))
    return " ".join([
        *words[:position],
        rng.choice(("de", "del", "de la")),
        *words[position:],
    ])


def _typo(text: str, rng: Draw) -> str:
    words = _words(text)
    candidates = [i for i, w in enumerate(words) if len(w) >= _MIN_TYPO_LENGTH]
    if not candidates:
        return text
    index = rng.choice(candidates)
    word = words[index]
    at = rng.randrange(1, len(word) - 1)
    match rng.below(_TYPO_KINDS):
        case 0:
            changed = word[:at] + word[at + 1 :]
        case 1:
            changed = word[:at] + rng.choice(_LETTERS) + word[at:]
        case 2:
            changed = word[:at] + word[at + 1] + word[at] + word[at + 2 :]
        case _:
            changed = word[:at] + rng.choice(_LETTERS) + word[at + 1 :]
    words[index] = changed
    return " ".join(words)


def _truncate(text: str, _rng: Draw) -> str:
    return text[:TRUNCATE_AT] if len(text) > TRUNCATE_AT else text


PERTURBATIONS: Mapping[str, Callable[[str, Draw], str]] = {
    "DROP_MATERNAL": _drop_maternal,
    "ORDER_SWAP": _swap_order,
    "ACCENTS": _lose_accents,
    "SUBSTITUTION": _substitute,
    "NICKNAME": _nickname,
    "INITIAL": _initials,
    "PARTICLE": _particles,
    "TYPO": _typo,
    "TRUNCATE": _truncate,
}
_PERSON_ONLY = frozenset({"DROP_MATERNAL", "NICKNAME", "INITIAL"})


@dataclass(frozen=True, slots=True)
class CorpusPair:
    """A query built from a listed name and the entry it must find."""

    query: NameQuery
    list_code: str
    entry_key: str
    perturbations: tuple[str, ...]


def perturb(text: str, kinds: Sequence[str], rng: Draw) -> str:
    """Apply perturbations in order.

    Returns:
        The perturbed name (possibly unchanged when no kind applies).

    """
    for kind in kinds:
        text = PERTURBATIONS[kind](text, rng)
    return text


def _query_type(entity_type: EntityType) -> EntityType:
    return (
        EntityType.PERSON
        if entity_type is EntityType.PERSON
        else EntityType.ORGANIZATION
    )


def _source_names(entry: ListEntry) -> list[str]:
    names = [entry.primary_name, *entry.aliases]
    return [
        n.full
        for n in names
        if n.quality is not NameQuality.WEAK and _ascii_fold(n.full).isascii()
    ]


def generate_pairs(
    entries: Iterable[ListEntry], *, per_entry: int, seed: int
) -> list[CorpusPair]:
    """Generate perturbed queries for every screenable entry.

    Each entry gets ``per_entry`` distinct queries (fewer when its names cannot
    yield that many), cycling over its Latin-script, non-weak names with one or two
    perturbations chosen by a ``Draw(seed)``.

    Returns:
        The pairs, in entry order.

    """
    rng = Draw(seed)
    pairs: list[CorpusPair] = []
    for entry in entries:
        if not entry.screenable:
            continue
        names = _source_names(entry)
        if not names:
            continue
        person = entry.entity_type is EntityType.PERSON
        kinds = [k for k in PERTURBATIONS if person or k not in _PERSON_ONLY]
        seen: set[str] = set()
        for attempt in range(per_entry * 4):
            if len(seen) >= per_entry:
                break
            chosen = tuple(rng.sample(kinds, 1 + attempt % 2))
            text = perturb(names[attempt % len(names)], chosen, rng)
            if text in seen:
                continue
            seen.add(text)
            pairs.append(
                CorpusPair(
                    query=NameQuery(_query_type(entry.entity_type), (text,)),
                    list_code=entry.list_code,
                    entry_key=entry.entry_key,
                    perturbations=chosen,
                )
            )
    return pairs


# ---- evaluation ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KindReport:
    list_kind: str
    pairs: int
    recall: float
    retrieval_recall: float
    alerts: int
    correct_alerts: int
    precision_at_alert: float

    def to_json(self) -> dict[str, JsonValue]:
        """Serialize for ``evaluation.json``.

        Returns:
            The report with ratios rounded to 4 places.

        """
        return {
            "list_kind": self.list_kind,
            "pairs": self.pairs,
            "recall": round(self.recall, 4),
            "retrieval_recall": round(self.retrieval_recall, 4),
            "alerts": self.alerts,
            "correct_alerts": self.correct_alerts,
            "precision_at_alert": round(self.precision_at_alert, 4),
        }


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    kinds: tuple[KindReport, ...]
    hard_negatives: int
    negative_alerts: int
    top_k: int
    thresholds: BandThresholds

    @property
    def alerts_per_1000_negatives(self) -> float:
        """Rate hard-negative queries that raised any alert.

        Returns:
            Alerts per thousand hard negatives.

        """
        return (
            1000 * self.negative_alerts / self.hard_negatives
            if self.hard_negatives
            else 0.0
        )

    def kind(self, list_kind: str) -> KindReport:
        """Find one list kind's figures.

        Returns:
            The report for ``list_kind``.

        Raises:
            KeyError: the corpus had no pair of that kind.

        """
        for report in self.kinds:
            if report.list_kind == list_kind:
                return report
        raise KeyError(list_kind)

    def to_json(self) -> dict[str, JsonValue]:
        """Serialize for ``evaluation.json`` (``xolotl screening eval --write``).

        Returns:
            Every figure, thresholds as strings.

        """
        return {
            "matcher_version": MATCHER_VERSION,
            "kinds": [k.to_json() for k in self.kinds],
            "hard_negatives": self.hard_negatives,
            "negative_alerts": self.negative_alerts,
            "alerts_per_1000_negatives": round(self.alerts_per_1000_negatives, 4),
            "top_k": self.top_k,
            "thresholds": {
                "alert": str(self.thresholds.alert),
                "review": str(self.thresholds.review),
            },
        }


@dataclass(frozen=True, slots=True)
class Corpus:
    entries: tuple[ListEntry, ...]
    pairs: tuple[CorpusPair, ...]
    hard_negatives: tuple[NameQuery, ...]

    def entry_for(self, pair: CorpusPair) -> ListEntry:
        """Find the entry a true pair must retrieve.

        Returns:
            The listed entry.

        Raises:
            KeyError: the pair names an entry outside the corpus.

        """
        for entry in self.entries:
            if (entry.list_code, entry.entry_key) == (pair.list_code, pair.entry_key):
                return entry
        raise KeyError((pair.list_code, pair.entry_key))


@dataclass(slots=True)
class _Tally:
    pairs: int = 0
    found: int = 0
    retrieved: int = 0
    alerts: int = 0
    correct: int = 0


type _Key = tuple[str, str]


def _same_person(entries: Sequence[ListEntry]) -> dict[_Key, frozenset[_Key]]:
    by_name: dict[str, set[_Key]] = {}
    for entry in entries:
        key = (entry.list_code, entry.entry_key)
        for name, _quality in entry_names(entry):
            by_name.setdefault(f"{entry.entity_type}:{name.folded}", set()).add(key)
    groups: dict[_Key, set[_Key]] = {}
    for keys in by_name.values():
        for key in keys:
            groups.setdefault(key, set()).update(keys)
    return {key: frozenset(group) for key, group in groups.items()}


def evaluate(
    corpus: Corpus,
    *,
    thresholds: BandThresholds,
    config: ScoringConfig | None = None,
    top_k: int = 50,
    list_kinds: Mapping[str, str] = DEFAULT_LIST_KINDS,
) -> EvaluationReport:
    """Run the corpus through retrieval, scoring and banding.

    Returns:
        Recall, retrieval recall and precision per list kind, and the hard-negative
        alert rate.

    """
    config = config or ScoringConfig()
    idf = IdfTable.build(corpus.entries)
    index = MemoryIndex.build(
        (
            IndexedEntry(e, UUID(int=n), e.list_code)
            for n, e in enumerate(corpus.entries)
        ),
        idf=idf,
    )
    shards = frozenset(e.list_code for e in corpus.entries)
    same = _same_person(corpus.entries)
    tallies: dict[str, _Tally] = {}

    def bands_for(query: NameQuery) -> list[tuple[_Key, Band]]:
        found: list[tuple[_Key, Band]] = []
        for candidate in index.candidates(query, shards=shards, top_k=top_k):
            entry = candidate.indexed.entry
            result = score(query, entry, idf=idf, config=config)
            found.append((
                (entry.list_code, entry.entry_key),
                band_for(result, thresholds),
            ))
        return found

    for pair in corpus.pairs:
        target = (pair.list_code, pair.entry_key)
        right = same.get(target, frozenset({target}))
        tally = tallies.setdefault(list_kinds.get(pair.list_code, "OTHER"), _Tally())
        tally.pairs += 1
        hits = bands_for(pair.query)
        tally.retrieved += any(key == target for key, _band in hits)
        tally.found += any(
            key in right and band in {Band.ALERT, Band.LOW_CONFIDENCE}
            for key, band in hits
        )
        for key, band in hits:
            if band is Band.ALERT:
                kind = tallies.setdefault(list_kinds.get(key[0], "OTHER"), _Tally())
                kind.alerts += 1
                kind.correct += key in right
    negative_alerts = 0
    for query in corpus.hard_negatives:
        hits = bands_for(query)
        alerting = [key for key, band in hits if band is Band.ALERT]
        negative_alerts += bool(alerting)
        for key in alerting:
            tallies.setdefault(list_kinds.get(key[0], "OTHER"), _Tally()).alerts += 1
    return EvaluationReport(
        kinds=tuple(
            KindReport(
                list_kind=kind,
                pairs=t.pairs,
                recall=t.found / t.pairs if t.pairs else 0.0,
                retrieval_recall=t.retrieved / t.pairs if t.pairs else 0.0,
                alerts=t.alerts,
                correct_alerts=t.correct,
                precision_at_alert=t.correct / t.alerts if t.alerts else 1.0,
            )
            for kind, t in sorted(tallies.items())
        ),
        hard_negatives=len(corpus.hard_negatives),
        negative_alerts=negative_alerts,
        top_k=top_k,
        thresholds=thresholds,
    )


# ---- loading and floors --------------------------------------------------------------

# The trimmed real fixtures the corpus is seeded from, relative to the test root.
FIXTURE_FILES: Mapping[str, str] = {
    "UN_CONSOLIDATED": "fixtures/un/consolidated.xml",
    "OFAC_SDN": "fixtures/ofac/sdn_enhanced.xml",
    "EU_FSF": "fixtures/eu/full_sanctions_list_1_1.xml",
    "UK_FCDO": "fixtures/uk/UK-Sanctions-List.xml",
    "MX_SAT_69B": "fixtures/sat69b/Listado_Completo_69-B.csv",
}


@dataclass(frozen=True, slots=True)
class Floors:
    """``corpus/floors.yaml``: generation settings, thresholds and the floors."""

    seed: int
    per_entry: int
    min_pairs: int
    min_hard_negatives: int
    top_k: int
    thresholds: BandThresholds
    floors: Mapping[str, Mapping[str, float]]
    max_alerts_per_1000_negatives: float


@dataclass(frozen=True, slots=True)
class FloorFailure:
    list_kind: str
    metric: str
    value: float
    floor: float


def _mapping(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        msg = f"{where} must be a mapping"
        raise TypeError(msg)
    return cast("Mapping[str, object]", value)


def _number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        msg = f"{where} must be a number"
        raise TypeError(msg)
    return float(value)


def load_floors(path: Path) -> Floors:
    """Read ``floors.yaml``.

    Returns:
        The floors. A malformed file raises ``TypeError`` or ``KeyError``.

    """
    data = _mapping(
        cast("object", yaml.safe_load(path.read_text(encoding="utf-8"))), "floors"
    )
    thresholds = _mapping(data["thresholds"], "thresholds")
    floors = {
        kind: {
            metric: _number(value, f"{kind}.{metric}")
            for metric, value in _mapping(metrics, kind).items()
        }
        for kind, metrics in _mapping(data["floors"], "floors").items()
    }
    return Floors(
        seed=int(_number(data["seed"], "seed")),
        per_entry=int(_number(data["per_entry"], "per_entry")),
        min_pairs=int(_number(data["min_pairs"], "min_pairs")),
        min_hard_negatives=int(
            _number(data["min_hard_negatives"], "min_hard_negatives")
        ),
        top_k=int(_number(data["top_k"], "top_k")),
        thresholds=BandThresholds(
            alert=Decimal(str(thresholds["alert"])),
            review=Decimal(str(thresholds["review"])),
        ),
        floors=floors,
        max_alerts_per_1000_negatives=_number(
            data["max_alerts_per_1000_negatives"], "max_alerts_per_1000_negatives"
        ),
    )


def check_floors(report: EvaluationReport, floors: Floors) -> list[FloorFailure]:
    """Compare a report with the floors, as CI and ``xolotl screening eval`` do.

    Returns:
        Every metric below its floor (or above the hard-negative ceiling); empty
        when the report passes.

    """
    failures: list[FloorFailure] = []
    for kind, metrics in sorted(floors.floors.items()):
        try:
            figures = report.kind(kind)
        except KeyError:
            failures.extend(
                FloorFailure(kind, m, 0.0, f) for m, f in sorted(metrics.items())
            )
            continue
        values = {
            "recall": figures.recall,
            "retrieval_recall": figures.retrieval_recall,
            "precision_at_alert": figures.precision_at_alert,
        }
        failures.extend(
            FloorFailure(kind, metric, values[metric], floor)
            for metric, floor in sorted(metrics.items())
            if values[metric] < floor
        )
    rate = report.alerts_per_1000_negatives
    if rate > floors.max_alerts_per_1000_negatives:
        failures.append(
            FloorFailure(
                "ALL",
                "alerts_per_1000_negatives",
                rate,
                floors.max_alerts_per_1000_negatives,
            )
        )
    return failures


def load_corpus(root: Path, floors: Floors) -> Corpus:
    """Load the corpus: fixture entries, generated true pairs and hard negatives.

    ``root`` is the screening test directory holding ``fixtures/`` and ``corpus/``.

    Returns:
        The corpus, deterministic for the same files and floors.

    """
    entries: list[ListEntry] = []
    for code, relative in FIXTURE_FILES.items():
        with (root / relative).open("rb") as data:
            entries.extend(parse(code, data).entries)
    lines = (
        (root / "corpus" / "hard_negatives.csv")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    rows = csv.DictReader(line for line in lines if not line.startswith("#"))
    negatives = tuple(
        NameQuery(EntityType(r["entity_type"]), (r["name"],)) for r in rows
    )
    pairs = generate_pairs(entries, per_entry=floors.per_entry, seed=floors.seed)
    return Corpus(tuple(entries), tuple(pairs), negatives)
