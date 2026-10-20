"""The shipped evaluation report and its endpoint (SP-03 spec §4.3, §12; F6).

``src/xolotl/modules/screening/data/evaluation.json`` is written by
``xolotl screening eval --write``. It must be for the matcher the build runs, meet
every floor and stay in its canonical form, so a regenerated report diffs cleanly.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.modules.party.route_support import machine_headers
from tests.modules.screening.route_support import SCREENING, staff_client
from xolotl.modules.screening import evaluation_report
from xolotl.modules.screening.evaluation_report import (
    SHIPPED_REPORT,
    EvaluationReportResponse,
    canonical_json,
    load_shipped_report,
)
from xolotl.modules.screening.matching import MATCHER_VERSION
from xolotl.modules.screening.matching.evaluation import load_floors
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.enums import Role

EVALUATION = f"{SCREENING}/evaluation"


def test_the_shipped_report_is_for_the_running_matcher():
    assert load_shipped_report().matcher_version == MATCHER_VERSION


def test_the_shipped_report_meets_every_floor():
    report = load_shipped_report()
    assert report.floors
    assert {f.list_kind for f in report.floors} >= {"SANCTION", "FISCAL"}
    assert [f for f in report.floors if not f.met] == []
    assert {k.list_kind for k in report.kinds} == {"SANCTION", "FISCAL"}
    assert sum(k.pairs for k in report.kinds) == report.corpus_pairs


def test_the_shipped_report_meets_the_floors_the_corpus_sets_now():
    floors = load_floors(Path(__file__).parent / "corpus" / "floors.yaml")
    rows = {(f.list_kind, f.metric): f for f in load_shipped_report().floors}

    for kind, metrics in floors.floors.items():
        for metric, floor in metrics.items():
            assert rows[kind, metric].value >= Decimal(str(floor)), (kind, metric)
    negatives = rows["ALL", "alerts_per_1000_negatives"]
    assert negatives.value <= Decimal(str(floors.max_alerts_per_1000_negatives))


def test_the_report_is_valid_canonical_json():
    stored = SHIPPED_REPORT.read_text(encoding="utf-8")
    report = EvaluationReportResponse.model_validate(json.loads(stored))

    assert canonical_json(report) == stored
    assert list(json.loads(stored)) == sorted(json.loads(stored))
    assert isinstance(json.loads(stored)["kinds"][0]["recall_review"], str)


def test_staff_read_the_evaluation_and_keys_cannot(
    client: TestClient, tenant: CreatedTenant
):
    auditor = staff_client(client, tenant, Role.AUDITOR_RO)

    read = client.get(EVALUATION, headers=auditor)
    assert read.status_code == 200, read.text
    assert read.json() == load_shipped_report().model_dump(mode="json")

    machine = machine_headers(tenant, "screening:read", "screening:write")
    refused = client.get(EVALUATION, headers=machine)
    assert (refused.status_code, refused.json()["code"]) == (403, "ROLE_FORBIDDEN")


def test_a_build_without_a_report_answers_not_found(
    client: TestClient,
    tenant: CreatedTenant,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setattr(
        evaluation_report, "SHIPPED_REPORT", tmp_path / "evaluation.json"
    )
    load_shipped_report.cache_clear()
    try:
        missing = client.get(
            EVALUATION, headers=staff_client(client, tenant, Role.ANALYST)
        )
    finally:
        load_shipped_report.cache_clear()

    assert (missing.status_code, missing.json()["code"]) == (404, "NOT_FOUND")
