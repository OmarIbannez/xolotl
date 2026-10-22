"""The simulation job: one row, no side effects, resumable, final on failure (§13.1).

The stage has ``CASH_STRUCTURING`` active from 30 June 2026 (B7). Structuring
customers are tier N3, so no other rule fires. A new draft of the rule (its
definition unchanged) is simulated over the first half of December 2026 under a
clock frozen on 14 December.
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any, NoReturn
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, inspect, select, table
from sqlalchemy.orm import Session

from tests.helpers import staff_session, system_ctx
from tests.modules.monitoring.rules.hook_support import (
    CASH,
    Stage,
    add_relationship,
    deposit,
    events_of,
    feed,
    hits,
    local,
)
from tests.modules.monitoring.rules.lifecycle_support import (
    AUTHOR_ROLES,
    add_staff,
    draft,
    freeze,
)
from xolotl.models import metadata
from xolotl.modules.monitoring.events import SIMULATION_COMPLETED
from xolotl.modules.monitoring.rules.assignments import active_rules
from xolotl.modules.monitoring.rules.enums import RuleVersionStatus, SimulationStatus
from xolotl.modules.monitoring.rules.models import (
    MonitoringRuleVersion,
    RuleSimulation,
)
from xolotl.modules.monitoring.rules.versions import VersionInput, update_version
from xolotl.modules.monitoring.simulation import job as job_module
from xolotl.modules.monitoring.simulation.engine import (
    ReplayPage,
    replay_pages,
    replay_rule_set,
)
from xolotl.modules.monitoring.simulation.job import (
    SIMULATE_KIND,
    handle_simulate,
    request_simulation,
)
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import JobStatus, Role
from xolotl.platform.errors import XolotlError
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import Job
from xolotl.platform.jobs.runner import JobRunner

TODAY = datetime(2026, 12, 14, 18, tzinfo=UTC)
FIRST = date(2026, 12, 1)
LAST = date(2026, 12, 13)


@pytest.fixture
def frozen(stage: Stage, monkeypatch: pytest.MonkeyPatch) -> Stage:
    freeze(monkeypatch, TODAY)
    return stage


def _near_three(ref: str, start_day: int = 7) -> list[dict[str, object]]:
    return [
        deposit(f"{ref}-{n}", local(start_day + n), amount, customer_ref=ref)
        for n, amount in enumerate(("3600.00", "3700.00", "3800.00"))
    ]


def _draft(stage: Stage) -> MonitoringRuleVersion:
    return draft(stage.tenant, CASH)


def _request(
    stage: Stage,
    version_id: UUID,
    *,
    start: date = FIRST,
    end: date = LAST,
    user: UUID | None = None,
    roles: tuple[Role, ...] = AUTHOR_ROLES,
) -> UUID:
    with staff_session(stage.tenant_id, user or stage.tenant.admin_user_id, roles) as s:
        simulation = request_simulation(
            s,
            version_id,
            range_start=start,
            range_end=end,
            queue=stage.services.queue,
        )
        return simulation.id


def _work(stage: Stage) -> int:
    runner = JobRunner(
        stage.services.queue,
        get_sessionmaker(),
        worker_id="w-simulate",
        kinds=[SIMULATE_KIND],
    )
    ran = 0
    while step := runner.run_once():
        ran += step
    return ran


def _simulation(stage: Stage, simulation_id: UUID) -> RuleSimulation:
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        return s.get_one(RuleSimulation, (stage.tenant_id, simulation_id))


def _version(stage: Stage, version_id: UUID) -> MonitoringRuleVersion:
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        return s.get_one(MonitoringRuleVersion, (stage.tenant_id, version_id))


def _counts(admin_engine: Engine) -> dict[str, int]:
    with admin_engine.connect() as conn:
        present = set(inspect(conn).get_table_names())
        return {
            name: conn.execute(
                select(func.count()).select_from(table(name))
            ).scalar_one()
            for name in metadata().tables
            if name in present
        }


def test_a_simulation_writes_only_its_own_row(frozen: Stage, admin_engine: Engine):
    stage = frozen
    _ = add_relationship(stage.tenant, "cust-a", "N3")
    _ = feed(stage, _near_three("cust-a"))
    version = _draft(stage)
    before = _counts(admin_engine)

    simulation_id = _request(stage, version.id)
    assert _work(stage) == 1

    after = _counts(admin_engine)
    changed = {
        name: after[name] - before[name]
        for name in after
        if after[name] != before[name]
    }
    # The row, the request's audit row, its job and the completion event.
    assert changed == {"rule_simulation": 1, "audit_event": 1, "job": 1, "outbox": 1}
    simulation = _simulation(stage, simulation_id)
    assert simulation.status is SimulationStatus.COMPLETED
    assert simulation.started_at == TODAY
    assert simulation.finished_at == TODAY
    assert simulation.definition_hash == version.definition_hash
    assert (simulation.transactions_read, simulation.hit_count) == (3, 1)
    simulated = _version(stage, version.id)
    assert simulated.status is RuleVersionStatus.SIMULATED
    assert simulated.simulation_id == simulation_id
    [event] = events_of(stage.tenant_id, SIMULATION_COMPLETED)
    assert event.key == str(version.id)
    assert event.data == {
        "simulation_id": str(simulation_id),
        "rule_version_id": str(version.id),
        "status": "COMPLETED",
    }


def test_simulation_reproduces_the_stored_hits_of_the_week(frozen: Stage):
    stage = frozen
    for ref in ("cust-a", "cust-b"):
        _ = add_relationship(stage.tenant, ref, "N3")
    _ = feed(stage, [*_near_three("cust-a"), *_near_three("cust-b", 10)])
    stored = {(h.dedup_key, h.window_start, h.window_end) for h in hits(stage, CASH)}
    assert len(stored) == 2

    version = _draft(stage)
    simulation_id = _request(stage, version.id, start=date(2026, 12, 7), end=LAST)
    _ = _work(stage)
    sample = _simulation(stage, simulation_id).sample or []
    simulated = {
        (
            str(entry["dedup_key"]),
            datetime.fromisoformat(str(entry["window_start"])),
            datetime.fromisoformat(str(entry["window_end"])),
        )
        for entry in sample
        if isinstance(entry, dict)
    }
    assert simulated == stored

    # Plan 4c's dry run compares (version, dedup key) with the stored rows.
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        ruleset = active_rules(s, on=date(2026, 12, 9), product="EMONEY")
        replayed = {
            (h.rule_version_id, h.dedup_key)
            for h in replay_rule_set(
                s, ruleset, range_start=date(2026, 12, 7), range_end=LAST, page=2
            )
        }
    assert replayed == {(h.rule_version_id, h.dedup_key) for h in hits(stage, CASH)}


def test_the_summary_counts_weeks_customers_and_overlap(
    frozen: Stage, monkeypatch: pytest.MonkeyPatch
):
    stage = frozen
    relationship = add_relationship(stage.tenant, "cust-a", "N3")
    _ = feed(stage, _near_three("cust-a"))
    [stored] = hits(stage, CASH)
    version = _draft(stage)

    simulation_id = _request(stage, version.id)
    _ = _work(stage)
    simulation = _simulation(stage, simulation_id)
    assert simulation.hits_per_week == [{"week": "2026-W50", "hits": 1, "customers": 1}]
    assert simulation.customers_hit == 1
    # The party was never screened: no later true match.
    assert simulation.overlap == {
        "existing_hits": 1,
        "same_customers": 1,
        "later_true_match": 0,
    }
    [entry] = simulation.sample or []
    assert entry == {
        "rule_version_id": str(version.id),
        "party_id": str(stored.party_id),
        "relationship_id": str(relationship),
        "trigger_transaction_id": str(stored.first_transaction_id),
        "dedup_key": stored.dedup_key,
        "window_start": stored.window_start.isoformat(),
        "window_end": stored.window_end.isoformat(),
        "severity": "MEDIUM",
        "outcome": "ALERT",
        "aggregates": stored.evidence["aggregates"],
    }
    assert simulation.cursor == str(stored.party_id)

    # A party matched after its simulated window counts as a later true match.
    later = stored.window_end + timedelta(days=1)

    def matched_later(_s: Session, _party: UUID) -> datetime:
        return later

    monkeypatch.setattr(job_module, "_true_match", [matched_later])
    again = _draft(stage)
    rerun = _simulation(stage, _request(stage, again.id))
    assert rerun.status is SimulationStatus.QUEUED
    _ = _work(stage)
    assert (_simulation(stage, rerun.id).overlap or {})["later_true_match"] == 1


class WorkerKilledError(BaseException):
    """Stands for the worker dying between two pages."""


def test_a_crash_between_pages_resumes_at_the_next_party(
    frozen: Stage, monkeypatch: pytest.MonkeyPatch
):
    stage = frozen
    for ref in ("cust-a", "cust-b", "cust-c"):
        _ = add_relationship(stage.tenant, ref, "N3")
    _ = feed(
        stage,
        [
            *_near_three("cust-a"),
            *_near_three("cust-b"),
            deposit("cust-c-0", local(8), "100.00", customer_ref="cust-c"),
        ],
    )
    monkeypatch.setattr(get_settings(), "monitoring_agg_backfill_batch", 2)
    version = _draft(stage)
    simulation_id = _request(stage, version.id)
    job = Job(
        id=uuid4(),
        tenant_id=stage.tenant_id,
        kind=SIMULATE_KIND,
        key=f"simulate:{simulation_id}",
        payload={"simulation_id": str(simulation_id)},
        attempts=1,
        max_attempts=1,
        lock_token=uuid4(),
    )
    real = replay_pages

    def one_page_then_die(*args: Any, **kwargs: Any) -> Iterator[ReplayPage]:
        pages = real(*args, **kwargs)
        yield next(pages)
        raise WorkerKilledError

    with monkeypatch.context() as patched:
        patched.setattr(job_module, "replay_pages", one_page_then_die)
        with (
            pytest.raises(WorkerKilledError),
            tenant_session(system_ctx(stage.tenant_id)) as s,
        ):
            handle_simulate(s, job)

    halfway = _simulation(stage, simulation_id)
    first_party = hits(stage, CASH)[0].party_id
    assert halfway.status is SimulationStatus.RUNNING
    assert halfway.cursor == str(first_party)
    assert (halfway.transactions_read, halfway.hit_count) == (3, 1)

    with tenant_session(system_ctx(stage.tenant_id)) as s:
        handle_simulate(s, job)
    done = _simulation(stage, simulation_id)
    assert done.status is SimulationStatus.COMPLETED
    assert (done.transactions_read, done.hit_count, done.customers_hit) == (7, 2, 2)
    assert done.hits_per_week == [{"week": "2026-W50", "hits": 2, "customers": 2}]
    # A finished simulation is left alone when its job runs again.
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        handle_simulate(s, job)
    assert _simulation(stage, simulation_id).transactions_read == 7


def _code(call: Any) -> tuple[str, tuple[str, ...]]:
    with pytest.raises(XolotlError) as raised:
        call()
    return raised.value.code, tuple(getattr(raised.value, "blocked_reasons", ()))


def test_a_too_long_range_is_refused(frozen: Stage):
    stage = frozen
    version = _draft(stage)
    end = date(2026, 12, 14)
    longest = end - timedelta(days=189)

    assert _code(
        lambda: _request(stage, version.id, start=longest - timedelta(days=1), end=end)
    ) == ("SIMULATION_RANGE_TOO_LONG", ())
    assert _code(
        lambda: _request(stage, version.id, start=end, end=end + timedelta(days=1))
    ) == ("VALIDATION_FAILED", ())
    assert _code(
        lambda: _request(stage, version.id, start=end, end=end - timedelta(days=1))
    ) == ("VALIDATION_FAILED", ())
    analyst = add_staff(stage.tenant, Role.ANALYST)
    assert _code(
        lambda: _request(stage, version.id, user=analyst, roles=(Role.ANALYST,))
    ) == ("RULE_TRANSITION_NOT_ALLOWED", ("ROLE_REQUIRED",))
    assert _code(lambda: _request(stage, stage.versions[CASH])) == (
        "RULE_TRANSITION_NOT_ALLOWED",
        ("STATE",),
    )
    assert _code(lambda: _request(stage, uuid4())) == ("NOT_FOUND", ())
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        assert _code(
            lambda: request_simulation(
                s,
                version.id,
                range_start=end,
                range_end=end,
                queue=stage.services.queue,
            )
        ) == ("FORBIDDEN", ())

    # 190 days is the most: accepted, queued with its job.
    simulation_id = _request(stage, version.id, start=longest, end=end)
    assert _simulation(stage, simulation_id).status is SimulationStatus.QUEUED
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        row = s.scalars(select(JobRow).where(JobRow.kind == SIMULATE_KIND)).one()
    assert (row.key, row.payload, row.max_attempts) == (
        f"simulate:{simulation_id}",
        {"simulation_id": str(simulation_id)},
        1,
    )


def test_completion_marks_the_version_simulated_only_when_unchanged(
    frozen: Stage, monkeypatch: pytest.MonkeyPatch
):
    stage = frozen
    _ = add_relationship(stage.tenant, "cust-a", "N3")
    _ = feed(stage, _near_three("cust-a"))
    version = _draft(stage)
    simulation_id = _request(stage, version.id)
    real = replay_pages

    def edited_meanwhile(*args: Any, **kwargs: Any) -> Iterator[ReplayPage]:
        with staff_session(
            stage.tenant_id, stage.tenant.admin_user_id, AUTHOR_ROLES
        ) as s:
            _ = update_version(
                s, version.id, body=VersionInput(params={"min_count": 4})
            )
        yield from real(*args, **kwargs)

    monkeypatch.setattr(job_module, "replay_pages", edited_meanwhile)
    _ = _work(stage)

    assert _simulation(stage, simulation_id).status is SimulationStatus.COMPLETED
    edited = _version(stage, version.id)
    assert edited.status is RuleVersionStatus.DRAFT
    assert edited.simulation_id is None
    assert edited.definition_hash != version.definition_hash


def test_a_failure_is_final_and_visible(frozen: Stage, monkeypatch: pytest.MonkeyPatch):
    stage = frozen
    version = _draft(stage)
    simulation_id = _request(stage, version.id)

    def broken(*_args: Any, **_kwargs: Any) -> NoReturn:
        msg = "cust-a deposited 3600"
        raise RuntimeError(msg)

    monkeypatch.setattr(job_module, "replay_pages", broken)
    assert _work(stage) == 1

    failed = _simulation(stage, simulation_id)
    assert failed.status is SimulationStatus.FAILED
    assert failed.last_error == "RuntimeError"
    assert failed.finished_at == TODAY
    [event] = events_of(stage.tenant_id, SIMULATION_COMPLETED)
    assert event.data["status"] == "FAILED"
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        row = s.scalars(select(JobRow).where(JobRow.kind == SIMULATE_KIND)).one()
    assert row.status is JobStatus.DEAD
    assert row.attempts == row.max_attempts == 1
    assert _version(stage, version.id).status is RuleVersionStatus.DRAFT
