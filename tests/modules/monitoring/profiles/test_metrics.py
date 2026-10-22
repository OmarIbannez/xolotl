"""Effectiveness metrics computed on read (spec §13.2; FR-TM-012, OPS-502..504).

The hits come from B7's stage: the structuring golden of an ``N3`` customer and
two peso deposits of an ``N1`` customer, so one ``CASH_STRUCTURING`` and one
``TIER_N1_MONTHLY_OPS`` hit are stored. Their ``created_at`` is moved to a known
distance from ``first_occurred_at`` so the time to hit is exact.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, update

from tests.helpers import system_ctx
from tests.modules.monitoring.captures.support import events
from tests.modules.monitoring.captures.test_periods import (
    NEW_YEAR,
    ROR,
    close_one,
    golden,
)
from tests.modules.monitoring.rules.hook_support import (
    CASH,
    TIER_N1,
    Stage,
    add_relationship,
    build_stage,
    deposit,
    feed,
    hits,
    local,
)
from tests.modules.monitoring.test_routes_hits import EXTRA_SCOPES, declared_scopes
from tests.modules.party.route_support import machine_headers, staff_headers
from xolotl.modules.monitoring.captures.models import ReportableEvent
from xolotl.modules.monitoring.decide.enums import DecisionMode, DecisionOutcome
from xolotl.modules.monitoring.decide.models import TxnDecision
from xolotl.modules.monitoring.errors import PeriodInvalidError
from xolotl.modules.monitoring.process.hooks import reset_processing_hooks
from xolotl.modules.monitoring.profiles.metrics import metrics_for_period
from xolotl.modules.monitoring.rules.definitions import Severity
from xolotl.modules.monitoring.rules.models import RuleHit
from xolotl.modules.monitoring.schemas import CaptureMetrics, MetricsResponse
from xolotl.modules.monitoring.txn.schema import Rail
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.ids import uuid7

METRICS = "/v1/monitoring/metrics"
N3 = "cust-n3"
N1 = "cust-n1"
CASH_DELAY = timedelta(seconds=90)
LIMIT_DELAY = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class Scenario:
    stage: Stage
    n1: UUID
    structuring: RuleHit
    limit: RuleHit

    @property
    def tenant_id(self) -> UUID:
        return self.stage.tenant_id


def _delay(admin_engine: Engine, hit: RuleHit, delay: timedelta) -> None:
    with admin_engine.begin() as conn:
        _ = conn.execute(
            update(RuleHit)
            .where(RuleHit.id == hit.id)
            .values(created_at=RuleHit.first_occurred_at + delay)
        )


@pytest.fixture
def scenario(
    tenant: CreatedTenant, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Scenario]:
    stage = build_stage(tenant, admin_engine, monkeypatch)
    _ = add_relationship(tenant, N3, "N3")
    n1 = add_relationship(tenant, N1, "N1")
    _ = feed(
        stage,
        [
            deposit(f"dep-{n}", local(day), amount, customer_ref=N3)
            for n, day, amount in (
                (1, 7, "3600.00"),
                (2, 8, "3700.00"),
                (3, 9, "3800.00"),
            )
        ],
    )
    _ = feed(
        stage,
        [
            deposit(f"mxn-{n}", local(day), "3300.00", customer_ref=N1, currency="MXN")
            for n, day in ((1, 10), (2, 11))
        ],
    )
    [structuring] = hits(stage, CASH)
    [limit] = hits(stage, TIER_N1)
    _delay(admin_engine, structuring, CASH_DELAY)
    _delay(admin_engine, limit, LIMIT_DELAY)
    yield Scenario(stage, n1, structuring, limit)
    reset_processing_hooks()


def _metrics(
    tenant_id: UUID, period: str = "2026-12", rule_version_id: UUID | None = None
) -> MetricsResponse:
    with tenant_session(system_ctx(tenant_id)) as s:
        return metrics_for_period(s, period=period, rule_version_id=rule_version_id)


def _severities(**counts: int) -> dict[Severity, int]:
    return {severity: counts.get(severity.value, 0) for severity in Severity}


def test_metrics_count_hits_parties_and_severities_for_the_month(scenario: Scenario):
    cash, limit = scenario.structuring, scenario.limit
    expected = _severities()
    for hit in (cash, limit):
        expected[Severity(hit.severity)] += 1

    shown = _metrics(scenario.tenant_id)

    assert shown.period == "2026-12"
    assert (shown.hits, shown.parties_hit) == (2, 2)
    assert shown.hits_by_severity == expected
    by_code = {rule.rule_code: rule for rule in shown.rules}
    assert list(by_code) == [CASH, TIER_N1]
    assert by_code[CASH].rule_version_id == scenario.stage.versions[CASH]
    assert (by_code[CASH].version, by_code[CASH].typology) == (1, ["STRUCTURING"])
    assert (by_code[CASH].hits, by_code[CASH].parties_hit) == (1, 1)
    assert by_code[CASH].hits_by_severity == _severities(**{cash.severity: 1})
    assert by_code[TIER_N1].typology == ["LIMIT"]
    november = _metrics(scenario.tenant_id, "2026-11")
    assert (november.hits, november.parties_hit, november.rules) == (0, 0, [])
    assert november.hits_by_severity == _severities()


def test_time_to_hit_percentiles_come_from_created_at_minus_first_occurred_at(
    scenario: Scenario,
):
    by_code = {rule.rule_code: rule for rule in _metrics(scenario.tenant_id).rules}

    assert by_code[CASH].median_seconds_to_hit == Decimal(90)
    assert by_code[CASH].p95_seconds_to_hit == Decimal(90)
    assert by_code[TIER_N1].median_seconds_to_hit == Decimal(30)


def test_typology_coverage_counts_active_templates_per_tag(scenario: Scenario):
    shown = _metrics(scenario.tenant_id)

    assert shown.typology_coverage == {"LIMIT": 1, "SCREENING": 1, "STRUCTURING": 1}
    # Before the rules were in force (30 June 2026) nothing covers anything.
    assert _metrics(scenario.tenant_id, "2026-05").typology_coverage == {}


@pytest.mark.usefixtures("forget_hooks")
def test_captures_per_report_code_against_the_reconciled_count(
    tenant: CreatedTenant, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    stage = golden(tenant, admin_engine, ROR)
    first = min(events(stage, ROR), key=lambda event: event.occurred_at)
    with admin_engine.begin() as conn:
        _ = conn.execute(delete(ReportableEvent).where(ReportableEvent.id == first.id))
    before = _metrics(stage.tenant_id)
    monkeypatch.setattr(clock, "now", lambda: NEW_YEAR)
    _ = close_one(stage, ROR, "2026-Q4")

    after = _metrics(stage.tenant_id)

    report_code = first.report_code
    (open_quarter,) = [c for c in before.captures if c.report_code == report_code]
    assert open_quarter == CaptureMetrics(
        report_code=report_code,
        period_key="2026-Q4",
        captured=1,
        reconciled_count=None,
        discrepancies=0,
    )
    (closed,) = [c for c in after.captures if c.report_code == report_code]
    # The close wrote the missing event back (late) and counted it.
    assert closed == CaptureMetrics(
        report_code=report_code,
        period_key="2026-Q4",
        captured=2,
        reconciled_count=2,
        discrepancies=1,
    )
    assert [c.report_code for c in _metrics(stage.tenant_id, "2027-01").captures] == []


@pytest.fixture
def forget_hooks() -> Iterator[None]:
    yield
    reset_processing_hooks()


def _decision(
    relationship_id: UUID, at: datetime, latency_ms: int, mode: DecisionMode
) -> TxnDecision:
    failed = mode is not DecisionMode.EVALUATED
    return TxnDecision(
        tenant_id=uuid7(),
        decided_at=at,
        id=uuid7(),
        external_id=f"d-{uuid7()}",
        rail=Rail.SPEI,
        relationship_id=relationship_id,
        decision=DecisionOutcome.REVIEW if failed else DecisionOutcome.ALLOW,
        mode=mode,
        reasons=[{"code": "DECIDE_UNAVAILABLE"}] if failed else [],
        ruleset_hash=b"",
        latency_ms=latency_ms,
        db_ms=latency_ms // 2,
        actor="api_client:test",
    )


def test_decision_percentiles_and_fail_counts(scenario: Scenario):
    december = datetime(2026, 12, 10, 18, tzinfo=UTC)
    modes = {3: DecisionMode.FAIL_OPEN, 7: DecisionMode.FAIL_OPEN}
    modes |= {9: DecisionMode.FAIL_CLOSED}
    rows = [
        _decision(
            scenario.n1,
            december + timedelta(minutes=ms),
            ms,
            modes.get(ms, DecisionMode.EVALUATED),
        )
        for ms in range(1, 101)
    ]
    # Mexico City's November: 1 December 05:00 UTC is still 30 November there.
    rows.append(
        _decision(
            scenario.n1,
            datetime(2026, 12, 1, 5, tzinfo=UTC),
            5000,
            DecisionMode.FAIL_CLOSED,
        )
    )
    with tenant_session(system_ctx(scenario.tenant_id)) as s:
        for row in rows:
            row.tenant_id = scenario.tenant_id
            s.add(row)

    shown = _metrics(scenario.tenant_id).decisions

    assert (shown.count, shown.p50_ms, shown.p95_ms, shown.p99_ms) == (100, 50, 95, 99)
    assert (shown.fail_open, shown.fail_closed) == (2, 1)
    november = _metrics(scenario.tenant_id, "2026-11").decisions
    assert (november.count, november.p50_ms, november.fail_closed) == (1, 5000, 1)
    empty = _metrics(scenario.tenant_id, "2027-02").decisions
    assert (empty.count, empty.p50_ms, empty.p99_ms, empty.fail_open) == (
        0,
        None,
        None,
        0,
    )


def test_a_rule_version_filter_narrows_the_hits_and_nothing_else(scenario: Scenario):
    everything = _metrics(scenario.tenant_id)

    narrowed = _metrics(
        scenario.tenant_id, rule_version_id=scenario.limit.rule_version_id
    )

    assert (narrowed.hits, narrowed.parties_hit) == (1, 1)
    assert [rule.rule_code for rule in narrowed.rules] == [TIER_N1]
    assert narrowed.hits_by_severity == _severities(**{scenario.limit.severity: 1})
    assert narrowed.typology_coverage == everything.typology_coverage
    assert narrowed.captures == everything.captures
    assert narrowed.decisions == everything.decisions
    assert _metrics(scenario.tenant_id, rule_version_id=uuid7()).hits == 0


@pytest.mark.parametrize("period", ["2026-13", "2026-1", "202612", "dec", "2026-12-01"])
def test_a_bad_period_is_422_period_invalid(
    client: TestClient, scenario: Scenario, period: str
):
    analyst = staff_headers(
        client, scenario.stage.tenant, email="a@acme.mx", roles=(Role.ANALYST,)
    )

    response = client.get(METRICS, params={"period": period}, headers=analyst)

    assert response.status_code == 422
    assert response.json()["code"] == "PERIOD_INVALID"
    with pytest.raises(PeriodInvalidError):
        _ = _metrics(scenario.tenant_id, period)
    # The month is required: a read is always bounded by one.
    unbounded = client.get(METRICS, headers=analyst)
    assert unbounded.status_code == 422


def test_staff_read_the_metrics_of_a_month(client: TestClient, scenario: Scenario):
    auditor = staff_headers(
        client, scenario.stage.tenant, email="audit@acme.mx", roles=(Role.AUDITOR_RO,)
    )

    response = client.get(
        METRICS,
        params={
            "period": "2026-12",
            "rule_version_id": str(scenario.structuring.rule_version_id),
        },
        headers=auditor,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["hits"] == 1
    assert body["rules"][0]["rule_code"] == CASH
    assert body["rules"][0]["median_seconds_to_hit"] == "90.000"


def test_no_api_key_reaches_the_metrics_whatever_its_scopes(
    app: FastAPI, client: TestClient, scenario: Scenario
):
    # Tipping-off (D20, PRV-502): the metrics name rules and count hits and
    # captures, so they are staff only, unlike spec §15's monitoring:read row.
    every_scope = machine_headers(
        scenario.stage.tenant, *sorted(declared_scopes(app) | set(EXTRA_SCOPES))
    )

    response = client.get(METRICS, params={"period": "2026-12"}, headers=every_scope)

    assert response.status_code == 403
    assert response.json()["code"] == "ROLE_FORBIDDEN"
    assert CASH not in response.text


def test_case_rates_are_null_until_sp06(scenario: Scenario):
    shown = _metrics(scenario.tenant_id)

    assert shown.alert_to_case_rate is None
    assert shown.case_to_report_rate is None
    dumped = shown.model_dump()
    assert dumped["alert_to_case_rate"] is None
    assert dumped["case_to_report_rate"] is None
