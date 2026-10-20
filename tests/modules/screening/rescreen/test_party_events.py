"""Party events re-index and re-screen a party, once per event (SP-03 spec §10.1)."""

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from tests.helpers import staff_session, system_ctx
from tests.modules.screening.catalog.support import load_shared, seed_sources
from tests.modules.screening.review.support import assign_itf
from xolotl.modules.party import service
from xolotl.modules.party.enums import IdentifierType, PartyType
from xolotl.modules.party.events import IDENTIFIER_ADDED, PARTY_CREATED, PARTY_UPDATED
from xolotl.modules.party.retention import purge_party
from xolotl.modules.party.schemas import (
    IdentifierInput,
    PartyCreateRequest,
    PartyUpdateRequest,
    PersonInput,
    PersonUpdate,
)
from xolotl.modules.screening.composition import register_screening
from xolotl.modules.screening.engine.enums import RunMode, TriggerType
from xolotl.modules.screening.engine.holder import set_index_holder
from xolotl.modules.screening.engine.models import PartyNameIndex, ScreeningRun
from xolotl.modules.screening.engine.screen import Trigger, screen
from xolotl.modules.screening.engine.subjects import subject_for_party
from xolotl.modules.screening.lists import (
    EntityType,
    EntryName,
    ListEntry,
    NameQuality,
    PartialDate,
)
from xolotl.modules.screening.rescreen.party_events import (
    PARTY_EVENT_MODES,
    SCREEN_PARTY_KIND,
    SCREENING_CONSUMER,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import JobStatus, Role
from xolotl.platform.events import OutboxEventBus
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.events.registry import consumers, reset_subscribers
from xolotl.platform.jobs import JobRunner, PostgresJobQueue
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.storage.local import LocalFsObjectStore

OFAC = "OFAC_SDN"
JUAN = ListEntry(
    list_code=OFAC,
    entry_key="7001",
    entity_type=EntityType.PERSON,
    primary_name=EntryName("JUAN CARLOS PEREZ GOMEZ", NameQuality.PRIMARY),
    birth_dates=(PartialDate(1970, 5, 10),),
    source_ref="sdn 7001",
)
PERSON = PartyCreateRequest(
    party_type=PartyType.PERSON,
    person=PersonInput(
        given_names="Juan Carlos",
        first_surname="Pérez",
        second_surname="Gómez",
        birth_date=date(1970, 5, 10),
    ),
)


@dataclass(frozen=True, slots=True)
class Wired:
    bus: OutboxEventBus
    queue: PostgresJobQueue
    runner: JobRunner


@pytest.fixture
def wired(
    tenant: CreatedTenant, admin_engine: Engine, tmp_path: Path
) -> Iterator[Wired]:
    seed_sources(admin_engine)
    _ = load_shared(OFAC, [JUAN], store=LocalFsObjectStore(tmp_path / "objects"))
    assign_itf(tenant, admin_engine)
    factory = get_sessionmaker()
    queue = PostgresJobQueue(factory)
    reset_subscribers()
    _ = register_screening(
        session_factory=factory, settings=get_settings(), queue=queue
    )
    yield Wired(
        OutboxEventBus(factory), queue, JobRunner(queue, factory, worker_id="w-test")
    )
    reset_subscribers()
    set_index_holder(None)


@contextmanager
def _staff(tenant: CreatedTenant) -> Generator[Session]:
    with staff_session(tenant.tenant_id, tenant.admin_user_id, roles=(Role.MLRO,)) as s:
        yield s


def _dispatch(tenant: CreatedTenant, wired: Wired) -> None:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = s.execute(
            text("UPDATE outbox SET created_at = created_at - interval '1 minute'")
        )
    while wired.bus.dispatch_pending():
        pass


def _jobs(tenant: CreatedTenant) -> list[JobRow]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return list(
            s.scalars(
                select(JobRow)
                .where(JobRow.kind == SCREEN_PARTY_KIND)
                .order_by(JobRow.created_at, JobRow.id)
            )
        )


def _runs(tenant: CreatedTenant) -> list[ScreeningRun]:
    with _staff(tenant) as s:
        return list(
            s.scalars(
                select(ScreeningRun).order_by(ScreeningRun.created_at, ScreeningRun.id)
            )
        )


def _event_id(tenant: CreatedTenant, event_type: str) -> str:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        ids = s.scalars(
            select(OutboxRow.id)
            .where(OutboxRow.type == event_type)
            .order_by(OutboxRow.seq.desc())
        )
        return str(ids.first())


def _create(tenant: CreatedTenant, wired: Wired) -> UUID:
    with _staff(tenant) as s:
        party_id = service.create_party(s, PERSON).id
    _dispatch(tenant, wired)
    return party_id


def test_the_screening_consumer_is_registered_for_the_party_events(wired: Wired):
    assert SCREENING_CONSUMER in consumers()
    assert dict(PARTY_EVENT_MODES) == {
        PARTY_CREATED: RunMode.NEW_SUBJECT,
        PARTY_UPDATED: RunMode.DATA_CHANGE,
        IDENTIFIER_ADDED: RunMode.DATA_CHANGE,
    }


def test_creating_a_party_indexes_its_names_and_screens_it(
    tenant: CreatedTenant, wired: Wired
):
    party_id = _create(tenant, wired)

    with _staff(tenant) as s:
        names = list(
            s.scalars(
                select(PartyNameIndex.norm_name).where(
                    PartyNameIndex.party_id == party_id
                )
            )
        )
    assert names == ["juan carlos perez gomez"]
    event_id = _event_id(tenant, PARTY_CREATED)
    (job,) = _jobs(tenant)
    assert job.key == f"screen_party:{party_id}:{event_id}"
    assert job.payload["party_id"] == str(party_id)
    assert job.payload["mode"] == "NEW_SUBJECT"
    assert _runs(tenant) == []

    assert wired.runner.run_once() == 1

    (run,) = _runs(tenant)
    assert run.mode is RunMode.NEW_SUBJECT
    assert run.trigger_type is TriggerType.EVENT
    assert run.trigger_ref == event_id
    assert run.hit_count == 1
    assert [j.status for j in _jobs(tenant)] == [JobStatus.DONE]


def test_an_update_and_an_identifier_rescreen_as_data_change(
    tenant: CreatedTenant, wired: Wired
):
    party_id = _create(tenant, wired)
    _ = wired.runner.run_once()
    with _staff(tenant) as s:
        _ = service.update_party(
            s,
            party_id,
            PartyUpdateRequest(person=PersonUpdate(nationalities=["MX"])),
        )
    _dispatch(tenant, wired)
    _ = wired.runner.run_once()
    with _staff(tenant) as s:
        _ = service.add_identifier(
            s,
            party_id,
            IdentifierInput(id_type=IdentifierType.PASSPORT, value="G12345678"),
        )
    _dispatch(tenant, wired)
    _ = wired.runner.run_once()

    runs = _runs(tenant)
    assert [r.mode for r in runs] == [
        RunMode.NEW_SUBJECT,
        RunMode.DATA_CHANGE,
        RunMode.DATA_CHANGE,
    ]
    assert [r.trigger_ref for r in runs[1:]] == [
        _event_id(tenant, PARTY_UPDATED),
        _event_id(tenant, IDENTIFIER_ADDED),
    ]
    assert len({r.query_fingerprint for r in runs}) == 3


def test_redelivery_enqueues_no_second_job(tenant: CreatedTenant, wired: Wired):
    _ = _create(tenant, wired)

    replayed = wired.bus.replay(
        since=clock.now() - timedelta(hours=1),
        types=[PARTY_CREATED],
        consumer=SCREENING_CONSUMER,
        tenant_id=tenant.tenant_id,
    )

    assert replayed == 1
    assert len(_jobs(tenant)) == 1


def test_a_job_skips_when_a_later_run_already_covered_the_event(
    tenant: CreatedTenant, wired: Wired
):
    party_id = _create(tenant, wired)
    with _staff(tenant) as s:
        subject = subject_for_party(s, party_id, for_update=True)
        checked = screen(
            s, subject, mode=RunMode.AD_HOC, trigger=Trigger(TriggerType.CONSOLE)
        ).run

    assert wired.runner.run_once() == 1

    assert [r.id for r in _runs(tenant)] == [checked.id]
    assert [j.status for j in _jobs(tenant)] == [JobStatus.DONE]


def test_a_purged_party_is_not_screened(tenant: CreatedTenant, wired: Wired):
    party_id = _create(tenant, wired)
    with _staff(tenant) as s:
        _ = purge_party(s, service.get_party(s, party_id))

    assert wired.runner.run_once() == 1

    assert _runs(tenant) == []
    assert [j.status for j in _jobs(tenant)] == [JobStatus.DONE]
