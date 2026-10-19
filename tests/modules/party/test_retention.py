"""The retention sweep: which parties it purges, what a purge removes, and its job.

``clock.now`` is frozen at ``T0`` (2036-10-20 03:00 UTC), so "today" is 2036-10-20
and a party whose ``retention_until`` is 2036-10-19 is due. The customer
relationships are written with the model directly: ``open_relationship`` would
answer ``POLICY_NOT_ASSIGNED``, since this module assigns no pack.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select

from tests.helpers import (
    audit_events,
    everything_stored,
    leaked,
    make_tenant,
    staff_session,
    system_ctx,
)
from tests.modules.party.conftest import ACME, ANA, CARLOS, of_type
from tests.platform.jobs_support import enqueue, fetch_job, fetch_schedule
from xolotl.app import build_worker, wire_platform
from xolotl.modules.party import documents, service
from xolotl.modules.party.enums import (
    AddressType,
    ContactKind,
    CustomerRelationshipStatus,
    DocumentType,
    IdentifierType,
    PartyStatus,
    RelationshipType,
)
from xolotl.modules.party.models import (
    CustomerRelationship,
    Document,
    Organization,
    Party,
    PartyAddress,
    PartyContact,
    PartyIdentifier,
    PartyRelationship,
    Person,
)
from xolotl.modules.party.retention import (
    RETENTION_SWEEP_KIND,
    RetentionSweeper,
    register_party,
)
from xolotl.modules.party.schemas import (
    AddressInput,
    AddressLines,
    ContactInput,
    PartyCreateRequest,
    PartyRelationshipInput,
)
from xolotl.modules.tenants import service as tenants_service
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import JobStatus
from xolotl.platform.jobs import JobRunner, PostgresJobQueue, job_handler
from xolotl.platform.storage.local import LocalFsObjectStore

T0 = datetime(2036, 10, 20, 3, 0, tzinfo=UTC)
TODAY = T0.date()
DUE = date(2036, 10, 19)


pytestmark = pytest.mark.usefixtures("frozen")


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "now", lambda: T0)


@dataclass(frozen=True)
class Scene:
    ana: UUID
    carlos: UUID
    acme: UUID
    today_party: UUID
    never: UUID
    document_file: Path


def _customer(
    s,
    body: PartyCreateRequest,
    *,
    retention_until: date | None,
    legal_hold: bool = False,
    ref: str,
) -> Party:
    party = service.create_party(s, body)
    s.add(
        CustomerRelationship(
            tenant_id=party.tenant_id,
            party_id=party.id,
            external_ref=ref,
            status=CustomerRelationshipStatus.EXITED,
        )
    )
    party.retention_until = retention_until
    party.legal_hold = legal_hold
    s.flush()
    return party


def _scene(tenant: CreatedTenant, store: LocalFsObjectStore, tmp_path: Path) -> Scene:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        ana = _customer(s, ANA, retention_until=DUE, ref="ana")
        carlos = _customer(s, CARLOS, retention_until=DUE, legal_hold=True, ref="c")
        acme = _customer(s, ACME, retention_until=DUE, ref="acme-1")
        s.add(
            CustomerRelationship(
                tenant_id=acme.tenant_id,
                party_id=acme.id,
                external_ref="acme-2",
                status=CustomerRelationshipStatus.ACTIVE,
            )
        )
        today_party = _customer(
            s,
            PartyCreateRequest.model_validate({
                "party_type": "PERSON",
                "person": {"given_names": "Hoy", "first_surname": "Mismo"},
            }),
            retention_until=TODAY,
            ref="today",
        )
        never = service.create_party(
            s,
            PartyCreateRequest.model_validate({
                "party_type": "PERSON",
                "person": {"given_names": "Nunca", "first_surname": "Cliente"},
            }),
        )
        _ = service.add_contact(
            s, ana.id, ContactInput(kind=ContactKind.EMAIL, value="ana@acme.mx")
        )
        _ = service.add_address(
            s,
            ana.id,
            AddressInput(
                address_type=AddressType.RESIDENTIAL,
                lines=AddressLines(street="Calle Secreta 12"),
            ),
        )
        _ = service.add_relationship(
            s,
            acme.id,
            PartyRelationshipInput(
                to_party_id=ana.id, rel_type=RelationshipType.DIRECTOR
            ),
        )
        row = documents.upload_document(
            s,
            store,
            party_id=ana.id,
            doc_type=DocumentType.IDENTITY,
            content_type="application/pdf",
            data=b"%PDF-1.4 ana",
        )
        file = tmp_path / "objects" / str(tenant.tenant_id) / row.storage_key
    assert file.exists()
    return Scene(ana.id, carlos.id, acme.id, today_party.id, never.id, file)


def _sweep(tenant: CreatedTenant, store: LocalFsObjectStore, **kw: bool) -> int:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return RetentionSweeper(store).sweep(s, today=TODAY, **kw)


def _count(
    tenant: CreatedTenant,
    model: type[PartyIdentifier | PartyContact | PartyAddress | Document],
    party_id: UUID,
) -> int:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return s.execute(
            select(func.count()).select_from(model).where(model.party_id == party_id)
        ).scalar_one()


def _party(tenant: CreatedTenant, party_id: UUID) -> Party:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return s.get_one(Party, (tenant.tenant_id, party_id))


def _purged_audits(tenant: CreatedTenant) -> list[dict[str, object] | None]:
    return [
        e.detail for e in audit_events(tenant.tenant_id) if e.action == "party.purged"
    ]


def test_the_sweep_purges_due_parties_and_nothing_else(
    engine: Engine,
    tenant: CreatedTenant,
    local_store: LocalFsObjectStore,
    tmp_path: Path,
    admin_engine: Engine,
):
    scene = _scene(tenant, local_store, tmp_path)
    assert _sweep(tenant, local_store) == 1

    assert _party(tenant, scene.ana).status is PartyStatus.PURGED
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        person = s.get_one(Person, (tenant.tenant_id, scene.ana))
        assert (
            person.given_names,
            person.first_surname,
            person.second_surname,
            person.birth_date,
            person.clave_elector,
        ) == (None, None, None, None, None)
        edges = s.scalar(
            select(func.count())
            .select_from(PartyRelationship)
            .where(
                (PartyRelationship.from_party_id == scene.ana)
                | (PartyRelationship.to_party_id == scene.ana)
            )
        )
        assert edges == 0
        found = s.scalars(
            service.parties_query(s, id_type=IdentifierType.RFC, value="PEGA760510XY0")
        ).all()
        assert found == []
        acme = s.get_one(Organization, (tenant.tenant_id, scene.acme))
        assert acme.legal_name is not None
    for model in (PartyIdentifier, PartyContact, PartyAddress, Document):
        assert _count(tenant, model, scene.ana) == 0
    assert not scene.document_file.exists()

    for other in (scene.carlos, scene.acme, scene.today_party, scene.never):
        assert _party(tenant, other).status is PartyStatus.ACTIVE
    assert _count(tenant, PartyIdentifier, scene.carlos) == 2
    assert _count(tenant, PartyIdentifier, scene.acme) == 1

    assert _purged_audits(tenant) == [{"documents": 1, "identifiers": 2}]
    (purged,) = of_type(tenant.tenant_id, "party.purged.v1")
    assert purged.data == {"party_id": str(scene.ana)}
    assert leaked(everything_stored(admin_engine), "Ana Mar\xeda".encode()) == []


def test_a_second_run_changes_nothing(
    engine: Engine,
    tenant: CreatedTenant,
    local_store: LocalFsObjectStore,
    tmp_path: Path,
):
    _ = _scene(tenant, local_store, tmp_path)
    assert _sweep(tenant, local_store) == 1
    assert _sweep(tenant, local_store) == 0
    assert len(_purged_audits(tenant)) == 1
    assert len(of_type(tenant.tenant_id, "party.purged.v1")) == 1


def test_a_dry_run_counts_without_purging(
    engine: Engine,
    tenant: CreatedTenant,
    local_store: LocalFsObjectStore,
    tmp_path: Path,
):
    scene = _scene(tenant, local_store, tmp_path)
    assert _sweep(tenant, local_store, dry_run=True) == 1
    assert _party(tenant, scene.ana).status is PartyStatus.ACTIVE
    assert scene.document_file.exists()
    assert _purged_audits(tenant) == []


def test_a_failed_object_delete_is_logged_and_the_purge_stands(
    engine: Engine,
    tenant: CreatedTenant,
    local_store: LocalFsObjectStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    scene = _scene(tenant, local_store, tmp_path)

    def refuse(*_args: object) -> None:
        msg = "disk gone"
        raise OSError(msg)

    monkeypatch.setattr(local_store, "delete", refuse)
    assert _sweep(tenant, local_store) == 1
    assert _party(tenant, scene.ana).status is PartyStatus.PURGED
    (warning,) = [
        r for r in caplog.records if r.message == "purged document not deleted"
    ]
    assert warning.levelname == "WARNING"
    assert str(scene.ana) in warning.__dict__["key"]


def test_the_job_runs_through_the_registered_handler(
    engine: Engine,
    tenant: CreatedTenant,
    local_store: LocalFsObjectStore,
    tmp_path: Path,
):
    scene = _scene(tenant, local_store, tmp_path)
    register_party(object_store=local_store)
    queue = PostgresJobQueue(get_sessionmaker())
    job_id = enqueue(queue, tenant.tenant_id, kind=RETENTION_SWEEP_KIND)
    runner = JobRunner(
        queue, get_sessionmaker(), worker_id="w-1", kinds=[RETENTION_SWEEP_KIND]
    )
    assert runner.run_once() == 1
    assert fetch_job(tenant.tenant_id, job_id).status is JobStatus.DONE
    assert _party(tenant, scene.ana).status is PartyStatus.PURGED


def test_the_platform_registers_the_handler_and_schedules_the_sweep(engine: Engine):
    _ = wire_platform(get_sessionmaker(), get_settings())
    _ = job_handler(RETENTION_SWEEP_KIND)
    legacy = make_tenant("legacy")
    _ = build_worker().run(max_ticks=0)
    schedule = fetch_schedule(legacy, "party.retention_sweep")
    assert (schedule.cron, schedule.tz, schedule.kind, schedule.enabled) == (
        "0 3 * * *",
        "UTC",
        "party.retention_sweep",
        True,
    )


def test_the_schedule_names_the_handlers_kind():
    assert tenants_service.RETENTION_SWEEP_SCHEDULE == RETENTION_SWEEP_KIND
