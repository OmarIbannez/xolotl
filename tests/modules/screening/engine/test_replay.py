"""Replay of screening runs (SP-03 spec §8.4, NFR-SC-007, Review Focus 2).

A fixture tenant with two parties, an ad hoc subject, the shared UN and OFAC lists, a
private ``MX_LPB`` and a whitelisted entry is screened once per subject; then a new
UN version changes the holder's scoring IDF, and every run still replays exactly.
"""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tests.helpers import staff_session
from tests.modules.screening.catalog.support import (
    load_private,
    load_shared,
    seed_sources,
)
from tests.modules.screening.engine.conftest import UN, FakeMonotonic, un_entry
from tests.modules.screening.review.support import assign_itf
from xolotl.modules.party import service
from xolotl.modules.party.enums import IdentifierType, PartyType
from xolotl.modules.party.schemas import (
    IdentifierInput,
    PartyCreateRequest,
    PersonInput,
)
from xolotl.modules.screening.engine import replay
from xolotl.modules.screening.engine.enums import (
    Decision,
    HitBand,
    RunMode,
    TriggerType,
)
from xolotl.modules.screening.engine.models import (
    ScreeningHit,
    ScreeningRun,
    ScreeningSubject,
)
from xolotl.modules.screening.engine.queries import AdHocSubjectInput
from xolotl.modules.screening.engine.replay import replay_run
from xolotl.modules.screening.engine.screen import RunResult, Trigger, screen
from xolotl.modules.screening.engine.subjects import (
    create_ad_hoc_subject,
    subject_for_party,
)
from xolotl.modules.screening.errors import MatcherVersionMismatchError
from xolotl.modules.screening.lists import (
    EntityType,
    EntryIdentifier,
    EntryName,
    IdType,
    ListEntry,
    NameQuality,
    PartialDate,
)
from xolotl.modules.screening.review.dispositions import disposition_match
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.enums import Role
from xolotl.platform.storage.local import LocalFsObjectStore

OFAC = "OFAC_SDN"
LPB = "MX_LPB"
BIRTH = date(1970, 5, 10)
JUAN = ListEntry(
    list_code=OFAC,
    entry_key="7001",
    entity_type=EntityType.PERSON,
    primary_name=EntryName("JUAN CARLOS PEREZ GOMEZ", NameQuality.PRIMARY),
    birth_dates=(PartialDate(1970, 5, 10),),
    identifiers=(EntryIdentifier(IdType.PASSPORT, "X0000001", "X0000001"),),
    source_ref="sdn 7001",
)
OFAC_OTHERS = (
    ListEntry(
        list_code=OFAC,
        entry_key="7002",
        entity_type=EntityType.PERSON,
        primary_name=EntryName("CARLOS GOMEZ RUIZ", NameQuality.PRIMARY),
        source_ref="sdn 7002",
    ),
)
BLOCKED = ListEntry(
    list_code=LPB,
    entry_key="LPB-1",
    entity_type=EntityType.PERSON,
    primary_name=EntryName("ABU HAMZA TLALOCAN", NameQuality.PRIMARY),
    source_ref="clave LPB-1",
)
JUAN_PARTY = PartyCreateRequest(
    party_type=PartyType.PERSON,
    person=PersonInput(
        given_names="Juan Carlos",
        first_surname="Pérez",
        second_surname="Gómez",
        birth_date=BIRTH,
    ),
)
ABU_PARTY = PartyCreateRequest(
    party_type=PartyType.PERSON,
    person=PersonInput(given_names="Abu Hamza", first_surname="Tlalocan"),
)
AD_HOC = AdHocSubjectInput(
    type="PERSON",
    names=["Ximena Quetzalli Ocotlan Ruiz"],
    given_names="Ximena Quetzalli",
    first_surname="Ocotlan",
    second_surname="Ruiz",
)
CONSOLE = Trigger(TriggerType.CONSOLE, "req-1")


@contextmanager
def _staff(tenant: CreatedTenant, *roles: Role) -> Generator[Session]:
    with staff_session(
        tenant.tenant_id, tenant.admin_user_id, roles=roles or (Role.MLRO,)
    ) as s:
        yield s


def _check_party(tenant: CreatedTenant, party_id: UUID) -> RunResult:
    with _staff(tenant) as s:
        subject = subject_for_party(s, party_id, for_update=True)
        return screen(s, subject, mode=RunMode.AD_HOC, trigger=CONSOLE)


def _party(tenant: CreatedTenant, request: PartyCreateRequest) -> UUID:
    with _staff(tenant) as s:
        return service.create_party(s, request).id


@dataclass(frozen=True, slots=True)
class Fixture:
    juan: UUID
    runs: tuple[RunResult, ...]


@pytest.fixture
def screened(
    tenant: CreatedTenant,
    admin_engine: Engine,
    store: LocalFsObjectStore,
    un_entries: tuple[ListEntry, ...],
    holder,
) -> Fixture:
    seed_sources(admin_engine)
    assign_itf(tenant, admin_engine)
    _ = load_shared(OFAC, (JUAN, *OFAC_OTHERS), store=store)
    _ = load_shared(UN, un_entries, store=store)
    with _staff(tenant) as s:
        _ = load_private(s, store, LPB, [BLOCKED])
    juan = _party(tenant, JUAN_PARTY)
    abu = _party(tenant, ABU_PARTY)
    first = _check_party(tenant, juan)
    with _staff(tenant, Role.ANALYST) as s:
        _ = disposition_match(
            s,
            first.new_matches[0].id,
            decision=Decision.FALSE_POSITIVE,
            rationale="Fecha de nacimiento distinta",
        )
    whitelisted = _check_party(tenant, juan)
    other = _check_party(tenant, abu)
    with _staff(tenant) as s:
        subject = create_ad_hoc_subject(s, AD_HOC)
        ad_hoc = screen(s, subject, mode=RunMode.AD_HOC, trigger=CONSOLE)
    return Fixture(juan=juan, runs=(first, whitelisted, other, ad_hoc))


def _new_un_version(store: LocalFsObjectStore, un_entries: tuple[ListEntry, ...]):
    # Thirty more XIMENA and OCOTLAN entries: above the library's rare share (ten
    # of its minimum corpus), so their weight falls under the new IDF.
    crowd = tuple(
        un_entry(f"2{i:03d}", f"Ximena Ocotlan Tla{letter}tl")
        for i, letter in enumerate("abcdefghijklmnopqrstuvwxyzabcd")
    )
    _ = load_shared(UN, (*un_entries, *crowd), store=store)


def test_replay_reproduces_every_hit_of_a_fixture_tenant(
    tenant: CreatedTenant,
    screened: Fixture,
    store: LocalFsObjectStore,
    un_entries: tuple[ListEntry, ...],
    fake_monotonic: FakeMonotonic,
):
    first, whitelisted, other, ad_hoc = screened.runs
    # The fixture covers a shared alert, a private alert, a whitelisted hit and an
    # ad hoc subject.
    assert {(h.list_code, h.band) for h in first.hits} >= {(OFAC, HitBand.ALERT)}
    assert (OFAC, HitBand.WHITELISTED) in {
        (h.list_code, h.band) for h in whitelisted.hits
    }
    assert (LPB, HitBand.ALERT) in {(h.list_code, h.band) for h in other.hits}
    assert ad_hoc.hits
    _new_un_version(store, un_entries)
    fake_monotonic.advance(60)
    # Today's scoring IDF is not the one the runs were scored with.
    with _staff(tenant) as s:
        subject = s.get_one(ScreeningSubject, (tenant.tenant_id, ad_hoc.subject.id))
        again = screen(s, subject, mode=RunMode.AD_HOC, trigger=CONSOLE)
    stored = {h.source_entry_key: h.score for h in ad_hoc.hits}
    assert {h.source_entry_key: h.score for h in again.hits}["1002"] != stored["1002"]

    with _staff(tenant) as s:
        reports = [replay_run(s, result.run.id) for result in screened.runs]

    for result, report in zip(screened.runs, reports, strict=True):
        assert report.run_id == result.run.id
        assert report.subject_changed is False
        assert len(report.rows) == len(result.hits)
        assert all(row.same for row in report.rows), report.rows
        assert report.identical
    whitelisted_row = next(
        row for row in reports[1].rows if row.stored_band is HitBand.WHITELISTED
    )
    assert whitelisted_row.replayed_band is HitBand.WHITELISTED
    with _staff(tenant) as s:
        hit = s.scalars(
            select(ScreeningHit).where(ScreeningHit.id == whitelisted_row.hit_id)
        ).one()
    assert hit.features["band_before_whitelist"] == "ALERT"


def test_replay_reports_a_changed_subject_and_does_not_rescore(
    tenant: CreatedTenant, screened: Fixture
):
    run = screened.runs[0].run
    with _staff(tenant) as s:
        _ = service.add_identifier(
            s,
            screened.juan,
            IdentifierInput(id_type=IdentifierType.PASSPORT, value="G12345678"),
        )

    with _staff(tenant) as s:
        report = replay_run(s, run.id)

    assert report.subject_changed is True
    assert report.rows == ()
    assert report.identical is False


def test_replay_refuses_another_matcher_version(
    tenant: CreatedTenant, screened: Fixture, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(replay, "MATCHER_VERSION", "another-matcher")

    with pytest.raises(MatcherVersionMismatchError), _staff(tenant) as s:
        _ = replay_run(s, screened.runs[0].run.id)


def test_replay_refuses_a_changed_scoring_configuration(
    tenant: CreatedTenant, screened: Fixture
):
    run_id = screened.runs[0].run.id
    with _staff(tenant) as s:
        run = s.scalars(select(ScreeningRun).where(ScreeningRun.id == run_id)).one()
        run.scoring_config_hash = b"\x00" * 32

    with pytest.raises(MatcherVersionMismatchError) as caught, _staff(tenant) as s:
        _ = replay_run(s, run_id)
    assert "scoring configuration" in caught.value.detail
