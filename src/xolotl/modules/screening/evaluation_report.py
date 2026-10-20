"""The matcher's evaluation report, as shipped with the build (SP-03 spec §4.3, §12).

``xolotl screening eval`` runs the screening library's corpus through ``evaluate``
and compares it with ``corpus/floors.yaml``; with ``--write`` it stores the result in
``data/evaluation.json``, which ``GET /v1/screening/evaluation`` serves (FR-SC-014).
A test fails when that file is for another ``MATCHER_VERSION``, so a matcher change
cannot ship with a stale report. The corpus lives under ``tests/``: this is a
build-time tool, refused cleanly where the corpus is absent (the container image).

Ratios are ``Decimal`` to four places, so the JSON is stable and diffs cleanly. The
hard-negative alert rate is one figure for the whole corpus, as the library measures
it, and its ceiling is reported as the floor row of list kind ``ALL``.
"""

import json
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from xolotl.modules.screening.matching import MATCHER_VERSION
from xolotl.modules.screening.matching.evaluation import (
    EvaluationReport,
    KindReport,
    check_floors,
    evaluate,
    load_corpus,
    load_floors,
)
from xolotl.platform.errors import NotFoundError

SHIPPED_REPORT = Path(__file__).parent / "data" / "evaluation.json"
ALL_KINDS = "ALL"
NEGATIVE_ALERTS_METRIC = "alerts_per_1000_negatives"
_PLACES = Decimal("0.0001")


class ListKindMetrics(BaseModel):
    list_kind: str
    pairs: int
    recall_review: Decimal
    # None when nothing of this kind alerted, so precision is undefined.
    precision_alert: Decimal | None
    retrieval_recall: Decimal


class Floor(BaseModel):
    metric: str
    list_kind: str
    floor: Decimal
    value: Decimal
    met: bool


class EvaluationReportResponse(BaseModel):
    matcher_version: str
    generated_at: datetime
    corpus_pairs: int
    hard_negatives: int
    alerts_per_1000_negatives: Decimal
    kinds: list[ListKindMetrics]
    floors: list[Floor]


def _ratio(value: float) -> Decimal:
    return Decimal(value).quantize(_PLACES)


def _kind_metrics(kind: KindReport) -> ListKindMetrics:
    return ListKindMetrics(
        list_kind=kind.list_kind,
        pairs=kind.pairs,
        recall_review=_ratio(kind.recall),
        precision_alert=_ratio(kind.precision_at_alert) if kind.alerts else None,
        retrieval_recall=_ratio(kind.retrieval_recall),
    )


def _metric(report: EvaluationReport, list_kind: str, metric: str) -> float:
    try:
        kind = report.kind(list_kind)
    except KeyError:
        return 0.0
    values = {
        "recall": kind.recall,
        "retrieval_recall": kind.retrieval_recall,
        "precision_at_alert": kind.precision_at_alert,
    }
    return values[metric]


def evaluate_corpus(
    corpus_dir: Path, *, generated_at: datetime
) -> EvaluationReportResponse:
    """Evaluate the corpus at ``corpus_dir`` against its floors.

    ``corpus_dir`` holds ``floors.yaml`` and ``hard_negatives.csv``; the fixtures the
    true pairs are generated from are in ``../fixtures``, as the library lays them out.
    Whether a floor is met is the library's ``check_floors`` verdict.

    Returns:
        The report, with one floor row per floor and the hard-negative ceiling.

    Raises:
        NotFoundError: there is no corpus at ``corpus_dir``.

    """
    floors_file = corpus_dir / "floors.yaml"
    if not floors_file.is_file():
        msg = f"no evaluation corpus at {corpus_dir}"
        raise NotFoundError(msg)
    floors = load_floors(floors_file)
    corpus = load_corpus(corpus_dir.parent, floors)
    report = evaluate(corpus, thresholds=floors.thresholds, top_k=floors.top_k)
    failed = {(f.list_kind, f.metric) for f in check_floors(report, floors)}
    rows = [
        Floor(
            metric=metric,
            list_kind=kind,
            floor=_ratio(floor),
            value=_ratio(_metric(report, kind, metric)),
            met=(kind, metric) not in failed,
        )
        for kind, metrics in sorted(floors.floors.items())
        for metric, floor in sorted(metrics.items())
    ]
    rows.append(
        Floor(
            metric=NEGATIVE_ALERTS_METRIC,
            list_kind=ALL_KINDS,
            floor=_ratio(floors.max_alerts_per_1000_negatives),
            value=_ratio(report.alerts_per_1000_negatives),
            met=(ALL_KINDS, NEGATIVE_ALERTS_METRIC) not in failed,
        )
    )
    return EvaluationReportResponse(
        matcher_version=MATCHER_VERSION,
        generated_at=generated_at,
        corpus_pairs=len(corpus.pairs),
        hard_negatives=report.hard_negatives,
        alerts_per_1000_negatives=_ratio(report.alerts_per_1000_negatives),
        kinds=[_kind_metrics(kind) for kind in report.kinds],
        floors=rows,
    )


def canonical_json(report: EvaluationReportResponse) -> str:
    """Serialize a report the one way it is committed.

    Returns:
        JSON with sorted keys, two-space indents, Decimals as strings and a final
        newline.

    """
    data = report.model_dump(mode="json")
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


@lru_cache(maxsize=1)
def load_shipped_report() -> EvaluationReportResponse:
    """Read the report shipped with the build.

    Returns:
        The committed report.

    Raises:
        NotFoundError: the build ships no report.

    """
    if not SHIPPED_REPORT.is_file():
        msg = "no evaluation report shipped with this build"
        raise NotFoundError(msg)
    return EvaluationReportResponse.model_validate_json(
        SHIPPED_REPORT.read_text(encoding="utf-8")
    )
