"""Counterparty subjects, the name index and the status cache (SP-04 spec §11.2).

A counterparty is a screening subject of its own kind whose row is its input; its
row caches the subject's status after runs and after dispositions alike (plan 4c
Deviation 4), and every change publishes one staff-only event.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError

from tests.helpers import everything_stored, leaked, staff_session, system_ctx
from tests.modules.screening.counterparty.conftest import (
    Screened,
    job_session,
    resolve_one,
    row_of,
)
from tests.modules.screening.review.support import ANA, add_staff
from tests.platform.jobs_support import FakeClock
from tests.platform.webhooks_support import Stack, deliveries, make_endpoint
from xolotl.modules.party import service
from xolotl.modules.screening.composition import register_screening
from xolotl.modules.screening.counterparty.models import CounterpartyNameIndex
from xolotl.modules.screening.counterparty.names import index_counterparty_name
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyInput,
    CounterpartyKind,
    CounterpartyScreeningStatus,
)
from xolotl.modules.screening.counterparty.subjects import subject_for_counterparty
from xolotl.modules.screening.engine.enums import (
    Decision,
    RunMode,
    SubjectKind,
    TriggerType,
)
from xolotl.modules.screening.engine.holder import set_index_holder
from xolotl.modules.screening.engine.models import ScreeningMatch, ScreeningSubject
from xolotl.modules.screening.engine.screen import Trigger, screen
from xolotl.modules.screening.engine.subjects import (
    refresh_subject_status,
    subject_for_party,
)
from xolotl.modules.screening.events import (
    COUNTERPARTY_STATUS_CHANGED,
    RUN_COMPLETED,
    STATUS_CHANGED,
)
from xolotl.modules.screening.matching import MATCHER_VERSION
from xolotl.modules.screening.review.dispositions import disposition_match
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.config import get_settings
from xolotl.platform.crypto.encrypted import encrypt_json
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.events import Event
from xolotl.platform.events.event import event_from_row
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.events.registry import reset_subscribers

ALARCON = CounterpartyInput(
    kind=CounterpartyKind.PERSON, name="Rodrigo Alarcón Palomarez", country="MX"
)
FERNANDA = CounterpartyInput(kind=CounterpartyKind.PERSON, name="Fernanda López Ruiz")
RATIONALE = "Coincide con el listado de OFAC"
INSERT_SUBJECT = """
INSERT INTO screening_subject (tenant_id, id, kind, party_id, counterparty_id, status)
VALUES (:t, gen_random_uuid(), 'COUNTERPARTY', :p, :c, 'CLEAR')
"""


def _status_events(tenant: CreatedTenant) -> list[Event]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        rows = s.scalars(
            select(OutboxRow)
            .where(OutboxRow.type == COUNTERPARTY_STATUS_CHANGED)
            .order_by(OutboxRow.seq)
        ).all()
        return [event_from_row(row) for row in rows]


def test_a_counterparty_subject_is_created_once_and_linked_to_its_row(
    tenant: CreatedTenant,
):
    ref = resolve_one(tenant, ALARCON)

    with job_session(tenant) as s:
        first = subject_for_counterparty(s, ref.id)
        second = subject_for_counterparty(s, ref.id, for_update=True)
        assert (first.id, first.kind, first.input) == (
            second.id,
            SubjectKind.COUNTERPARTY,
            None,
        )
        subject_id = first.id
        count = len(s.scalars(select(ScreeningSubject)).all())

    assert count == 1
    assert row_of(tenant, ref.id).screening_subject_id == subject_id


def test_the_kind_constraint_refuses_a_party_id_or_an_input_on_a_counterparty_subject(
    tenant: CreatedTenant,
):
    ref = resolve_one(tenant, ALARCON)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party_id = service.create_party(s, ANA).id

    with (
        pytest.raises(IntegrityError, match="kind_input"),
        job_session(tenant) as s,
    ):
        _ = s.execute(
            text(INSERT_SUBJECT),
            {"t": tenant.tenant_id, "p": party_id, "c": ref.id},
        )
    with (
        pytest.raises(IntegrityError, match="kind_input"),
        job_session(tenant) as s,
    ):
        s.add(
            ScreeningSubject(
                tenant_id=tenant.tenant_id,
                kind=SubjectKind.COUNTERPARTY,
                counterparty_id=ref.id,
                input=encrypt_json(
                    {"type": "PERSON"}, tenant_id=tenant.tenant_id, session=s
                ),
            )
        )
        s.flush()


def test_the_name_index_row_holds_tokens_and_keys_and_no_name_as_written(
    tenant: CreatedTenant, admin_engine: Engine
):
    ref = resolve_one(tenant, ALARCON)

    with job_session(tenant) as s:
        row = s.scalars(
            select(CounterpartyNameIndex).where(
                CounterpartyNameIndex.counterparty_id == ref.id
            )
        ).one()
        assert (row.norm_name, row.tokens, row.matcher_version) == (
            "rodrigo alarcon palomarez",
            ["rodrigo", "alarcon", "palomarez"],
            MATCHER_VERSION,
        )
        assert row.phonetic_keys
        assert row.phonetic_keys == sorted(row.phonetic_keys)
    assert leaked(everything_stored(admin_engine), "Alarcón".encode()) == []


def test_a_name_less_counterparty_keeps_no_index_row(tenant: CreatedTenant):
    ref = resolve_one(tenant, CounterpartyInput(account="012180001234567"))

    with job_session(tenant) as s:
        rows = s.scalars(select(CounterpartyNameIndex)).all()
        assert rows == []
        row = row_of(tenant, ref.id)
        assert index_counterparty_name(s, row) is None


def test_a_disposition_updates_the_counterparty_cache_and_publishes_once(
    tenant: CreatedTenant, screened: Screened
):
    ref = resolve_one(tenant, ALARCON)
    assert screened.runner.run_once() == 1
    with job_session(tenant) as s:
        match_id, subject_id = s.execute(
            select(ScreeningMatch.id, ScreeningMatch.subject_id)
        ).one()
    analyst = add_staff(tenant, Role.ANALYST)
    senior = add_staff(tenant, Role.SENIOR_ANALYST)
    for user_id, role, decision in (
        (analyst, Role.ANALYST, Decision.POSSIBLE_TRUE_MATCH),
        (senior, Role.SENIOR_ANALYST, Decision.TRUE_MATCH),
    ):
        with staff_session(tenant.tenant_id, user_id, roles=(role,)) as s:
            _ = disposition_match(s, match_id, decision=decision, rationale=RATIONALE)

    assert row_of(tenant, ref.id).screening_status is (
        CounterpartyScreeningStatus.TRUE_MATCH
    )
    events = _status_events(tenant)
    assert [e.data["status"] for e in events] == ["PENDING_REVIEW", "TRUE_MATCH"]
    confirmed = events[-1]
    assert confirmed.data == {
        "counterparty_id": str(ref.id),
        "subject_id": str(subject_id),
        "status": "TRUE_MATCH",
    }
    assert confirmed.key == str(subject_id)
    assert confirmed.subject == f"counterparty/{ref.id}"

    with job_session(tenant) as s:
        subject = s.get_one(ScreeningSubject, (tenant.tenant_id, subject_id))
        _ = refresh_subject_status(s, subject)
    assert len(_status_events(tenant)) == 2


@pytest.fixture
def webhook_stack(screened: Screened) -> Iterator[Stack]:
    reset_subscribers()
    stack = Stack(FakeClock())
    _ = register_screening(
        session_factory=get_sessionmaker(), settings=get_settings(), queue=stack.queue
    )
    yield stack
    reset_subscribers()
    set_index_holder(None)


def _dispatch(tenant: CreatedTenant, stack: Stack) -> None:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = s.execute(
            text("UPDATE outbox SET created_at = created_at - interval '1 minute'")
        )
    while stack.bus.dispatch_pending():
        pass


def _events_of(tenant: CreatedTenant, *types: str) -> list[Event]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        rows = s.scalars(
            select(OutboxRow).where(OutboxRow.type.in_(types)).order_by(OutboxRow.seq)
        ).all()
        return [event_from_row(row) for row in rows]


@pytest.mark.parametrize(
    ("counterparty", "outcome"),
    [(FERNANDA, "CLEAR"), (ALARCON, "PENDING_REVIEW")],
    ids=["clear", "pending-review"],
)
def test_the_status_event_is_staff_only(
    tenant: CreatedTenant,
    webhook_stack: Stack,
    counterparty: CounterpartyInput,
    outcome: str,
):
    webhook_stack.settle(tenant.tenant_id)
    _ = make_endpoint(tenant.tenant_id, event_types=["*"])

    ref = resolve_one(tenant, counterparty)
    assert webhook_stack.run_jobs() == 1
    _dispatch(tenant, webhook_stack)

    # A first-sight screen reaches no client endpoint at all (tipping-off).
    assert row_of(tenant, ref.id).screening_status == outcome
    assert _status_events(tenant)
    assert not _events_of(tenant, RUN_COMPLETED, STATUS_CHANGED)
    assert deliveries(tenant.tenant_id) == []

    # A party's screen still delivers its webhook-safe run event.
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party_id = service.create_party(s, ANA).id
        subject = subject_for_party(s, party_id, for_update=True)
        _ = screen(
            s, subject, mode=RunMode.AD_HOC, trigger=Trigger(TriggerType.CONSOLE, "r")
        )
    _dispatch(tenant, webhook_stack)
    (completed,) = _events_of(tenant, RUN_COMPLETED)
    assert completed.id in {d.event_id for d in deliveries(tenant.tenant_id)}
