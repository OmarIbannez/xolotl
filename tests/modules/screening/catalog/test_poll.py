"""The ``lists.poll`` job: the tenant half (SP-03a D3) and the shared half (D5).

The shared half claims at most one due source under a lease, fetches it outside any
transaction, ingests it, and records the outcome; failures back off exponentially
and keep the previous version active.
"""

import logging
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from sqlalchemy import Engine, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import new_tenant, platform_ctx, system_ctx
from tests.modules.screening.catalog.support import (
    UN_V1,
    UN_V2,
    assign_tenant_wide,
    ingest_fixture,
    make_version,
    seed_sources,
)
from tests.platform.jobs_support import FakeClock, enqueue, fetch_job
from tests.platform.policy_support import install_both_packs
from xolotl.modules.screening.catalog import (
    FetchResult,
    HttpListFetcher,
    ListFetcher,
    ListSource,
    ListVersion,
    TenantListSubscription,
)
from xolotl.modules.screening.catalog.poll import (
    LEASE,
    LISTS_POLL_KIND,
    MAX_BACKOFF,
    ListsPollJob,
    PollOutcome,
    backoff,
    claim_due_source,
    claim_source,
    poll_source,
    register_lists,
)
from xolotl.modules.screening.catalog.subscriptions import LPB_MISSING_TEMPLATE
from xolotl.modules.screening.enums import VersionStatus
from xolotl.modules.screening.events import LISTS_VERSION_PUBLISHED
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.db import SessionFactory, get_sessionmaker, tenant_session
from xolotl.platform.enums import JobStatus
from xolotl.platform.events import InAppNotifier
from xolotl.platform.events.event import event_from_row
from xolotl.platform.events.models import Notification, OutboxRow
from xolotl.platform.jobs.queue import Job, PostgresJobQueue
from xolotl.platform.jobs.runner import JobRunner
from xolotl.platform.storage import ObjectStore
from xolotl.platform.storage.local import LocalFsObjectStore

NOW = datetime(2026, 10, 19, 18, tzinfo=UTC)


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock(NOW)
    monkeypatch.setattr(clock, "now", fake)
    return fake


def test_the_poll_job_runs_the_tenant_half_through_the_runner(
    tenant: CreatedTenant, admin_engine: Engine, frozen: FakeClock, tmp_path: Path
):
    seed_sources(admin_engine)
    packs = install_both_packs(admin_engine)
    _ = assign_tenant_wide(tenant, packs["MX/ITF-IFPE"], date(2026, 1, 1))
    version_id = make_version(admin_engine, "UN_CONSOLIDATED", 1)
    _ = register_lists(
        session_factory=get_sessionmaker(),
        notifier=InAppNotifier(),
        fetcher=HttpListFetcher(timeout_seconds=5, max_bytes=1_000),
        store=LocalFsObjectStore(tmp_path),
        fetching=False,
    )
    queue = PostgresJobQueue(get_sessionmaker(), clock_fn=frozen)
    job_id = enqueue(queue, tenant.tenant_id, kind=LISTS_POLL_KIND)

    runner = JobRunner(queue, get_sessionmaker(), worker_id="w-1")
    assert runner.run_once() == 1

    assert fetch_job(tenant.tenant_id, job_id).status is JobStatus.DONE
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        codes = set(s.scalars(select(TenantListSubscription.list_code)))
        published = s.scalars(
            select(OutboxRow).where(OutboxRow.type == LISTS_VERSION_PUBLISHED)
        ).all()
        templates = set(s.scalars(select(Notification.template)))
    assert "UN_CONSOLIDATED" in codes
    assert len(codes) == 5
    assert [event_from_row(row).data["version_id"] for row in published] == [
        str(version_id)
    ]
    assert LPB_MISSING_TEMPLATE in templates


# ---- the shared half: lease, conditional fetch, ingest, backoff (D5) ---------------

UN = "UN_CONSOLIDATED"
UN_URL = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
ITF = "MX/ITF-IFPE"
LATER = NOW + timedelta(days=30)
SECRET_BODY = b"<html>Internal error for Jane Q. Listed, RFC XAXX010101000</html>"


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def store(tmp_path: Path) -> LocalFsObjectStore:
    return LocalFsObjectStore(tmp_path / "objects")


@pytest.fixture
def seeded(admin_engine: Engine, frozen: FakeClock) -> None:
    # Only the UN list is due; the others wait well past every test's clock.
    seed_sources(admin_engine)
    _set_source(admin_engine, None, next_poll_at=LATER)
    _set_source(admin_engine, UN, next_poll_at=NOW - timedelta(minutes=1))


@pytest.fixture
def two_tenants(
    tenant: CreatedTenant, admin_engine: Engine, seeded: None
) -> list[CreatedTenant]:
    packs = install_both_packs(admin_engine)
    other = new_tenant("beta", "admin@beta.mx")
    for each in (tenant, other):
        _ = assign_tenant_wide(each, packs[ITF], date(2026, 1, 1))
    return [tenant, other]


def _set_source(admin_engine: Engine, code: str | None, **values: object) -> None:
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    with tenant_session(platform_ctx(), factory=owner) as s:
        statement = update(ListSource).values(**values)
        if code is not None:
            statement = statement.where(ListSource.code == code)
        _ = s.execute(statement)


def _source(code: str = UN) -> ListSource:
    with tenant_session(platform_ctx()) as s:
        source = s.get(ListSource, code)
        assert source is not None
        return source


def _versions(code: str = UN) -> list[tuple[int, VersionStatus]]:
    with tenant_session(platform_ctx()) as s:
        rows = s.execute(
            select(ListVersion.seq, ListVersion.status)
            .where(ListVersion.list_code == code)
            .order_by(ListVersion.seq)
        ).all()
        return [(seq, status) for seq, status in rows]


def _published(tenant_id: UUID) -> list[dict[str, object]]:
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.scalars(
            select(OutboxRow).where(OutboxRow.type == LISTS_VERSION_PUBLISHED)
        ).all()
        return [dict(event_from_row(row).data) for row in rows]


def _job(
    store: ObjectStore,
    clock_fn: FakeClock,
    *,
    fetching: bool = True,
    fetcher: ListFetcher | None = None,
) -> ListsPollJob:
    return ListsPollJob(
        session_factory=get_sessionmaker(),
        notifier=InAppNotifier(),
        fetcher=fetcher or HttpListFetcher(timeout_seconds=5, max_bytes=10_000_000),
        store=store,
        fetching=fetching,
        clock_fn=clock_fn,
    )


def _run(job: ListsPollJob, tenant_id: UUID) -> None:
    job_id = uuid4()
    claimed = Job(
        id=job_id,
        tenant_id=tenant_id,
        kind=LISTS_POLL_KIND,
        key=None,
        payload={},
        attempts=1,
        max_attempts=8,
        lock_token=uuid4(),
    )
    with tenant_session(system_ctx(tenant_id, f"job:{job_id}")) as s:
        job.run(s, claimed)


def _poll(
    store: ObjectStore,
    *,
    now: datetime = NOW,
    fetcher: ListFetcher | None = None,
    session_factory: SessionFactory | None = None,
) -> PollOutcome:
    # Claim the source first, as the job and the CLI do, so that the lease the
    # poll releases is one it really held.
    with tenant_session(platform_ctx("job:test")) as s:
        assert claim_source(s, UN, holder="job:test", now=now) is not None
    assert _source().lease_holder == "job:test"
    return poll_source(
        session_factory or get_sessionmaker(),
        UN,
        holder="job:test",
        fetcher=fetcher or HttpListFetcher(timeout_seconds=5, max_bytes=10_000_000),
        store=store,
        now=now,
    )


def _without_entities() -> bytes:
    # The UN fixture without its entities: 8 of 30 entries removed.
    text_v1 = UN_V1.read_text(encoding="utf-8")
    start, end = text_v1.index("<ENTITIES>"), text_v1.index("</ENTITIES>")
    return (text_v1[:start] + "<ENTITIES>" + text_v1[end:]).encode()


def test_a_due_source_is_fetched_ingested_and_caught_up_in_one_run(
    two_tenants: list[CreatedTenant],
    store: LocalFsObjectStore,
    frozen: FakeClock,
    mock: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    route = mock.get(UN_URL).respond(
        200,
        content=UN_V1.read_bytes(),
        headers={"ETag": '"v1"', "Last-Modified": "Mon, 19 Oct 2026 12:00:00 GMT"},
    )
    _run(_job(store, frozen), two_tenants[0].tenant_id)

    assert route.call_count == 1
    assert _versions() == [(1, VersionStatus.ACTIVE)]
    source = _source()
    assert source.etag == '"v1"'
    assert source.last_modified == "Mon, 19 Oct 2026 12:00:00 GMT"
    assert source.lease_until is None
    assert source.lease_holder is None
    assert source.last_success_at == NOW
    assert source.last_change_at == NOW
    assert source.next_poll_at == NOW + timedelta(minutes=15)
    with tenant_session(platform_ctx()) as s:
        loaded_by = s.scalar(select(ListVersion.loaded_by))
    assert loaded_by is not None
    assert loaded_by.startswith("job:")
    [event] = _published(two_tenants[0].tenant_id)
    assert event["list_code"] == UN
    assert event["initial"] is True
    assert "lists poll: UN_CONSOLIDATED INGESTED" in caplog.messages


def test_not_modified_refreshes_freshness_and_keeps_the_version(
    two_tenants: list[CreatedTenant],
    admin_engine: Engine,
    store: LocalFsObjectStore,
    frozen: FakeClock,
    mock: respx.MockRouter,
):
    _ = ingest_fixture(UN, UN_V1, store=store)
    _set_source(
        admin_engine,
        UN,
        etag='"v1"',
        last_modified="Mon, 19 Oct 2026 12:00:00 GMT",
        last_success_at=NOW - timedelta(hours=1),
        next_poll_at=NOW - timedelta(minutes=1),
    )
    route = mock.get(UN_URL).respond(304)
    _run(_job(store, frozen), two_tenants[0].tenant_id)

    assert route.call_count == 1
    request = route.calls.last.request
    assert request.headers["If-None-Match"] == '"v1"'
    assert request.headers["If-Modified-Since"] == "Mon, 19 Oct 2026 12:00:00 GMT"
    assert _versions() == [(1, VersionStatus.ACTIVE)]
    source = _source()
    assert source.last_success_at == NOW
    assert source.next_poll_at == NOW + timedelta(minutes=15)
    assert source.etag == '"v1"'
    assert source.last_modified == "Mon, 19 Oct 2026 12:00:00 GMT"
    assert source.lease_until is None
    assert source.lease_holder is None


def test_a_body_equal_to_the_active_file_is_same_file(
    seeded: None, store: LocalFsObjectStore, mock: respx.MockRouter
):
    _ = ingest_fixture(UN, UN_V1, store=store)
    _ = mock.get(UN_URL).respond(200, content=UN_V1.read_bytes())
    outcome = _poll(store)
    assert outcome.result == "SAME_FILE"
    assert outcome.error is None
    assert _versions() == [(1, VersionStatus.ACTIVE)]
    source = _source()
    assert source.last_success_at == NOW
    assert source.lease_until is None
    assert source.lease_holder is None


def test_two_tenants_polling_at_once_fetch_once(
    two_tenants: list[CreatedTenant],
    store: LocalFsObjectStore,
    frozen: FakeClock,
    mock: respx.MockRouter,
):
    started = threading.Event()
    release = threading.Event()

    def slow(_request: httpx.Request) -> httpx.Response:
        started.set()
        assert release.wait(timeout=30)
        return httpx.Response(200, content=UN_V1.read_bytes())

    route = mock.get(UN_URL).mock(side_effect=slow)
    job = _job(store, frozen)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_run, job, two_tenants[0].tenant_id)
        assert started.wait(timeout=30)
        # The second tenant's poll finds the source leased and runs its tenant half.
        pool.submit(_run, job, two_tenants[1].tenant_id).result(timeout=30)
        release.set()
        first.result(timeout=30)

    assert route.call_count == 1
    assert _versions() == [(1, VersionStatus.ACTIVE)]


def _claim(*, holder: str = "job:me") -> ListSource | None:
    with tenant_session(platform_ctx(holder)) as s:
        return claim_due_source(s, holder=holder, now=NOW)


def test_an_expired_lease_is_taken_over(admin_engine: Engine, seeded: None):
    _set_source(
        admin_engine, UN, lease_until=NOW - timedelta(seconds=1), lease_holder="job:x"
    )
    claimed = _claim()
    assert claimed is not None
    assert claimed.code == UN
    source = _source()
    assert source.lease_holder == "job:me"
    assert source.lease_until == NOW + LEASE


def test_a_leased_or_not_due_source_is_skipped(admin_engine: Engine, seeded: None):
    _set_source(
        admin_engine, UN, lease_until=NOW + timedelta(minutes=5), lease_holder="job:x"
    )
    assert _claim() is None
    assert _source().lease_holder == "job:x"
    _set_source(
        admin_engine,
        UN,
        lease_until=None,
        lease_holder=None,
        next_poll_at=NOW + timedelta(minutes=1),
    )
    assert _claim() is None
    _set_source(admin_engine, UN, next_poll_at=NOW, enabled=False)
    assert _claim() is None


def test_claim_source_ignores_next_poll_at_but_not_a_lease(
    admin_engine: Engine, seeded: None
):
    _set_source(admin_engine, UN, next_poll_at=LATER)
    with tenant_session(platform_ctx("cli:op")) as s:
        claimed = claim_source(s, UN, holder="cli:op", now=NOW)
    assert claimed is not None
    assert _source().lease_holder == "cli:op"
    with tenant_session(platform_ctx("cli:other")) as s:
        assert claim_source(s, UN, holder="cli:other", now=NOW) is None


def _assert_failed_once(outcome: PollOutcome, prefix: str) -> ListSource:
    assert outcome.result == "FAILED"
    assert outcome.report is None
    assert outcome.error is not None
    assert outcome.error.startswith(prefix)
    source = _source()
    assert source.last_error == outcome.error
    assert source.consecutive_failures == 1
    assert source.last_attempt_at == NOW
    assert source.next_poll_at == NOW + timedelta(minutes=30)
    assert source.lease_until is None
    assert source.lease_holder is None
    return source


def test_a_truncated_file_keeps_the_previous_version(
    seeded: None, store: LocalFsObjectStore, mock: respx.MockRouter
):
    _ = ingest_fixture(UN, UN_V1, store=store)
    body = UN_V2.read_bytes()
    _ = mock.get(UN_URL).respond(200, content=body[: len(body) // 2])
    source = _assert_failed_once(_poll(store), "LIST_FORMAT_INVALID: ")
    assert source.last_success_at == NOW
    assert _versions() == [(1, VersionStatus.ACTIVE)]


def test_an_html_error_page_is_a_format_error(
    seeded: None, store: LocalFsObjectStore, mock: respx.MockRouter
):
    _ = ingest_fixture(UN, UN_V1, store=store)
    _ = mock.get(UN_URL).respond(
        200,
        content=b"<html><body>Service temporarily unavailable</body></html>",
        headers={"Content-Type": "text/html"},
    )
    _ = _assert_failed_once(_poll(store), "LIST_FORMAT_INVALID: ")
    assert _versions() == [(1, VersionStatus.ACTIVE)]


def test_mass_removal_is_refused_and_backs_off(
    two_tenants: list[CreatedTenant],
    admin_engine: Engine,
    store: LocalFsObjectStore,
    frozen: FakeClock,
    mock: respx.MockRouter,
):
    tenant_id = two_tenants[0].tenant_id
    _ = ingest_fixture(UN, UN_V1, store=store)
    _run(_job(store, frozen, fetching=False), tenant_id)
    assert len(_published(tenant_id)) == 1
    _ = mock.get(UN_URL).respond(200, content=_without_entities())
    _set_source(admin_engine, UN, next_poll_at=NOW - timedelta(minutes=1))

    _run(_job(store, frozen), tenant_id)

    source = _source()
    assert source.last_error == "LIST_MASS_REMOVAL: removed 8 of 30"
    assert source.consecutive_failures == 1
    assert source.next_poll_at == NOW + timedelta(minutes=30)
    assert source.lease_until is None
    assert source.lease_holder is None
    assert _versions() == [(1, VersionStatus.ACTIVE)]
    assert len(_published(tenant_id)) == 1


def test_failures_back_off_exponentially_up_to_six_hours(
    admin_engine: Engine,
    seeded: None,
    store: LocalFsObjectStore,
    mock: respx.MockRouter,
):
    _ = mock.get(UN_URL).respond(503)
    waits = []
    for _attempt in range(3):
        outcome = _poll(store)
        assert outcome.error == "HTTP_503: the server answered 503"
        waits.append(_source().next_poll_at - NOW)
    assert waits == [timedelta(minutes=m) for m in (30, 60, 120)]
    _set_source(admin_engine, UN, consecutive_failures=9)
    _ = _poll(store)
    source = _source()
    assert source.consecutive_failures == 10
    assert source.next_poll_at == NOW + MAX_BACKOFF
    assert backoff(timedelta(days=1), 1000) == MAX_BACKOFF


def test_a_success_after_failures_resets_the_count(
    admin_engine: Engine,
    seeded: None,
    store: LocalFsObjectStore,
    mock: respx.MockRouter,
):
    _set_source(
        admin_engine, UN, consecutive_failures=3, last_error="HTTP_503: earlier"
    )
    _ = mock.get(UN_URL).respond(200, content=UN_V1.read_bytes())
    outcome = _poll(store)
    assert outcome.result == "INGESTED"
    assert outcome.report is not None
    assert outcome.report.seq == 1
    source = _source()
    assert source.consecutive_failures == 0
    assert source.last_error is None
    assert source.next_poll_at == NOW + timedelta(minutes=15)


def test_fetching_off_never_touches_the_network(
    two_tenants: list[CreatedTenant],
    store: LocalFsObjectStore,
    frozen: FakeClock,
    mock: respx.MockRouter,
):
    route = mock.get(UN_URL).respond(200, content=UN_V1.read_bytes())
    _run(_job(store, frozen, fetching=False), two_tenants[0].tenant_id)
    assert route.call_count == 0
    source = _source()
    assert source.lease_until is None
    assert source.lease_holder is None
    assert _versions() == []
    with tenant_session(system_ctx(two_tenants[0].tenant_id)) as s:
        codes = set(s.scalars(select(TenantListSubscription.list_code)))
    assert UN in codes


def test_last_error_never_holds_a_response_body(
    admin_engine: Engine,
    seeded: None,
    store: LocalFsObjectStore,
    mock: respx.MockRouter,
):
    _ = mock.get(UN_URL).respond(500, content=SECRET_BODY)
    outcome = _poll(store)
    assert outcome.error == "HTTP_500: the server answered 500"
    with admin_engine.connect() as conn:
        row = conn.execute(
            text("SELECT row_to_json(s)::text FROM list_source s WHERE code = :c"),
            {"c": UN},
        ).scalar_one()
    assert "Jane" not in row
    assert "XAXX010101000" not in row


class _BrokenFetcher:
    """A fetcher with a bug: every call raises something no poll expects."""

    def __init__(self) -> None:
        self.calls: int = 0

    def fetch(
        self, url: str, *, etag: str | None, last_modified: str | None
    ) -> FetchResult:
        self.calls += 1
        msg = f"bug while fetching {url} {etag} {last_modified}"
        raise RuntimeError(msg)


def test_an_unexpected_error_still_releases_the_lease(
    seeded: None, store: LocalFsObjectStore, caplog: pytest.LogCaptureFixture
):
    broken = _BrokenFetcher()
    outcome = _poll(store, fetcher=broken)
    assert broken.calls == 1
    _ = _assert_failed_once(outcome, "INTERNAL: RuntimeError")
    assert any(r.levelname == "ERROR" for r in caplog.records)


def test_a_failure_reading_the_source_still_releases_the_lease(
    seeded: None, store: LocalFsObjectStore, caplog: pytest.LogCaptureFixture
):
    sessions = get_sessionmaker()
    opened = 0

    def flaky() -> Session:
        # The first session, the one that reads the source's url, fails to open.
        nonlocal opened
        opened += 1
        if opened == 1:
            msg = "database went away"
            raise RuntimeError(msg)
        return sessions()

    broken = _BrokenFetcher()
    outcome = _poll(store, fetcher=broken, session_factory=flaky)
    assert broken.calls == 0
    _ = _assert_failed_once(outcome, "INTERNAL: RuntimeError")
    assert any(r.levelname == "ERROR" for r in caplog.records)
