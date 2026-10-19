"""In-app notifications: the ``Notifier`` port's adapter and who receives what.

``InAppNotifier.notify`` writes one ``notification`` row in the caller's transaction,
for one staff user or for every holder of a role, and logs that it did without the
data. ``addressed_to`` and ``mark_read`` are what the notifications routes read and
write through.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, select, text

from tests.helpers import make_tenant, platform_ctx, system_ctx
from tests.platform.jobs_support import eventually
from xolotl.platform import clock
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.errors import ContextMissingError, NotFoundError
from xolotl.platform.events.models import Notification
from xolotl.platform.events.notifier import (
    InAppNotifier,
    Notifier,
    addressed_to,
    mark_read,
)
from xolotl.platform.ids import uuid7

T0 = datetime(2026, 10, 19, 9, 30, tzinfo=UTC)
ANA = uuid7()  # a staff user id; notifications carry no foreign key to staff_user
BETO = uuid7()


@pytest.fixture
def tenant_id(engine: Engine) -> UUID:
    return make_tenant("alpha")


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> datetime:
    monkeypatch.setattr(clock, "now", lambda: T0)
    return T0


def notify(
    tenant_id: UUID,
    *,
    recipient_user_id: UUID | None = None,
    recipient_role: Role | None = None,
) -> UUID:
    with tenant_session(system_ctx(tenant_id)) as s:
        return InAppNotifier().notify(
            s,
            template="units.fetch_failed",
            data={"unit": "UDI"},
            recipient_user_id=recipient_user_id,
            recipient_role=recipient_role,
        )


def rows(tenant_id: UUID) -> list[Notification]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return list(s.scalars(select(Notification).order_by(Notification.id)))


def test_a_notice_for_a_user_is_one_row_stamped_by_the_clock(
    tenant_id: UUID, frozen: datetime
):
    notification_id = notify(tenant_id, recipient_user_id=ANA)
    (row,) = rows(tenant_id)
    assert (row.id, row.tenant_id) == (notification_id, tenant_id)
    assert (row.recipient_user_id, row.recipient_role) == (ANA, None)
    assert (row.template, row.data) == ("units.fetch_failed", {"unit": "UDI"})
    assert (row.created_at, row.read_at) == (frozen, None)


def test_a_notice_for_a_role_names_the_role(tenant_id: UUID):
    _ = notify(tenant_id, recipient_role=Role.MLRO)
    (row,) = rows(tenant_id)
    assert (row.recipient_user_id, row.recipient_role) == (None, "MLRO")


def test_the_notice_belongs_to_the_callers_transaction(tenant_id: UUID):
    class AbortError(Exception):
        pass

    with (
        pytest.raises(AbortError),
        tenant_session(system_ctx(tenant_id)) as s,
    ):
        _ = InAppNotifier().notify(s, template="t", data={}, recipient_user_id=ANA)
        raise AbortError
    assert rows(tenant_id) == []


@pytest.mark.parametrize(
    ("user", "role"), [(None, None), (ANA, Role.ADMIN)], ids=["nobody", "both"]
)
def test_a_notice_has_exactly_one_recipient(
    tenant_id: UUID, user: UUID | None, role: Role | None
):
    with pytest.raises(ValueError, match="exactly one recipient"):
        _ = notify(tenant_id, recipient_user_id=user, recipient_role=role)
    assert rows(tenant_id) == []


def test_notify_needs_a_session_bound_to_a_tenant(engine: Engine):
    with (
        tenant_session(platform_ctx()) as s,
        pytest.raises(ContextMissingError),
    ):
        _ = InAppNotifier().notify(s, template="t", data={}, recipient_user_id=ANA)


def test_the_log_line_names_the_notice_but_never_its_data(
    tenant_id: UUID, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger="xolotl.platform.events.notifier")
    with tenant_session(system_ctx(tenant_id)) as s:
        notification_id = InAppNotifier().notify(
            s,
            template="case.assigned",
            data={"case_id": "CASE-NEVER-LOGGED", "note": "ana.perez@example.mx"},
            recipient_role=Role.ANALYST,
        )
    (record,) = [r for r in caplog.records if r.name.endswith("notifier")]
    assert record.getMessage() == "notification created"
    fields = vars(record)
    assert fields["notification_id"] == str(notification_id)
    assert fields["template"] == "case.assigned"
    assert fields["recipient_role"] == "ANALYST"
    assert "ana.perez" not in repr(fields)
    assert "CASE-NEVER-LOGGED" not in repr(fields)  # no hex id can contain it


def test_the_inapp_notifier_is_a_notifier():
    notifier: Notifier = InAppNotifier()
    assert callable(notifier.notify)


def visible(tenant_id: UUID, user_id: UUID, roles: set[Role]) -> list[UUID]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return list(
            s.scalars(
                select(Notification.id)
                .where(addressed_to(user_id, roles))
                .order_by(Notification.id)
            )
        )


def test_a_user_receives_their_own_notices_and_their_roles_ones(tenant_id: UUID):
    for_ana = notify(tenant_id, recipient_user_id=ANA)
    for_beto = notify(tenant_id, recipient_user_id=BETO)
    for_mlro = notify(tenant_id, recipient_role=Role.MLRO)
    for_admin = notify(tenant_id, recipient_role=Role.ADMIN)
    assert visible(tenant_id, ANA, {Role.MLRO}) == [for_ana, for_mlro]
    assert visible(tenant_id, BETO, {Role.ADMIN, Role.MLRO}) == [
        for_beto,
        for_mlro,
        for_admin,
    ]
    assert visible(tenant_id, ANA, set()) == [for_ana]


def test_marking_read_sets_read_at_once_and_keeps_the_first_time(
    tenant_id: UUID, monkeypatch: pytest.MonkeyPatch
):
    notification_id = notify(tenant_id, recipient_user_id=ANA)
    monkeypatch.setattr(clock, "now", lambda: T0)
    with tenant_session(system_ctx(tenant_id)) as s:
        assert mark_read(s, notification_id, user_id=ANA, roles=()).read_at == T0
    monkeypatch.setattr(clock, "now", lambda: T0 + timedelta(hours=1))
    with tenant_session(system_ctx(tenant_id)) as s:
        assert mark_read(s, notification_id, user_id=ANA, roles=()).read_at == T0
    (row,) = rows(tenant_id)
    assert row.read_at == T0


def test_a_role_notice_read_by_one_holder_is_read_for_all_of_them(tenant_id: UUID):
    # One shared notice per role, as the table has one read_at.
    notification_id = notify(tenant_id, recipient_role=Role.MLRO)
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = mark_read(s, notification_id, user_id=ANA, roles={Role.MLRO})
    (row,) = rows(tenant_id)
    assert row.read_at is not None


@pytest.mark.parametrize(
    ("user_id", "roles"),
    [(BETO, set()), (ANA, {Role.ADMIN})],
    ids=["someone-elses", "another-roles"],
)
def test_only_a_recipient_can_mark_a_notice_read(
    tenant_id: UUID, user_id: UUID, roles: set[Role]
):
    for_ana = notify(tenant_id, recipient_user_id=ANA)
    for_mlro = notify(tenant_id, recipient_role=Role.MLRO)
    target = for_ana if user_id == BETO else for_mlro
    with (
        tenant_session(system_ctx(tenant_id)) as s,
        pytest.raises(NotFoundError),
    ):
        _ = mark_read(s, target, user_id=user_id, roles=roles)
    assert all(row.read_at is None for row in rows(tenant_id))


def test_an_unknown_notice_is_not_found(tenant_id: UUID):
    with (
        tenant_session(system_ctx(tenant_id)) as s,
        pytest.raises(NotFoundError),
    ):
        _ = mark_read(s, uuid7(), user_id=ANA, roles=())


def lock_waiters(admin: Engine) -> int:
    with admin.connect() as conn:
        return conn.execute(
            text("""
                SELECT count(*) FROM pg_stat_activity
                WHERE wait_event_type = 'Lock' AND datname = current_database()
            """)
        ).scalar_one()


def test_two_marks_at_once_agree_on_the_first_read_at(
    tenant_id: UUID, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    notification_id = notify(tenant_id, recipient_user_id=ANA)
    first, second = T0, T0 + timedelta(minutes=1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with tenant_session(system_ctx(tenant_id)) as s:
            monkeypatch.setattr(clock, "now", lambda: first)
            _ = mark_read(s, notification_id, user_id=ANA, roles=())
            # Not committed yet: a second mark has to wait for this one.
            monkeypatch.setattr(clock, "now", lambda: second)

            def mark_again() -> datetime | None:
                with tenant_session(system_ctx(tenant_id)) as other:
                    row = mark_read(other, notification_id, user_id=ANA, roles=())
                    return row.read_at

            again = pool.submit(mark_again)
            assert eventually(lambda: lock_waiters(admin_engine) == 1)
        assert again.result(timeout=30) == first
    (row,) = rows(tenant_id)
    assert row.read_at == first
