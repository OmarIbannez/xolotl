"""The dry-run replay of a range against the stored hits (spec §13.1, FR-TM-018).

Tenant A's week of 7 to 13 December 2026 holds 4b's structuring golden, processed
by the worker's jobs, so one ``CASH_STRUCTURING`` hit is stored. The replay runs
the rules in force on the range's last day through B8's in-memory engine and
compares ``(rule_version_id, dedup_key)`` with the stored ``rule_hit`` rows.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import Engine, delete, update
from sqlalchemy.exc import DBAPIError

from tests.helpers import system_ctx
from tests.modules.monitoring.rules.hook_support import (
    ACTIVE_CODES,
    CASH,
    EFFECTIVE_FROM,
    Stage,
    hits,
)
from tests.modules.monitoring.simulation.replay_support import (
    WEEK_END,
    WEEK_START,
    clone_rule,
    process_week,
    row_counts,
)
from xolotl.modules.monitoring.errors import SimulationRangeTooLongError
from xolotl.modules.monitoring.rules.models import RuleHit, RuleHitTransaction
from xolotl.modules.monitoring.simulation.replay import (
    ReplayReport,
    RuleReplay,
    replay_range,
)
from xolotl.platform.config import get_settings
from xolotl.platform.db import tenant_session
from xolotl.platform.errors import ValidationFailedError


@pytest.fixture
def week(stage: Stage) -> Stage:
    process_week(stage)
    return stage


def _replay(
    stage: Stage, start: date = WEEK_START, end: date = WEEK_END
) -> ReplayReport:
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        return replay_range(s, range_start=start, range_end=end)


def _rule(report: ReplayReport, code: str) -> RuleReplay:
    (found,) = [rule for rule in report.rules if rule.rule_code == code]
    return found


def test_replay_reproduces_the_stored_hits_of_a_week(week: Stage):
    report = _replay(week)

    assert report.identical
    assert (report.range_start, report.range_end) == (WEEK_START, WEEK_END)
    assert [rule.rule_code for rule in report.rules] == sorted(ACTIVE_CODES)
    assert all(rule.compared for rule in report.rules)
    cash = _rule(report, CASH)
    assert cash.rule_version_id == week.versions[CASH]
    assert cash.version == 1
    assert (cash.simulated, cash.stored, cash.missing, cash.extra) == (1, 1, 0, 0)


def test_replay_twice_gives_the_same_hits_and_writes_nothing(
    week: Stage, admin_engine: Engine
):
    before = row_counts(admin_engine)

    first = _replay(week)
    second = _replay(week)

    assert row_counts(admin_engine) == before
    assert first == second
    assert first.identical


def test_a_hit_deleted_from_the_store_shows_as_extra(week: Stage, admin_engine: Engine):
    [stored] = hits(week, CASH)
    with admin_engine.begin() as conn:
        _ = conn.execute(
            delete(RuleHitTransaction).where(RuleHitTransaction.hit_id == stored.id)
        )
        _ = conn.execute(delete(RuleHit).where(RuleHit.id == stored.id))

    report = _replay(week)

    assert not report.identical
    cash = _rule(report, CASH)
    assert (cash.simulated, cash.stored, cash.missing, cash.extra) == (1, 0, 0, 1)


def test_a_stored_hit_the_replay_does_not_reproduce_shows_as_missing(
    week: Stage, admin_engine: Engine
):
    [stored] = hits(week, CASH)
    with admin_engine.begin() as conn:
        _ = conn.execute(
            update(RuleHit)
            .where(RuleHit.id == stored.id)
            .values(dedup_key=f"{stored.dedup_key}:forged")
        )

    cash = _rule(_replay(week), CASH)

    assert (cash.simulated, cash.stored, cash.missing, cash.extra) == (1, 1, 1, 1)


def test_a_rule_activated_after_the_week_shows_its_hits_as_extra(week: Stage):
    # In force from the 14th: processing never ran it over the week's deposits,
    # and the replay runs the rules in force on the range's last day.
    late = clone_rule(week, "CASH_STRUCTURING_LATE", effective_from=date(2026, 12, 14))

    report = _replay(week, end=date(2026, 12, 14))

    assert not report.identical
    added = _rule(report, "CASH_STRUCTURING_LATE")
    assert added.rule_version_id == late
    assert (added.simulated, added.stored, added.missing, added.extra) == (1, 0, 0, 1)
    cash = _rule(report, CASH)
    assert (cash.missing, cash.extra) == (0, 0)


def test_a_product_rule_replays_only_that_products_transactions(week: Stage):
    # The deposits are EMONEY's: a CRYPTO rule never sees them, an EMONEY one does.
    _ = clone_rule(week, "CASH_CRYPTO", effective_from=EFFECTIVE_FROM, product="CRYPTO")
    _ = clone_rule(week, "CASH_EMONEY", effective_from=EFFECTIVE_FROM, product="EMONEY")

    report = _replay(week)

    crypto = _rule(report, "CASH_CRYPTO")
    assert (crypto.simulated, crypto.stored) == (0, 0)
    emoney = _rule(report, "CASH_EMONEY")
    assert (emoney.simulated, emoney.stored, emoney.extra) == (1, 0, 1)
    assert _rule(report, CASH).simulated == 1


def test_a_rule_keyed_across_parties_is_listed_and_not_compared(week: Stage):
    # The replay runs one party at a time (plan 4b Deviation 12), so a key that
    # several parties share would be counted per party: it is not compared.
    _ = clone_rule(
        week,
        "CASH_BY_COUNTERPARTY",
        effective_from=EFFECTIVE_FROM,
        dedup_key="COUNTERPARTY",
    )

    report = _replay(week)

    skipped = _rule(report, "CASH_BY_COUNTERPARTY")
    assert not skipped.compared
    assert (skipped.simulated, skipped.missing, skipped.extra) == (0, 0, 0)
    assert report.identical


def test_a_range_must_be_ordered_and_bounded(week: Stage):
    with pytest.raises(ValidationFailedError):
        _ = _replay(week, start=WEEK_END, end=WEEK_START)
    most = get_settings().monitoring_simulation_max_days
    with pytest.raises(SimulationRangeTooLongError):
        _ = _replay(week, start=WEEK_END - timedelta(days=most), end=WEEK_END)


def test_the_replay_runs_in_a_read_only_transaction(week: Stage):
    with tenant_session(system_ctx(week.tenant_id)) as s:
        _ = replay_range(s, range_start=WEEK_START, range_end=WEEK_END)
        with pytest.raises(DBAPIError, match="read-only transaction"):
            _ = s.execute(update(RuleHit).values(fired_count=RuleHit.fired_count))
        s.rollback()
