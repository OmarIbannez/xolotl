"""Effectiveness metrics of a month, computed on read (spec §13.2; FR-TM-012).

``metrics_for_period`` reads a Mexico City month (``YYYY-MM``) straight from the
tables; there is no metrics table, and SP-11 decides on a materialised monthly row
if its measurement asks for one. Every read is bounded by the month:

- ``rule_hit`` by ``created_at`` in the month, optionally one rule version: one
  query for the counts per version and severity, one for the parties and the
  time to hit per version with the month's total (``ROLLUP``). With a version
  ``ix_rule_hit_rule_period`` serves the range; without one the month's rows of
  the tenant are scanned once per query (``ix_rule_hit_queue`` leads with the
  severity), which is the cost to watch on a large tenant.
- ``capture_period`` rows whose dates overlap the month (a few per capture
  code), then the live ``reportable_event`` rows of those report codes and
  period keys through ``ix_reportable_event_period``.
- ``txn_decision`` by ``decided_at`` in the month, which prunes to the month's
  partition and scans it (OPS-502; ``ix_txn_decision_relationship`` does not
  help a tenant-wide read). Fail-mode rows count in the latencies too.
- The rules in force on the month's last day (or today, if earlier) for the
  typology coverage: ``rule_assignment`` and their versions.

A rule version filter narrows the hit metrics only: coverage, captures and
decisions belong to no single rule version. The case rates stay null until
SP-06.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Integer,
    Numeric,
    cast,
    distinct,
    extract,
    func,
    select,
    type_coerce,
)
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.captures.enums import ReportableEventStatus
from xolotl.modules.monitoring.captures.models import CapturePeriod, ReportableEvent
from xolotl.modules.monitoring.captures.record import discrepancy_count
from xolotl.modules.monitoring.decide.enums import DecisionMode
from xolotl.modules.monitoring.decide.models import TxnDecision
from xolotl.modules.monitoring.enrich.policy import policy_date
from xolotl.modules.monitoring.errors import PeriodInvalidError
from xolotl.modules.monitoring.profiles.job import parse_month
from xolotl.modules.monitoring.rules.assignments import assignments_in_force
from xolotl.modules.monitoring.rules.definitions import RuleDefinition, Severity
from xolotl.modules.monitoring.rules.models import (
    MonitoringRule,
    MonitoringRuleVersion,
    RuleHit,
)
from xolotl.modules.monitoring.schemas import (
    CaptureMetrics,
    DecisionMetrics,
    MetricsResponse,
    RuleMetrics,
)
from xolotl.modules.monitoring.simulation.engine import range_bounds
from xolotl.platform import clock

_LIVE = (ReportableEventStatus.CAPTURED, ReportableEventStatus.INCLUDED)


def _month(period: str) -> tuple[date, date]:
    try:
        _ = parse_month(period)
    except ValueError as exc:
        raise PeriodInvalidError from exc
    year, number = (int(part) for part in period.split("-"))
    first = date(year, number, 1)
    following = (first + timedelta(days=31)).replace(day=1)
    return first, following - timedelta(days=1)


def _no_severities() -> dict[Severity, int]:
    return dict.fromkeys(Severity, 0)


def _seconds(fraction: float) -> ColumnElement[Decimal]:
    delay = RuleHit.created_at - RuleHit.first_occurred_at
    interval = func.percentile_cont(fraction).within_group(delay)
    return cast(extract("epoch", interval), Numeric(18, 3))


class _Hits:
    """The month's hits per rule version, and overall."""

    def __init__(
        self, s: Session, *, first: date, last: date, rule_version_id: UUID | None
    ) -> None:
        start, end = range_bounds(first, last)
        where = [RuleHit.created_at >= start, RuleHit.created_at < end]
        if rule_version_id is not None:
            where.append(RuleHit.rule_version_id == rule_version_id)
        self.by_severity: dict[UUID, dict[Severity, int]] = defaultdict(_no_severities)
        counted = s.execute(
            select(RuleHit.rule_version_id, RuleHit.severity, func.count())
            .where(*where)
            .group_by(RuleHit.rule_version_id, RuleHit.severity)
        )
        for version_id, severity, count in counted:
            self.by_severity[version_id][Severity(severity)] = count
        self.parties: dict[UUID, int] = {}
        self.median: dict[UUID, Decimal] = {}
        self.p95: dict[UUID, Decimal] = {}
        self.parties_hit: int = 0
        version = RuleHit.rule_version_id
        rolled = s.execute(
            select(
                version,
                type_coerce(func.grouping(version), Integer),
                func.count(distinct(RuleHit.party_id)),
                _seconds(0.5),
                _seconds(0.95),
            )
            .where(*where)
            .group_by(func.rollup(version))
        )
        for version_id, total, parties, median, p95 in rolled:
            if total:
                self.parties_hit = parties
                continue
            self.parties[version_id] = parties
            self.median[version_id] = median
            self.p95[version_id] = p95

    def overall(self) -> dict[Severity, int]:
        totals = _no_severities()
        for counts in self.by_severity.values():
            for severity, count in counts.items():
                totals[severity] += count
        return totals

    def rules(self, s: Session) -> list[RuleMetrics]:
        if not self.parties:
            return []
        rows = s.execute(
            select(
                MonitoringRuleVersion.id,
                MonitoringRule.code,
                MonitoringRuleVersion.version,
                MonitoringRuleVersion.definition,
            )
            .join(
                MonitoringRule,
                (MonitoringRule.tenant_id == MonitoringRuleVersion.tenant_id)
                & (MonitoringRule.id == MonitoringRuleVersion.rule_id),
            )
            .where(MonitoringRuleVersion.id.in_(self.parties))
            .order_by(MonitoringRule.code, MonitoringRuleVersion.version)
        )
        return [
            RuleMetrics(
                rule_version_id=version_id,
                rule_code=code,
                version=number,
                typology=RuleDefinition.model_validate(definition).typology,
                hits=sum(self.by_severity[version_id].values()),
                parties_hit=self.parties[version_id],
                hits_by_severity=self.by_severity[version_id],
                median_seconds_to_hit=self.median[version_id],
                p95_seconds_to_hit=self.p95[version_id],
            )
            for version_id, code, number, definition in rows
        ]


def _coverage(s: Session, *, last: date) -> dict[str, int]:
    on = min(last, policy_date(clock.now()))
    codes = {
        assignment.rule_version_id: code
        for assignment, code in assignments_in_force(s, on=on)
    }
    if not codes:
        return {}
    definitions = s.execute(
        select(MonitoringRuleVersion.id, MonitoringRuleVersion.definition).where(
            MonitoringRuleVersion.id.in_(codes)
        )
    )
    tagged: dict[str, set[str]] = defaultdict(set)
    for version_id, definition in definitions:
        for tag in RuleDefinition.model_validate(definition).typology:
            tagged[tag].add(codes[version_id])
    return {tag: len(tagged[tag]) for tag in sorted(tagged)}


def _captures(s: Session, *, first: date, last: date) -> list[CaptureMetrics]:
    periods = s.scalars(
        select(CapturePeriod).where(
            CapturePeriod.period_start <= last, CapturePeriod.period_end >= first
        )
    ).all()
    if not periods:
        return []
    grouped: dict[tuple[str, str], list[CapturePeriod]] = defaultdict(list)
    for period in periods:
        grouped[period.report_code, period.period_key].append(period)
    counted = s.execute(
        select(ReportableEvent.report_code, ReportableEvent.period_key, func.count())
        .where(
            ReportableEvent.report_code.in_({code for code, _ in grouped}),
            ReportableEvent.period_key.in_({key for _, key in grouped}),
            ReportableEvent.status.in_(_LIVE),
        )
        .group_by(ReportableEvent.report_code, ReportableEvent.period_key)
    )
    captured = {(code, key): count for code, key, count in counted}
    shown: list[CaptureMetrics] = []
    for (code, key), rows in sorted(grouped.items()):
        counts = [row.reconciled_count for row in rows]
        reconciled = [count for count in counts if count is not None]
        shown.append(
            CaptureMetrics(
                report_code=code,
                period_key=key,
                captured=captured.get((code, key), 0),
                reconciled_count=(
                    sum(reconciled) if len(reconciled) == len(counts) else None
                ),
                discrepancies=sum(discrepancy_count(row) for row in rows),
            )
        )
    return shown


def _latency(fraction: float) -> ColumnElement[int]:
    return type_coerce(
        func.percentile_disc(fraction).within_group(TxnDecision.latency_ms), Integer
    )


def _decisions(s: Session, *, first: date, last: date) -> DecisionMetrics:
    start, end = range_bounds(first, last)
    count, p50, p95, p99, fail_open, fail_closed = s.execute(
        select(
            func.count(),
            _latency(0.5),
            _latency(0.95),
            _latency(0.99),
            func.count().filter(TxnDecision.mode == DecisionMode.FAIL_OPEN),
            func.count().filter(TxnDecision.mode == DecisionMode.FAIL_CLOSED),
        ).where(TxnDecision.decided_at >= start, TxnDecision.decided_at < end)
    ).one()
    return DecisionMetrics(
        count=count,
        p50_ms=p50,
        p95_ms=p95,
        p99_ms=p99,
        fail_open=fail_open,
        fail_closed=fail_closed,
    )


def metrics_for_period(
    s: Session, *, period: str, rule_version_id: UUID | None
) -> MetricsResponse:
    """Compute the effectiveness metrics of a Mexico City month.

    A period that is not a ``YYYY-MM`` month is ``PERIOD_INVALID``.

    Returns:
        The month's hits overall and per rule version, the typology coverage,
        the captures against their reconciliation and the decision latencies.

    """
    first, last = _month(period)
    found = _Hits(s, first=first, last=last, rule_version_id=rule_version_id)
    by_severity = found.overall()
    return MetricsResponse(
        period=period,
        hits=sum(by_severity.values()),
        parties_hit=found.parties_hit,
        hits_by_severity=by_severity,
        typology_coverage=_coverage(s, last=last),
        rules=found.rules(s),
        captures=_captures(s, first=first, last=last),
        decisions=_decisions(s, first=first, last=last),
    )
