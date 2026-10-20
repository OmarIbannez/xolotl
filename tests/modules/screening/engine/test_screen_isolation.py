"""Private lists stay with their tenant through ``screen()`` (PRV-302, Review Focus 5).

Tenant A's ``MX_LPB`` lives in A's private shard only: tenant B's runs over the same
person, before and after A's run warms that shard, hold no private member and no hit,
and B's session cannot read A's runs, hits or findings.
"""

from datetime import date
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tests.helpers import staff_session
from tests.modules.screening.catalog.support import (
    load_private,
    load_shared,
    seed_sources,
)
from tests.modules.screening.engine.conftest import UN
from tests.modules.screening.review.support import assign_itf
from xolotl.modules.party import service
from xolotl.modules.party.enums import PartyType
from xolotl.modules.party.schemas import PartyCreateRequest, PersonInput
from xolotl.modules.screening.engine.enums import HitBand, RunMode, TriggerType
from xolotl.modules.screening.engine.models import (
    ScreeningHit,
    ScreeningListSetMember,
    ScreeningMatch,
    ScreeningRun,
)
from xolotl.modules.screening.engine.screen import RunResult, Trigger, screen
from xolotl.modules.screening.engine.subjects import subject_for_party
from xolotl.modules.screening.lists import (
    EntityType,
    EntryName,
    ListEntry,
    NameQuality,
    PartialDate,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.enums import Role
from xolotl.platform.storage.local import LocalFsObjectStore

LPB = "MX_LPB"
BLOCKED = ListEntry(
    list_code=LPB,
    entry_key="LPB-1",
    entity_type=EntityType.PERSON,
    primary_name=EntryName("JUAN CARLOS PEREZ GOMEZ", NameQuality.PRIMARY),
    birth_dates=(PartialDate(1970, 5, 10),),
    source_ref="clave LPB-1",
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


def _mlro(tenant: CreatedTenant):
    return staff_session(tenant.tenant_id, tenant.admin_user_id, roles=(Role.MLRO,))


def _check(tenant: CreatedTenant) -> RunResult:
    with _mlro(tenant) as s:
        party = service.create_party(s, PERSON)
        subject = subject_for_party(s, party.id, for_update=True)
        return screen(
            s, subject, mode=RunMode.AD_HOC, trigger=Trigger(TriggerType.CONSOLE)
        )


def _codes(s: Session, run: ScreeningRun) -> set[str]:
    return set(
        s.scalars(
            select(ScreeningListSetMember.list_code).where(
                ScreeningListSetMember.list_set_id == run.list_set_id
            )
        )
    )


def _ids(
    s: Session, model: type[ScreeningRun | ScreeningHit | ScreeningMatch]
) -> set[UUID]:
    return set(s.scalars(select(model.id)))


def test_tenant_b_never_hits_tenant_a_private_list(
    tenant: CreatedTenant,
    other_tenant: CreatedTenant,
    admin_engine: Engine,
    store: LocalFsObjectStore,
    un_entries,
    holder,
):
    seed_sources(admin_engine)
    _ = load_shared(UN, un_entries, store=store)
    for each in (tenant, other_tenant):
        assign_itf(each, admin_engine)
    with _mlro(tenant) as s:
        _ = load_private(s, store, LPB, [BLOCKED])

    # B screens before A's private shard is warm and again after A's run warmed it.
    before = _check(other_tenant)
    ours = _check(tenant)
    after = _check(other_tenant)

    for theirs in (before, after):
        assert theirs.hits == ()
        assert theirs.new_matches == ()
    assert [(h.list_code, h.band) for h in ours.hits] == [(LPB, HitBand.ALERT)]
    assert ours.hits[0].private_list_entry_id is not None
    assert ours.hits[0].features["list_kind"] == "BLOCKED"
    with _mlro(other_tenant) as s:
        assert _codes(s, before.run) == _codes(s, after.run) == {UN}
        assert _ids(s, ScreeningRun) == {before.run.id, after.run.id}
        assert _ids(s, ScreeningHit) == set()
        assert _ids(s, ScreeningMatch) == set()
    with _mlro(tenant) as s:
        assert _codes(s, ours.run) == {UN, LPB}
        assert _ids(s, ScreeningMatch) == {m.id for m in ours.new_matches}
