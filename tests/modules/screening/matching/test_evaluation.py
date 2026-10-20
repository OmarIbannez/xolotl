"""The perturbation generator, the report and the floors (SP-03 spec §4.3)."""

from dataclasses import replace
from decimal import Decimal

import pytest

from tests.modules.screening.matching.support import entry
from xolotl.modules.screening.lists import EntityType
from xolotl.modules.screening.matching import BandThresholds, NameQuery
from xolotl.modules.screening.matching.evaluation import (
    PERTURBATIONS,
    Corpus,
    CorpusPair,
    Draw,
    EvaluationReport,
    Floors,
    KindReport,
    check_floors,
    evaluate,
    generate_pairs,
    load_floors,
    perturb,
)

THRESHOLDS = BandThresholds(Decimal("0.70"), Decimal("0.55"))


def test_draw_is_deterministic_and_bounded():
    first, second = Draw(7), Draw(7)
    values = [first.below(10) for _ in range(50)]
    assert values == [second.below(10) for _ in range(50)]
    assert all(0 <= v < 10 for v in values)
    assert 5 <= Draw(1).randrange(5, 8) < 8
    assert sorted(Draw(3).sample("abcd", 4)) == list("abcd")
    assert Draw(3).choice(["only"]) == "only"


@pytest.mark.parametrize(
    ("kind", "text", "expected"),
    [
        ("DROP_MATERNAL", "Joaquín Guzmán Loera", "Joaquín Guzmán"),
        ("DROP_MATERNAL", "Ana Ruiz", "Ana Ruiz"),
        ("ORDER_SWAP", "Ana Ruiz Soto", "Ruiz Soto, Ana"),
        ("ORDER_SWAP", "Madonna", "Madonna"),
        ("ACCENTS", "José Pérez", "Jose Perez"),
        ("ACCENTS", "Ana", "Ána"),
        ("ACCENTS", "123", "123"),
        ("NICKNAME", "Ana Ruiz", "Ana Ruiz"),
        ("INITIAL", "Ana Sofía Ruiz", "A. Sofía Ruiz"),
        ("INITIAL", "Ana Ruiz", "Ana Ruiz"),
        ("PARTICLE", "Juan de la Cruz", "Juan Cruz"),
        ("PARTICLE", "Madonna", "Madonna"),
        ("TYPO", "Al Bo", "Al Bo"),
        ("SUBSTITUTION", "Pt", "Pt"),
        ("TRUNCATE", "x" * 70, "x" * 60),
        ("TRUNCATE", "short", "short"),
    ],
)
def test_perturbations(kind, text, expected):
    assert PERTURBATIONS[kind](text, Draw(1)) == expected


def test_random_perturbations_change_the_name():
    assert perturb("Francisco Ruiz", ["NICKNAME"], Draw(1)).split()[0] in {
        "paco",
        "pancho",
        "frank",
        "francis",
        "kiko",
    }
    assert perturb("Ana Ruiz", ["PARTICLE"], Draw(1)) != "Ana Ruiz"
    # A typo may redraw the same letter; across seeds it changes the name.
    typos = {perturb("Hernandez", ["TYPO"], Draw(seed)) for seed in range(8)}
    assert len(typos - {"Hernandez"}) >= 6
    assert perturb("Vazquez", ["SUBSTITUTION"], Draw(1)) != "Vazquez"


def test_generate_pairs_is_deterministic_and_skips_unusable_entries():
    entries = [
        entry("1", "Joaquín Archivaldo Guzmán Loera"),
        entry("2", "Acme Sol SA de CV", entity_type=EntityType.ORGANIZATION),
        entry("3", "Gone", screenable=False),
        entry("4", "صدام حسين"),
    ]
    pairs = generate_pairs(entries, per_entry=20, seed=5)
    assert pairs == generate_pairs(entries, per_entry=20, seed=5)
    assert {p.entry_key for p in pairs} == {"1", "2"}
    assert len({p.query.names[0] for p in pairs if p.entry_key == "1"}) == 20
    organization = [p for p in pairs if p.entry_key == "2"]
    assert all(p.query.entity_type is EntityType.ORGANIZATION for p in organization)
    assert all("NICKNAME" not in p.perturbations for p in organization)


def _small_corpus() -> Corpus:
    guzman = entry("1", "Joaquín Archivaldo Guzmán Loera")
    also = entry("9", "Joaquín Archivaldo Guzmán Loera", list_code="OFAC_SDN")
    acme = entry(
        "2",
        "Acme Sol SA de CV",
        entity_type=EntityType.ORGANIZATION,
        list_code="MX_SAT_69B",
    )
    pairs = (
        CorpusPair(
            NameQuery(EntityType.PERSON, ("Joaquin Guzman Loera",)),
            "UN_CONSOLIDATED",
            "1",
            (),
        ),
        CorpusPair(
            NameQuery(EntityType.ORGANIZATION, ("Acme Sol",)), "MX_SAT_69B", "2", ()
        ),
        CorpusPair(
            NameQuery(EntityType.PERSON, ("Zed Unrelated",)), "UN_CONSOLIDATED", "1", ()
        ),
    )
    negatives = (
        NameQuery(EntityType.PERSON, ("María López",)),
        NameQuery(EntityType.PERSON, ("Joaquín Guzmán Loera",)),
    )
    return Corpus((guzman, also, acme), pairs, negatives)


def test_evaluate_counts_recall_retrieval_precision_and_negatives():
    report = evaluate(_small_corpus(), thresholds=THRESHOLDS)
    sanction = report.kind("SANCTION")
    assert (sanction.pairs, sanction.recall, sanction.retrieval_recall) == (2, 0.5, 0.5)
    # The same person listed by OFAC is a right answer, not a false positive; the
    # negative that names him is counted against precision and the negative rate.
    assert sanction.alerts == 4
    assert sanction.correct_alerts == 2
    fiscal = report.kind("FISCAL")
    assert (fiscal.recall, fiscal.alerts, fiscal.precision_at_alert) == (1.0, 0, 1.0)
    assert report.negative_alerts == 1
    assert report.alerts_per_1000_negatives == pytest.approx(500.0)
    data = report.to_json()
    assert data["matcher_version"] == "2.1.0"
    assert data["thresholds"] == {"alert": "0.70", "review": "0.55"}
    with pytest.raises(KeyError):
        _ = report.kind("PEP")


def test_corpus_entry_lookup():
    corpus = _small_corpus()
    assert corpus.entry_for(corpus.pairs[1]).entry_key == "2"
    with pytest.raises(KeyError):
        _ = corpus.entry_for(CorpusPair(corpus.pairs[0].query, "X", "Y", ()))


BASE_FLOORS = Floors(
    seed=1,
    per_entry=1,
    min_pairs=1,
    min_hard_negatives=1,
    top_k=5,
    thresholds=THRESHOLDS,
    floors={
        "SANCTION": {"recall": 0.98, "precision_at_alert": 0.9},
        "PEP": {"recall": 0.9},
    },
    max_alerts_per_1000_negatives=2.0,
)


def _floors(**changes: object) -> Floors:
    return replace(BASE_FLOORS, **changes)


def test_check_floors_names_each_failure():
    report = EvaluationReport(
        kinds=(KindReport("SANCTION", 10, 0.9, 1.0, 10, 10, 1.0),),
        hard_negatives=100,
        negative_alerts=1,
        top_k=5,
        thresholds=THRESHOLDS,
    )
    failures = check_floors(report, _floors())
    assert [(f.list_kind, f.metric) for f in failures] == [
        ("PEP", "recall"),
        ("SANCTION", "recall"),
        ("ALL", "alerts_per_1000_negatives"),
    ]
    assert (
        check_floors(report, _floors(floors={}, max_alerts_per_1000_negatives=10)) == []
    )
    empty = EvaluationReport((), 0, 0, 5, THRESHOLDS)
    assert not empty.alerts_per_1000_negatives


GOOD_FLOORS = """
seed: 1
per_entry: 2
min_pairs: 3
min_hard_negatives: 4
top_k: 5
thresholds: {alert: '0.70', review: '0.55'}
floors: {SANCTION: {recall: 0.98}}
max_alerts_per_1000_negatives: 2
"""


def test_load_floors(tmp_path):
    path = tmp_path / "floors.yaml"
    _ = path.write_text(GOOD_FLOORS)
    floors = load_floors(path)
    assert floors.thresholds == THRESHOLDS
    assert floors.floors == {"SANCTION": {"recall": 0.98}}
    _ = path.write_text("[]")
    with pytest.raises(TypeError, match="mapping"):
        _ = load_floors(path)
    _ = path.write_text(GOOD_FLOORS.replace("seed: 1", "seed: true"))
    with pytest.raises(TypeError, match="seed must be a number"):
        _ = load_floors(path)
