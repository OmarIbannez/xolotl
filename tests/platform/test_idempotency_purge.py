"""Expired ``Idempotency-Key`` records are purged per tenant (spec §7, plan 1c).

``purge_expired`` deletes one bounded batch of the session tenant's expired keys,
passing by any key another transaction holds. The opportunistic purge in front of
every reservation runs one batch, and the ``idempotency.purge`` job, which reaches
tenants that make no requests at all, runs batch after batch, each in a transaction
of its own, so neither ever waits for the other or holds a tenant's keys for long.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, insert, select, text

from tests.helpers import make_tenant_with_keys, platform_ctx, system_ctx
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.api.idempotency import (
    PURGE_BATCH,
    PURGE_KIND,
    TTL,
    purge_expired,
    purge_expired_keys,
)
from xolotl.platform.api.models import IdempotencyKey
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.errors import ContextMissingError
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.queue import Job, PostgresJobQueue, register_job_handler
from xolotl.platform.jobs.runner import JobRunner

NOW = datetime.fromisoformat("2026-10-19T12:00:00+00:00")


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> datetime:
    monkeypatch.setattr(clock, "now", lambda: NOW)
    return NOW


def add_key(tenant_id: UUID, key: str, *, expires_at: datetime, actor: str = "a"):
    with tenant_session(system_ctx(tenant_id)) as s:
        s.add(
            IdempotencyKey(
                tenant_id=tenant_id,
                actor_id=actor,
                key=key,
                request_hash=b"",
                status_code=200,
                response_body={},
                expires_at=expires_at,
            )
        )


def stored_keys(tenant_id: UUID) -> set[str]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return set(s.scalars(select(IdempotencyKey.key)))


def test_expired_keys_of_every_actor_go_and_live_ones_stay(
    engine: Engine, frozen: datetime
):
    tenant_id = make_tenant_with_keys("alpha")
    add_key(tenant_id, "long-gone", expires_at=frozen - TTL)
    add_key(tenant_id, "just-now", expires_at=frozen)  # expired at this very instant
    add_key(tenant_id, "other-actor", expires_at=frozen, actor="b")
    add_key(tenant_id, "live", expires_at=frozen + timedelta(microseconds=1))
    with tenant_session(system_ctx(tenant_id)) as s:
        assert purge_expired(s) == 3
    assert stored_keys(tenant_id) == {"live"}


def test_the_purge_stays_inside_the_sessions_tenant(engine: Engine, frozen: datetime):
    alpha, beta = make_tenant_with_keys("alpha"), make_tenant_with_keys("beta")
    add_key(alpha, "alpha-expired", expires_at=frozen - timedelta(hours=1))
    add_key(beta, "beta-expired", expires_at=frozen - timedelta(hours=1))
    with tenant_session(system_ctx(alpha)) as s:
        assert purge_expired(s) == 1
    assert stored_keys(alpha) == set()
    assert stored_keys(beta) == {"beta-expired"}


def test_nothing_to_purge_is_zero(engine: Engine, frozen: datetime):
    tenant_id = make_tenant_with_keys("alpha")
    add_key(tenant_id, "live", expires_at=frozen + TTL)
    with tenant_session(system_ctx(tenant_id)) as s:
        assert purge_expired(s) == 0
    assert stored_keys(tenant_id) == {"live"}


def test_the_purge_needs_a_session_bound_to_a_tenant(engine: Engine):
    with (
        tenant_session(platform_ctx()) as s,
        pytest.raises(ContextMissingError),
    ):
        _ = purge_expired(s)


def test_the_purge_job_clears_its_tenants_expired_keys(
    engine: Engine, frozen: datetime
):
    alpha, beta = make_tenant_with_keys("alpha"), make_tenant_with_keys("beta")
    for tenant_id in (alpha, beta):
        add_key(tenant_id, "expired", expires_at=frozen - timedelta(minutes=5))
        add_key(tenant_id, "live", expires_at=frozen + timedelta(minutes=5))
    factory = get_sessionmaker()
    queue = PostgresJobQueue(factory, clock_fn=lambda: frozen)
    register_job_handler(PURGE_KIND, purge_expired_keys)
    with tenant_session(system_ctx(alpha)) as s:
        _ = queue.enqueue(s, kind=PURGE_KIND, payload={})

    assert JobRunner(queue, factory, worker_id="w-purge").run_once() == 1

    assert stored_keys(alpha) == {"live"}
    assert stored_keys(beta) == {"expired", "live"}  # its own job has not run


def test_the_job_kind_is_idempotency_purge():
    assert PURGE_KIND == "idempotency.purge"


def add_expired_keys(tenant_id: UUID, count: int, *, expired_at: datetime) -> None:
    """Insert ``count`` expired keys a minute apart, ``k-00000`` the longest expired.

    They go in newest first, so the table's own order is the reverse of the order in
    which they expired.
    """
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            insert(IdempotencyKey),
            [
                {
                    "tenant_id": tenant_id,
                    "actor_id": "bulk",
                    "key": f"k-{number:05d}",
                    "request_hash": b"",
                    "status_code": 200,
                    "response_body": {},
                    "expires_at": expired_at - timedelta(minutes=count - number),
                }
                for number in reversed(range(count))
            ],
        )


def a_purge_job(tenant_id: UUID) -> Job:
    return Job(
        id=uuid7(),
        tenant_id=tenant_id,
        kind=PURGE_KIND,
        key=None,
        payload={},
        attempts=1,
        max_attempts=8,
        lock_token=uuid7(),
    )


def test_a_batch_is_about_a_thousand_keys():
    assert PURGE_BATCH == 1000


def test_one_purge_deletes_one_batch_the_longest_expired_first(
    engine: Engine, frozen: datetime
):
    tenant_id = make_tenant_with_keys("alpha")
    add_expired_keys(tenant_id, PURGE_BATCH + 7, expired_at=frozen)
    with tenant_session(system_ctx(tenant_id)) as s:
        assert purge_expired(s) == PURGE_BATCH
    # The seven left are the ones that expired last.
    assert stored_keys(tenant_id) == {f"k-{n:05d}" for n in range(1000, 1007)}


def test_the_job_purges_batch_after_batch_and_commits_each_one(
    engine: Engine, frozen: datetime
):
    tenant_id = make_tenant_with_keys("alpha")
    add_expired_keys(tenant_id, 2 * PURGE_BATCH + 345, expired_at=frozen)
    add_key(tenant_id, "live", expires_at=frozen + timedelta(minutes=5))
    commits: list[object] = []
    with tenant_session(system_ctx(tenant_id)) as s:
        event.listen(s, "after_commit", commits.append)
        purge_expired_keys(s, a_purge_job(tenant_id))
        # One commit after each full batch, so no batch's locks outlive it; the
        # last, short batch commits with the job.
        assert len(commits) == 2
    assert stored_keys(tenant_id) == {"live"}


def test_a_key_another_transaction_holds_is_passed_by_not_waited_for(
    engine: Engine, frozen: datetime
):
    tenant_id = make_tenant_with_keys("alpha")
    for key in ("held", "free-1", "free-2"):
        add_key(tenant_id, key, expires_at=frozen - timedelta(minutes=1))
    with tenant_session(system_ctx(tenant_id)) as holder:
        _ = holder.execute(
            select(IdempotencyKey).where(IdempotencyKey.key == "held").with_for_update()
        ).all()
        with tenant_session(system_ctx(tenant_id)) as s:
            # A purge that waited for the lock would fail here rather than hang.
            _ = s.execute(text("SET LOCAL lock_timeout = '2s'"))
            assert purge_expired(s) == 2
    assert stored_keys(tenant_id) == {"held"}
    with tenant_session(system_ctx(tenant_id)) as s:
        assert purge_expired(s) == 1  # the next purge takes it
    assert stored_keys(tenant_id) == set()


def test_a_request_never_waits_for_a_purge_in_progress(
    client: TestClient, tenant: CreatedTenant, admin_headers: dict[str, str]
):
    add_key(tenant.tenant_id, "expired", expires_at=clock.now() - timedelta(minutes=1))
    # Leaving the block releases the lock first, then waits for the request.
    with (
        ThreadPoolExecutor(max_workers=1) as pool,
        tenant_session(system_ctx(tenant.tenant_id)) as purging,
    ):
        # What the purge job holds while its batch is open.
        _ = purging.execute(
            select(IdempotencyKey)
            .where(IdempotencyKey.key == "expired")
            .with_for_update()
        ).all()
        ping = pool.submit(
            client.post,
            "/v1/ping",
            json={},
            headers={**admin_headers, "Idempotency-Key": "during-a-purge"},
        )
        # The request's own purge passes the held key by instead of queueing.
        assert ping.result(timeout=10).status_code == 200
    assert "expired" in stored_keys(tenant.tenant_id)
