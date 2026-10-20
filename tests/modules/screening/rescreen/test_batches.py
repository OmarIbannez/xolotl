"""Re-screen batch bookkeeping and the batches API (SP-03 spec §5.2, §10, §12; F1).

A batch is opened by ``(kind, ref)``, so a redelivered event or a second schedule
tick finds the same row; it is advanced in each page's transaction and never moves
its cursor backwards, so a replayed page is recognised and skipped.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.helpers import new_tenant
from tests.modules.party.route_support import machine_headers
from tests.modules.screening.rescreen.conftest import job_session
from tests.modules.screening.route_support import SCREENING, staff_client
from xolotl.modules.screening.engine.enums import BatchKind, BatchStatus
from xolotl.modules.screening.engine.models import ScreeningBatch
from xolotl.modules.screening.rescreen.batches import (
    advance_batch,
    complete_batch,
    lock_batch,
    open_batch,
    page_already_done,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.enums import Role
from xolotl.platform.errors import NotFoundError
from xolotl.platform.ids import uuid7

BATCHES = f"{SCREENING}/batches"
VERSION = "0192f000-0000-7000-8000-000000000001"


def _open(tenant: CreatedTenant, kind: BatchKind, ref: str) -> UUID | None:
    with job_session(tenant) as s:
        batch = open_batch(s, kind=kind, ref=ref, total=10)
        return None if batch is None else batch.id


def _batch(tenant: CreatedTenant, batch_id: UUID) -> ScreeningBatch:
    with job_session(tenant) as s:
        return s.scalars(
            select(ScreeningBatch).where(ScreeningBatch.id == batch_id)
        ).one()


def test_opening_a_batch_twice_returns_the_same_row(tenant: CreatedTenant):
    first = _open(tenant, BatchKind.LIST_DELTA, VERSION)
    second = _open(tenant, BatchKind.LIST_DELTA, VERSION)

    assert first is not None
    assert second == first
    batch = _batch(tenant, first)
    assert (batch.status, batch.total, batch.done, batch.cursor) == (
        BatchStatus.RUNNING,
        10,
        0,
        None,
    )
    # Another kind with the same ref is another batch.
    assert _open(tenant, BatchKind.PRIVATE_LIST, VERSION) not in {None, first}


def test_a_completed_batch_is_never_reopened(tenant: CreatedTenant):
    batch_id = _open(tenant, BatchKind.PERIODIC, "2026-10")
    assert batch_id is not None
    with job_session(tenant) as s:
        complete_batch(lock_batch(s, batch_id))

    assert _open(tenant, BatchKind.PERIODIC, "2026-10") is None
    batch = _batch(tenant, batch_id)
    assert batch.status is BatchStatus.COMPLETED
    assert batch.finished_at is not None
    assert batch.last_error is None


def test_a_completed_batch_keeps_its_note(tenant: CreatedTenant):
    batch_id = _open(tenant, BatchKind.LIST_DELTA, VERSION)
    assert batch_id is not None
    with job_session(tenant) as s:
        complete_batch(lock_batch(s, batch_id), note="OVERFLOW:1")

    assert _batch(tenant, batch_id).last_error == "OVERFLOW:1"


def test_a_failed_batch_resumes_from_its_cursor(tenant: CreatedTenant):
    batch_id = _open(tenant, BatchKind.LIST_DELTA, VERSION)
    assert batch_id is not None
    with job_session(tenant) as s:
        batch = lock_batch(s, batch_id)
        advance_batch(batch, done=200, runs=3, new_matches=1, cursor="200")
        batch.status = BatchStatus.FAILED
        batch.last_error = "JOB_FAILED"

    assert _open(tenant, BatchKind.LIST_DELTA, VERSION) == batch_id
    batch = _batch(tenant, batch_id)
    assert (batch.status, batch.cursor, batch.last_error, batch.done) == (
        BatchStatus.RUNNING,
        "200",
        None,
        200,
    )


def test_the_cursor_never_moves_backwards(tenant: CreatedTenant):
    delta_id = _open(tenant, BatchKind.LIST_DELTA, VERSION)
    walker_id = _open(tenant, BatchKind.PERIODIC, "2026-10")
    assert delta_id is not None
    assert walker_id is not None
    low, high = UUID(int=1 << 64), UUID(int=1 << 65)
    with job_session(tenant) as s:
        delta = lock_batch(s, delta_id)
        advance_batch(delta, done=200, runs=2, new_matches=1, cursor="1000")
        # "200" sorts after "1000" as text: offsets compare as numbers.
        advance_batch(delta, done=0, runs=1, new_matches=0, cursor="200")
        walker = lock_batch(s, walker_id)
        advance_batch(walker, done=500, runs=500, new_matches=0, cursor=str(high))
        advance_batch(walker, done=1, runs=1, new_matches=2, cursor=str(low))

    delta = _batch(tenant, delta_id)
    assert (delta.cursor, delta.done, delta.runs, delta.new_matches) == (
        "1000",
        200,
        3,
        1,
    )
    walker = _batch(tenant, walker_id)
    assert (walker.cursor, walker.done, walker.runs, walker.new_matches) == (
        str(high),
        501,
        501,
        2,
    )


def test_page_already_done_compares_offsets_and_party_ids(tenant: CreatedTenant):
    delta_id = _open(tenant, BatchKind.LIST_DELTA, VERSION)
    walker_id = _open(tenant, BatchKind.REINDEX, "matcher/2")
    done_id = _open(tenant, BatchKind.PERIODIC, "2026-09")
    assert delta_id is not None
    assert walker_id is not None
    assert done_id is not None
    first, middle, last = (UUID(int=1 << n) for n in (63, 64, 65))
    with job_session(tenant) as s:
        delta = lock_batch(s, delta_id)
        walker = lock_batch(s, walker_id)
        # Nothing has run yet: the first page is still to do.
        assert not page_already_done(delta, "0")
        assert not page_already_done(walker, "start")

        advance_batch(delta, done=200, runs=1, new_matches=0, cursor="1000")
        advance_batch(walker, done=500, runs=500, new_matches=0, cursor=str(middle))
        completed = lock_batch(s, done_id)
        complete_batch(completed)

        assert page_already_done(delta, "0")
        assert page_already_done(delta, "200")
        assert not page_already_done(delta, "1000")
        assert not page_already_done(delta, "1200")
        assert page_already_done(walker, "start")
        assert page_already_done(walker, str(first))
        assert not page_already_done(walker, str(middle))
        assert not page_already_done(walker, str(last))
        assert page_already_done(completed, "start")


def test_locking_an_unknown_batch_is_not_found(tenant: CreatedTenant):
    with pytest.raises(NotFoundError), job_session(tenant) as s:
        _ = lock_batch(s, uuid7())


# ---- the API --------------------------------------------------------------------


def _seed(tenant: CreatedTenant) -> list[UUID]:
    """Add three batches started a minute apart, oldest first.

    Returns:
        Their ids, oldest first.

    """
    start = datetime(2026, 10, 1, 8, tzinfo=UTC)
    rows = [
        (BatchKind.LIST_DELTA, VERSION, BatchStatus.COMPLETED),
        (BatchKind.PERIODIC, "2026-10", BatchStatus.RUNNING),
        (BatchKind.REINDEX, "matcher/2", BatchStatus.FAILED),
    ]
    with job_session(tenant) as s:
        batches = [
            ScreeningBatch(
                tenant_id=tenant.tenant_id,
                kind=kind,
                ref=ref,
                status=status,
                total=20,
                done=5,
                started_at=start + timedelta(minutes=n),
            )
            for n, (kind, ref, status) in enumerate(rows)
        ]
        s.add_all(batches)
        s.flush()
        return [batch.id for batch in batches]


# Spec §12 makes the route staff only; the plan's F1 named a screening:read key too,
# and the spec wins (an API client never sees list versions or finding counts).
def test_batches_are_listed_newest_first_for_staff_only(
    client: TestClient, tenant: CreatedTenant
):
    ids = _seed(tenant)
    analyst = staff_client(client, tenant, Role.ANALYST)

    listed = client.get(BATCHES, headers=analyst)
    assert listed.status_code == 200, listed.text
    assert [b["id"] for b in listed.json()["items"]] == [str(i) for i in reversed(ids)]
    newest = client.get(BATCHES, headers=analyst).json()["items"][0]
    assert {k: newest[k] for k in ("kind", "ref", "status", "total", "done")} == {
        "kind": "REINDEX",
        "ref": "matcher/2",
        "status": "FAILED",
        "total": 20,
        "done": 5,
    }
    assert set(newest) == {
        "id",
        "kind",
        "ref",
        "status",
        "total",
        "done",
        "runs",
        "new_matches",
        "started_at",
        "finished_at",
        "last_error",
    }

    paged = client.get(BATCHES, headers=analyst, params={"limit": 2}).json()
    assert [b["id"] for b in paged["items"]] == [str(ids[2]), str(ids[1])]
    rest = client.get(
        BATCHES, headers=analyst, params={"limit": 2, "cursor": paged["next_cursor"]}
    ).json()
    assert [b["id"] for b in rest["items"]] == [str(ids[0])]
    assert rest["next_cursor"] is None

    by_kind = client.get(BATCHES, headers=analyst, params={"kind": "PERIODIC"})
    assert [b["id"] for b in by_kind.json()["items"]] == [str(ids[1])]
    by_status = client.get(BATCHES, headers=analyst, params={"status": "COMPLETED"})
    assert [b["id"] for b in by_status.json()["items"]] == [str(ids[0])]

    machine = machine_headers(tenant, "screening:read", "screening:write")
    refused = client.get(BATCHES, headers=machine)
    assert (refused.status_code, refused.json()["code"]) == (403, "ROLE_FORBIDDEN")


def test_another_tenant_sees_no_batches(client: TestClient, tenant: CreatedTenant):
    _ = _seed(tenant)
    other = new_tenant("globex", "admin@globex.mx")
    theirs = staff_client(client, other, Role.MLRO)

    listed = client.get(BATCHES, headers=theirs)

    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == []
    with job_session(other) as s:
        assert open_batch(s, kind=BatchKind.LIST_DELTA, ref=VERSION, total=1)
    assert len(client.get(BATCHES, headers=theirs).json()["items"]) == 1
