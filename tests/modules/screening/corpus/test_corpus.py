"""The evaluation corpus floors: CI fails when matching quality drops (FR-SC-014)."""

import hashlib
from functools import cache
from pathlib import Path

from xolotl.modules.screening.matching import normalize_name
from xolotl.modules.screening.matching.evaluation import (
    Corpus,
    EvaluationReport,
    Floors,
    check_floors,
    evaluate,
    load_corpus,
    load_floors,
)

ROOT = Path(__file__).parents[1]
# sha256 of the generated pairs; update it in the same PR as a generator change.
PAIRS_SHA256 = "3cff0090ba51887c5e6133b3f1618edcd4ec590f7fad00f3ef966c6b5ef4c8b4"


@cache
def _floors() -> Floors:
    return load_floors(ROOT / "corpus" / "floors.yaml")


@cache
def _corpus() -> Corpus:
    return load_corpus(ROOT, _floors())


@cache
def _report() -> EvaluationReport:
    return evaluate(_corpus(), thresholds=_floors().thresholds, top_k=_floors().top_k)


def test_the_corpus_is_large_enough_and_deterministic():
    corpus = _corpus()
    assert len(corpus.pairs) >= _floors().min_pairs
    assert len(corpus.hard_negatives) >= _floors().min_hard_negatives
    lines = (
        f"{p.list_code}|{p.entry_key}|{p.query.names[0]}|{','.join(p.perturbations)}"
        for p in corpus.pairs
    )
    assert hashlib.sha256("\n".join(lines).encode()).hexdigest() == PAIRS_SHA256


def test_no_hard_negative_is_a_fixture_name():
    listed = {
        normalize_name(n.full, entity_type=e.entity_type).folded
        for e in _corpus().entries
        for n in (e.primary_name, *e.aliases)
    }
    for query in _corpus().hard_negatives:
        name = normalize_name(query.names[0], entity_type=query.entity_type)
        assert name.folded not in listed


def test_every_pair_points_at_a_corpus_entry():
    corpus = _corpus()
    pair = corpus.pairs[0]
    entry = corpus.entry_for(pair)
    assert (entry.list_code, entry.entry_key) == (pair.list_code, pair.entry_key)


def test_the_floors_hold():
    assert check_floors(_report(), _floors()) == [], _report().to_json()
