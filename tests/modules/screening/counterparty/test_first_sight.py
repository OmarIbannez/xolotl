"""First-sight screening of a new counterparty (SP-04 spec §11.1 and §11.2).

A resolution that creates a counterparty enqueues one
``screening.screen_counterparties`` job per UTC minute; the job screens the
never-screened rows a page at a time, and the row caches the subject's status, which
the enricher and the decide path read.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from tests.modules.screening.counterparty.conftest import (
    Screened,
    first_sight_jobs,
    job_session,
    resolve_one,
    row_of,
    runs_of,
)
from xolotl.modules.screening.counterparty import first_sight
from xolotl.modules.screening.counterparty.first_sight import (
    FIRST_SIGHT_KIND,
    set_first_sight_queue,
)
from xolotl.modules.screening.counterparty.models import CounterpartyNameIndex
from xolotl.modules.screening.counterparty.resolve import (
    dedup_hash_of,
    resolve,
    status_by_dedup_hash,
)
from xolotl.modules.screening.counterparty.runs import SKIPPED_RETRY, handle_first_sight
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyInput,
    CounterpartyKind,
    CounterpartyScreeningStatus,
)
from xolotl.modules.screening.engine.enums import RunMode, SubjectKind, TriggerType
from xolotl.modules.screening.engine.models import ScreeningMatch, ScreeningSubject
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_engine
from xolotl.platform.enums import JobStatus
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import Job

ALARCON = CounterpartyInput(
    kind=CounterpartyKind.PERSON, name="Rodrigo Alarcón Palomarez", country="MX"
)
NOBODY = CounterpartyInput(kind=CounterpartyKind.PERSON, name="Fernanda López Ruiz")
MINUTE = datetime(2026, 10, 20, 15, 4, 12, tzinfo=UTC)


@pytest.fixture
def at_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "now", lambda: MINUTE)


def test_a_new_counterparty_is_screened_by_the_first_sight_job(
    tenant: CreatedTenant, screened: Screened
):
    ref = resolve_one(tenant, ALARCON)

    (job,) = first_sight_jobs(tenant)
    assert job.payload == {}
    assert job.key is not None
    assert job.key.startswith("screen_counterparties:")
    assert (
        row_of(tenant, ref.id).screening_status
        is CounterpartyScreeningStatus.UNSCREENED
    )

    assert screened.runner.run_once() == 1

    row = row_of(tenant, ref.id)
    assert row.screening_status is CounterpartyScreeningStatus.PENDING_REVIEW
    assert row.screened_at is not None
    assert row.screening_subject_id is not None
    (run,) = runs_of(tenant)
    assert run.mode is RunMode.COUNTERPARTY
    assert run.trigger_type is TriggerType.JOB
    assert run.trigger_ref == str(job.id)
    assert run.subject_id == row.screening_subject_id
    assert [j.status for j in first_sight_jobs(tenant)] == [JobStatus.DONE]


def test_two_resolutions_in_the_same_minute_enqueue_one_job(
    tenant: CreatedTenant, screened: Screened, at_minute: None
):
    _ = resolve_one(tenant, ALARCON)
    _ = resolve_one(tenant, NOBODY)

    (job,) = first_sight_jobs(tenant)
    assert job.key == "screen_counterparties:2026-10-20T15:04"


def test_a_known_counterparty_enqueues_nothing(
    tenant: CreatedTenant, screened: Screened
):
    _ = resolve_one(tenant, NOBODY)
    assert screened.runner.run_once() == 1

    _ = resolve_one(tenant, NOBODY)

    assert [j.status for j in first_sight_jobs(tenant)] == [JobStatus.DONE]


def test_a_full_page_enqueues_a_follow_up(
    tenant: CreatedTenant, screened: Screened, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(get_settings(), "monitoring_counterparty_screen_batch", 2)
    refs = [
        resolve_one(tenant, CounterpartyInput(name=name))
        for name in ("Comercial Uno", "Comercial Dos", "Comercial Tres")
    ]

    assert screened.runner.run_once(limit=1) == 1
    first, follow_up = first_sight_jobs(tenant)
    assert follow_up.key == f"screen_counterparties:continue:{first.id}"
    assert follow_up.status is JobStatus.PENDING
    assert [row_of(tenant, r.id).screened_at is not None for r in refs] == [
        True,
        True,
        False,
    ]

    assert screened.runner.run_once() == 1
    assert all(row_of(tenant, r.id).screened_at is not None for r in refs)
    # The second page came up short: nothing is left and no third job exists.
    assert len(first_sight_jobs(tenant)) == 2


def test_a_row_another_transaction_holds_is_retried_later(
    tenant: CreatedTenant, screened: Screened
):
    ref = resolve_one(tenant, NOBODY)
    (job_row,) = first_sight_jobs(tenant)
    job = Job(
        id=job_row.id,
        tenant_id=tenant.tenant_id,
        kind=FIRST_SIGHT_KIND,
        key=job_row.key,
        payload={},
        attempts=1,
        max_attempts=8,
        lock_token=job_row.id,
    )
    # An ingest still holds the row: the page skips it rather than wait.
    with get_engine().connect() as holder:
        _ = holder.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(tenant.tenant_id)},
        )
        _ = holder.execute(
            text("SELECT id FROM counterparty WHERE id = :id FOR UPDATE"),
            {"id": ref.id},
        ).one()
        before = clock.now()
        with job_session(tenant) as s:
            handle_first_sight(s, job)
        holder.rollback()

    assert row_of(tenant, ref.id).screened_at is None
    retry = next(j for j in first_sight_jobs(tenant) if j.id != job_row.id)
    assert retry.key == f"screen_counterparties:continue:{job_row.id}"
    assert retry.run_after >= before + SKIPPED_RETRY

    with job_session(tenant) as s:
        handle_first_sight(s, job)
    assert row_of(tenant, ref.id).screened_at is not None


def test_an_unknown_kind_is_screened_as_person_and_organization(
    tenant: CreatedTenant, screened: Screened
):
    ref = resolve_one(tenant, CounterpartyInput(name="Copa de Plata SA de CV"))

    assert screened.runner.run_once() == 1

    runs = runs_of(tenant)
    assert len(runs) == 2
    assert {r.subject_id for r in runs} == {row_of(tenant, ref.id).screening_subject_id}
    assert runs[0].query_fingerprint != runs[1].query_fingerprint
    with job_session(tenant) as s:
        keys = set(s.scalars(select(ScreeningMatch.source_entry_key)))
    assert "11006" in keys
    assert row_of(tenant, ref.id).screening_status is (
        CounterpartyScreeningStatus.PENDING_REVIEW
    )


def test_a_clear_counterparty_is_marked_clear_and_screened_at(
    tenant: CreatedTenant, screened: Screened
):
    ref = resolve_one(tenant, NOBODY)

    assert screened.runner.run_once() == 1

    row = row_of(tenant, ref.id)
    assert row.screening_status is CounterpartyScreeningStatus.CLEAR
    assert row.screened_at is not None
    (run,) = runs_of(tenant)
    assert run.hit_count == 0


def test_the_enricher_reads_unscreened_until_the_job_runs(
    tenant: CreatedTenant, screened: Screened
):
    _ = resolve_one(tenant, ALARCON)
    with job_session(tenant) as s:
        key = dedup_hash_of(s, ALARCON)
        assert status_by_dedup_hash(s, key) is CounterpartyScreeningStatus.UNSCREENED

    assert screened.runner.run_once() == 1

    with job_session(tenant) as s:
        assert status_by_dedup_hash(s, key) is (
            CounterpartyScreeningStatus.PENDING_REVIEW
        )


def test_a_resolver_without_a_queue_indexes_and_enqueues_nothing(
    tenant: CreatedTenant,
):
    set_first_sight_queue(None)
    with job_session(tenant) as s:
        ref = resolve(s, ALARCON)
        assert first_sight.enqueue_first_sight(s) is None
        index = s.scalars(
            select(CounterpartyNameIndex).where(
                CounterpartyNameIndex.counterparty_id == ref.id
            )
        ).one()
        subjects = s.scalars(select(ScreeningSubject)).all()
        jobs = s.scalars(select(JobRow)).all()
    assert index.norm_name == "rodrigo alarcon palomarez"
    assert subjects == []
    assert jobs == []


def test_a_first_sight_subject_is_a_counterparty_subject(
    tenant: CreatedTenant, screened: Screened
):
    ref = resolve_one(tenant, NOBODY)
    assert screened.runner.run_once() == 1

    with job_session(tenant) as s:
        subject = s.scalars(select(ScreeningSubject)).one()
    assert subject.kind is SubjectKind.COUNTERPARTY
    assert subject.counterparty_id == ref.id
    assert subject.party_id is None
    assert subject.input is None
    assert subject.last_screened_at is not None
