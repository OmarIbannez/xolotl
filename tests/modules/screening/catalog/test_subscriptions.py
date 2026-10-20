"""The ``lists.poll`` tenant half: subscriptions, catch-up, staleness and the LPB.

Subscriptions follow ``effective_policy(...).screening``: mandatory lists are forced
on, recommended ones start on and the tenant may switch them off, and lists that
leave the policy go. The catch-up hands each new version over as one
``lists.version_published.v1`` (the first as ``initial``), exactly once even when two
polls overlap. A stale source and a missing LPB notify ADMIN and MLRO once per
episode and once a Mexico City day.
"""

import threading
import time
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import sessionmaker

from tests.helpers import new_tenant, platform_ctx, system_ctx
from tests.modules.screening.catalog.support import (
    assign_tenant_wide,
    make_version,
    seed_sources,
)
from tests.platform.jobs_support import FakeClock
from tests.platform.policy_support import install_both_packs
from xolotl.modules.screening.catalog import ListSource, TenantListSubscription
from xolotl.modules.screening.catalog.subscriptions import (
    LPB_MISSING_TEMPLATE,
    STALE_TEMPLATE,
    ReconcileReport,
    catch_up,
    check_staleness,
    is_stale,
    reconcile_subscriptions,
    remind_lpb_missing,
)
from xolotl.modules.screening.enums import ListKind, ListRequirement, SourceFormat
from xolotl.modules.screening.events import EVENT_SOURCE, LISTS_VERSION_PUBLISHED
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.events import InAppNotifier
from xolotl.platform.events.event import Event, event_from_row
from xolotl.platform.events.models import Notification, OutboxRow

ITF = "MX/ITF-IFPE"
AVI = "MX/LFPIORPI-AVI"
# 18:00 UTC is noon in Mexico City.
NOW = datetime(2026, 10, 19, 18, tzinfo=UTC)
TODAY = date(2026, 10, 19)
RECOMMENDED_ITF = ("EU_FSF", "MX_SAT_69B", "OFAC_SDN", "UK_FCDO")


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock(NOW)
    monkeypatch.setattr(clock, "now", fake)
    return fake


@pytest.fixture
def packs(admin_engine: Engine) -> dict[str, UUID]:
    seed_sources(admin_engine)
    return install_both_packs(admin_engine)


@pytest.fixture
def itf_tenant(
    tenant: CreatedTenant, packs: dict[str, UUID], frozen: FakeClock
) -> CreatedTenant:
    _ = assign_tenant_wide(tenant, packs[ITF], date(2026, 1, 1))
    return tenant


def _reconcile(tenant_id: UUID, today: date = TODAY) -> ReconcileReport | None:
    with tenant_session(system_ctx(tenant_id)) as s:
        return reconcile_subscriptions(s, today=today)


def _catch_up(tenant_id: UUID) -> list[Event]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return catch_up(s)


def _subscriptions(tenant_id: UUID) -> dict[str, tuple[ListRequirement, bool, int]]:
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.scalars(select(TenantListSubscription)).all()
        return {r.list_code: (r.requirement, r.enabled, r.last_seq) for r in rows}


def _set_enabled(tenant_id: UUID, code: str, *, enabled: bool) -> None:
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            update(TenantListSubscription)
            .where(TenantListSubscription.list_code == code)
            .values(enabled=enabled)
        )


def _published(tenant_id: UUID) -> list[Event]:
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.scalars(
            select(OutboxRow)
            .where(OutboxRow.type == LISTS_VERSION_PUBLISHED)
            .order_by(OutboxRow.seq)
        ).all()
        return [event_from_row(row) for row in rows]


def _notices(tenant_id: UUID, template: str) -> list[tuple[str | None, dict]]:
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.scalars(
            select(Notification)
            .where(Notification.template == template)
            .order_by(Notification.created_at, Notification.recipient_role)
        ).all()
        return [(r.recipient_role, dict(r.data)) for r in rows]


def _set_success(admin_engine: Engine, at: datetime | None, *codes: str) -> None:
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    with tenant_session(platform_ctx(), factory=owner) as s:
        query = update(ListSource).values(last_success_at=at)
        if codes:
            query = query.where(ListSource.code.in_(codes))
        _ = s.execute(query)


def _check_staleness(tenant_id: UUID) -> list[str]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return check_staleness(s, InAppNotifier(), now=clock.now())


def _remind(tenant_id: UUID) -> bool:
    with tenant_session(system_ctx(tenant_id)) as s:
        return remind_lpb_missing(s, InAppNotifier(), now=clock.now())


def test_mandatory_and_recommended_lists_become_subscriptions(
    itf_tenant: CreatedTenant,
):
    report = _reconcile(itf_tenant.tenant_id)
    assert report == ReconcileReport(
        added=(*RECOMMENDED_ITF, "UN_CONSOLIDATED"), changed=(), removed=()
    )
    assert _subscriptions(itf_tenant.tenant_id) == {
        "UN_CONSOLIDATED": (ListRequirement.MANDATORY, True, 0),
        **dict.fromkeys(RECOMMENDED_ITF, (ListRequirement.RECOMMENDED, True, 0)),
    }
    # Reconciling again changes nothing.
    assert _reconcile(itf_tenant.tenant_id) == ReconcileReport((), (), ())
    with tenant_session(system_ctx(itf_tenant.tenant_id)) as s:
        assert set(s.scalars(select(TenantListSubscription.changed_by))) == {"system"}


def test_a_disabled_recommended_list_stays_disabled_on_reconcile(
    itf_tenant: CreatedTenant,
):
    _ = _reconcile(itf_tenant.tenant_id)
    _set_enabled(itf_tenant.tenant_id, "OFAC_SDN", enabled=False)
    assert _reconcile(itf_tenant.tenant_id) == ReconcileReport((), (), ())
    assert _subscriptions(itf_tenant.tenant_id)["OFAC_SDN"] == (
        ListRequirement.RECOMMENDED,
        False,
        0,
    )


def test_a_list_leaving_the_policy_is_removed(
    itf_tenant: CreatedTenant, packs: dict[str, UUID]
):
    _ = _reconcile(itf_tenant.tenant_id)
    _ = assign_tenant_wide(itf_tenant, packs[AVI], date(2026, 12, 1))
    report = _reconcile(itf_tenant.tenant_id, date(2026, 12, 1))
    assert report == ReconcileReport(
        added=(), changed=(), removed=("EU_FSF", "UK_FCDO")
    )
    assert set(_subscriptions(itf_tenant.tenant_id)) == {
        "UN_CONSOLIDATED",
        "OFAC_SDN",
        "MX_SAT_69B",
    }


def test_without_a_policy_subscriptions_are_left_alone(
    tenant: CreatedTenant, packs: dict[str, UUID], frozen: FakeClock
):
    assert _reconcile(tenant.tenant_id) is None
    assert _subscriptions(tenant.tenant_id) == {}
    # Rows from an earlier policy stay as they are once no pack is in force.
    _ = assign_tenant_wide(tenant, packs[ITF], date(2026, 1, 1))
    _ = _reconcile(tenant.tenant_id)
    before = _subscriptions(tenant.tenant_id)
    assert _reconcile(tenant.tenant_id, date(2025, 12, 31)) is None
    assert _subscriptions(tenant.tenant_id) == before


def test_the_first_catch_up_is_one_initial_event_for_the_active_version(
    itf_tenant: CreatedTenant, admin_engine: Engine
):
    _ = _reconcile(itf_tenant.tenant_id)
    ids = [make_version(admin_engine, "UN_CONSOLIDATED", seq) for seq in (1, 2, 3)]
    events = _catch_up(itf_tenant.tenant_id)
    assert [e.data for e in events] == [
        {
            "list_code": "UN_CONSOLIDATED",
            "version_id": str(ids[2]),
            "seq": 3,
            "added": 1,
            "changed": 0,
            "removed": 0,
            "initial": True,
        }
    ]
    (event,) = _published(itf_tenant.tenant_id)
    assert (event.type, event.source, event.key) == (
        LISTS_VERSION_PUBLISHED,
        EVENT_SOURCE,
        "UN_CONSOLIDATED",
    )
    assert event.subject == f"list_version/{ids[2]}"
    assert _subscriptions(itf_tenant.tenant_id)["UN_CONSOLIDATED"][2] == 3
    assert _catch_up(itf_tenant.tenant_id) == []


def test_later_versions_are_handed_over_one_event_each_in_order(
    itf_tenant: CreatedTenant, admin_engine: Engine
):
    _ = _reconcile(itf_tenant.tenant_id)
    _ = make_version(admin_engine, "UN_CONSOLIDATED", 3)
    _ = _catch_up(itf_tenant.tenant_id)
    fourth = make_version(admin_engine, "UN_CONSOLIDATED", 4, added=2, changed=1)
    fifth = make_version(admin_engine, "UN_CONSOLIDATED", 5, added=0, removed=3)
    events = _catch_up(itf_tenant.tenant_id)
    assert [
        (e.data["version_id"], e.data["seq"], e.data["initial"]) for e in events
    ] == [(str(fourth), 4, False), (str(fifth), 5, False)]
    assert (events[0].data["added"], events[0].data["changed"]) == (2, 1)
    assert events[1].data["removed"] == 3
    assert [e.data["seq"] for e in _published(itf_tenant.tenant_id)] == [3, 4, 5]
    assert _subscriptions(itf_tenant.tenant_id)["UN_CONSOLIDATED"][2] == 5


def test_a_disabled_subscription_is_not_caught_up(
    itf_tenant: CreatedTenant, admin_engine: Engine
):
    _ = _reconcile(itf_tenant.tenant_id)
    _set_enabled(itf_tenant.tenant_id, "OFAC_SDN", enabled=False)
    _ = make_version(admin_engine, "OFAC_SDN", 1)
    assert _catch_up(itf_tenant.tenant_id) == []
    assert _subscriptions(itf_tenant.tenant_id)["OFAC_SDN"][2] == 0


def test_overlapping_polls_publish_each_version_once(
    itf_tenant: CreatedTenant, admin_engine: Engine
):
    _ = _reconcile(itf_tenant.tenant_id)
    _ = make_version(admin_engine, "UN_CONSOLIDATED", 1)
    _ = _catch_up(itf_tenant.tenant_id)
    for seq in (2, 3):
        _ = make_version(admin_engine, "UN_CONSOLIDATED", seq)
    second: list[list[Event]] = []
    factory = get_sessionmaker()

    def overlapping() -> None:
        with tenant_session(system_ctx(itf_tenant.tenant_id), factory=factory) as s:
            second.append(catch_up(s))

    with tenant_session(system_ctx(itf_tenant.tenant_id), factory=factory) as s:
        first = catch_up(s)
        thread = threading.Thread(target=overlapping)
        thread.start()
        # The second poll waits on the subscription rows the first one holds.
        time.sleep(0.3)
        assert thread.is_alive()
    thread.join(timeout=10)
    assert [e.data["seq"] for e in first] == [2, 3]
    assert second == [[]]
    assert [e.data["seq"] for e in _published(itf_tenant.tenant_id)] == [1, 2, 3]


def test_a_stale_source_notifies_admin_and_mlro_once_per_episode(
    itf_tenant: CreatedTenant, admin_engine: Engine, frozen: FakeClock
):
    _ = _reconcile(itf_tenant.tenant_id)
    _set_success(admin_engine, NOW)
    _set_success(admin_engine, NOW - timedelta(hours=1), "UN_CONSOLIDATED")
    stale_un = {
        "list_code": "UN_CONSOLIDATED",
        "last_success_at": "2026-10-19T17:00:00+00:00",
    }
    assert _check_staleness(itf_tenant.tenant_id) == ["UN_CONSOLIDATED"]
    assert _check_staleness(itf_tenant.tenant_id) == []
    assert _notices(itf_tenant.tenant_id, STALE_TEMPLATE) == [
        ("ADMIN", stale_un),
        ("MLRO", stale_un),
    ]
    # Fresh again: the episode ends and the flag is cleared.
    _set_success(admin_engine, NOW, "UN_CONSOLIDATED")
    assert _check_staleness(itf_tenant.tenant_id) == []
    with tenant_session(system_ctx(itf_tenant.tenant_id)) as s:
        assert set(s.scalars(select(TenantListSubscription.stale_notified_at))) == {
            None
        }
    # Never fetched: stale again, a new episode.
    _set_success(admin_engine, None, "UN_CONSOLIDATED")
    _ = frozen.advance(timedelta(minutes=1))
    assert _check_staleness(itf_tenant.tenant_id) == ["UN_CONSOLIDATED"]
    never = {"list_code": "UN_CONSOLIDATED", "last_success_at": None}
    assert _notices(itf_tenant.tenant_id, STALE_TEMPLATE)[2:] == [
        ("ADMIN", never),
        ("MLRO", never),
    ]


def test_a_disabled_subscription_is_never_reported_stale(
    itf_tenant: CreatedTenant, admin_engine: Engine
):
    _ = _reconcile(itf_tenant.tenant_id)
    _set_success(admin_engine, NOW)
    _set_success(admin_engine, None, "OFAC_SDN")
    _set_enabled(itf_tenant.tenant_id, "OFAC_SDN", enabled=False)
    assert _check_staleness(itf_tenant.tenant_id) == []


def test_a_fresh_source_is_not_stale_at_twice_its_interval():
    source = ListSource(
        code="UN_CONSOLIDATED",
        list_kind=ListKind.SANCTION,
        title="UN",
        jurisdiction="UN",
        format=SourceFormat.UN_XML,
        url="https://example.org/un.xml",
        poll_interval=timedelta(minutes=15),
        last_success_at=NOW - timedelta(minutes=30),
    )
    assert not is_stale(source, now=NOW)
    assert is_stale(source, now=NOW + timedelta(seconds=1))
    source.last_success_at = None
    assert is_stale(source, now=NOW)


def test_a_missing_lpb_is_reminded_once_a_day(
    itf_tenant: CreatedTenant, frozen: FakeClock
):
    assert _remind(itf_tenant.tenant_id) is True
    _ = frozen.advance(timedelta(hours=5))  # 17:00 in Mexico City, same day
    assert _remind(itf_tenant.tenant_id) is False
    assert _notices(itf_tenant.tenant_id, LPB_MISSING_TEMPLATE) == [
        ("ADMIN", {}),
        ("MLRO", {}),
    ]
    _ = frozen.advance(timedelta(hours=7, minutes=1))  # 00:01 the next day
    assert _remind(itf_tenant.tenant_id) is True
    assert len(_notices(itf_tenant.tenant_id, LPB_MISSING_TEMPLATE)) == 4


def test_no_lpb_reminder_without_a_policy(
    tenant: CreatedTenant, packs: dict[str, UUID], frozen: FakeClock
):
    assert _remind(tenant.tenant_id) is False
    assert _notices(tenant.tenant_id, LPB_MISSING_TEMPLATE) == []


def test_another_tenant_is_never_caught_up_by_this_one(
    itf_tenant: CreatedTenant, packs: dict[str, UUID], admin_engine: Engine
):
    other = new_tenant("other", "admin@other.mx")
    _ = assign_tenant_wide(other, packs[ITF], date(2026, 1, 1))
    for tenant_id in (itf_tenant.tenant_id, other.tenant_id):
        _ = _reconcile(tenant_id)
    _ = make_version(admin_engine, "UN_CONSOLIDATED", 1)
    events = _catch_up(itf_tenant.tenant_id)
    assert {e.tenant_id for e in events} == {itf_tenant.tenant_id}
    assert _published(other.tenant_id) == []
    assert _subscriptions(other.tenant_id)["UN_CONSOLIDATED"][2] == 0
