"""Screening a tenant whose packs are assigned per product (SP-03 Decision 14).

D21 forbids a tenant-wide assignment beside product assignments, so the normal
Mexican tenant (ITF on ``EMONEY``, AVI on ``CRYPTO``, SP-04 spec §19's tenant A)
has no tenant-wide policy. A screening run takes the strictest merge of every pack
in force for the tenant that day, whatever the party's relationships: the union of
the lists and the strictest bands.
"""

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import platform_ctx, staff_session, system_ctx
from tests.modules.screening.catalog.support import load_shared, seed_sources
from tests.modules.screening.engine.conftest import UN
from tests.platform.policy_support import ITF as ITF_PATH
from tests.platform.policy_support import install_both_packs
from xolotl.modules.party import customer, service
from xolotl.modules.party.enums import PartyType
from xolotl.modules.party.schemas import (
    PartyCreateRequest,
    PersonInput,
    RelationshipOpenRequest,
)
from xolotl.modules.screening.catalog.subscriptions import reconcile_subscriptions
from xolotl.modules.screening.composition import register_screening
from xolotl.modules.screening.engine.enums import (
    Decision,
    HitBand,
    RunMode,
    TriggerType,
)
from xolotl.modules.screening.engine.holder import (
    ScreeningIndexHolder,
    set_index_holder,
)
from xolotl.modules.screening.engine.models import (
    ScreeningListSetMember,
    ScreeningRun,
)
from xolotl.modules.screening.engine.policy import policy_for_run, scoring_config
from xolotl.modules.screening.engine.queries import AdHocSubjectInput
from xolotl.modules.screening.engine.screen import RunResult, Trigger, screen
from xolotl.modules.screening.engine.subjects import (
    create_ad_hoc_subject,
    subject_for_party,
)
from xolotl.modules.screening.lists import (
    EntityType,
    EntryName,
    ListEntry,
    NameQuality,
    PartialDate,
)
from xolotl.modules.screening.rescreen.party_events import SCREEN_PARTY_KIND
from xolotl.modules.screening.review.dispositions import disposition_match
from xolotl.modules.screening.review.models import MatchDisposition
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import JobStatus, Role
from xolotl.platform.events import OutboxEventBus
from xolotl.platform.events.registry import reset_subscribers
from xolotl.platform.jobs import JobRunner, PostgresJobQueue
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.policy import (
    PackConfig,
    Provenanced,
    ScreeningBands,
    assign_pack,
    effective_policy,
    get_snapshot,
    install_pack,
    load_pack,
    snapshot_policy,
)
from xolotl.platform.storage.local import LocalFsObjectStore

ITF = "MX/ITF-IFPE"
AVI = "MX/LFPIORPI-AVI"
OFAC = "OFAC_SDN"
EU = "EU_FSF"
FROM = date(2026, 1, 1)
JUAN = ListEntry(
    list_code=OFAC,
    entry_key="7001",
    entity_type=EntityType.PERSON,
    primary_name=EntryName("JUAN CARLOS PEREZ GOMEZ", NameQuality.PRIMARY),
    birth_dates=(PartialDate(1970, 5, 10),),
    source_ref="sdn 7001",
)
EU_ENTRY = ListEntry(
    list_code=EU,
    entry_key="EU-1",
    entity_type=EntityType.PERSON,
    primary_name=EntryName("Teodora Itzel Cuautla", NameQuality.PRIMARY),
    source_ref="eu 1",
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
CONSOLE = Trigger(TriggerType.CONSOLE, "req-1")
# Two test packs whose lists and bands differ: EMONEY screens OFAC with a lower PEP
# band; CRYPTO screens the EU list with lower sanctions bands and a stricter ID rule.
EMONEY_BANDS = ScreeningBands(pep_alert=Decimal("0.75"))
CRYPTO_BANDS = ScreeningBands(
    sanctions_alert=Decimal("0.65"),
    sanctions_review=Decimal("0.50"),
    id_match_floor=Decimal("0.97"),
    id_conflict_cap=Decimal("0.35"),
)
STRICTEST = ScreeningBands(
    sanctions_alert=Decimal("0.65"),
    sanctions_review=Decimal("0.50"),
    pep_alert=Decimal("0.75"),
    id_match_floor=Decimal("0.97"),
    id_conflict_cap=Decimal("0.35"),
)


def _variant(code: str, recommended: list[str], bands: ScreeningBands) -> PackConfig:
    base = load_pack(ITF_PATH, allow_unverified=True)
    screening = base.screening.model_copy(
        update={
            "mandatory_lists": [
                Provenanced[str](
                    value=UN,
                    legal_basis="prueba",
                    confidence=entry.confidence,
                    verified_as_of=entry.verified_as_of,
                )
                for entry in base.screening.mandatory_lists[:1]
            ],
            "recommended_lists": recommended,
            "bands": bands,
        }
    )
    return PackConfig.model_validate({
        **base.model_dump(),
        "pack": code,
        "title": code,
        "screening": screening.model_dump(),
    })


def _install(admin_engine: Engine, *configs: PackConfig) -> list[UUID]:
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    with tenant_session(platform_ctx(), factory=owner) as s:
        return [
            install_pack(s, config, source_path="test", loaded_by="test")
            for config in configs
        ]


def _assign(tenant: CreatedTenant, *assignments: tuple[UUID, str | None]) -> None:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        for version_id, product in assignments:
            _ = assign_pack(
                s,
                pack_version_id=version_id,
                product=product,
                effective_from=FROM,
                approved_by=tenant.admin_user_id,
            )


@pytest.fixture
def per_product(tenant: CreatedTenant, admin_engine: Engine) -> None:
    """Assign ITF to ``EMONEY`` and AVI to ``CRYPTO``, as SP-04's tenant A."""
    versions = install_both_packs(admin_engine)
    _assign(tenant, (versions[ITF], "EMONEY"), (versions[AVI], "CRYPTO"))


@pytest.fixture
def two_products(tenant: CreatedTenant, admin_engine: Engine) -> None:
    """Assign the two test packs to ``EMONEY`` and ``CRYPTO``, in force today."""
    emoney, crypto = _install(
        admin_engine,
        _variant("MX/TEST-EMONEY", [OFAC], EMONEY_BANDS),
        _variant("MX/TEST-CRYPTO", [EU], CRYPTO_BANDS),
    )
    _assign(tenant, (emoney, "EMONEY"), (crypto, "CRYPTO"))


@pytest.fixture
def lists(admin_engine: Engine, store: LocalFsObjectStore, un_entries) -> None:
    seed_sources(admin_engine)
    _ = load_shared(OFAC, [JUAN], store=store)
    _ = load_shared(UN, un_entries, store=store)
    _ = load_shared(EU, [EU_ENTRY], store=store)


@contextmanager
def _staff(tenant: CreatedTenant, *roles: Role) -> Generator[Session]:
    with staff_session(
        tenant.tenant_id, tenant.admin_user_id, roles=roles or (Role.MLRO,)
    ) as s:
        yield s


def _party(tenant: CreatedTenant, *products: str) -> UUID:
    with _staff(tenant, Role.ADMIN) as s:
        party_id = service.create_party(s, PERSON).id
        for product in products:
            _ = customer.open_relationship(
                s,
                RelationshipOpenRequest(
                    party_id=party_id,
                    external_ref=f"{product}-{party_id}",
                    product=product,
                ),
            )
    return party_id


def _check(tenant: CreatedTenant, party_id: UUID) -> RunResult:
    with _staff(tenant) as s:
        subject = subject_for_party(s, party_id, for_update=True)
        return screen(s, subject, mode=RunMode.AD_HOC, trigger=CONSOLE)


def _members(tenant: CreatedTenant, run: ScreeningRun) -> set[str]:
    with _staff(tenant) as s:
        return set(
            s.scalars(
                select(ScreeningListSetMember.list_code).where(
                    ScreeningListSetMember.list_set_id == run.list_set_id
                )
            )
        )


def _bands(tenant: CreatedTenant, run: ScreeningRun) -> ScreeningBands:
    with _staff(tenant) as s:
        return get_snapshot(s, run.policy_snapshot_id).policy.screening.bands


# ---- the policy a run resolves ---------------------------------------------------


def test_a_per_product_tenant_resolves_the_merge_of_its_packs(
    tenant: CreatedTenant, per_product, monkeypatch: pytest.MonkeyPatch
):
    # Before AVI's effective_from only ITF is in force; from 30 November both are.
    with _staff(tenant) as s:
        october = policy_for_run(s)
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 12, 1, 18, tzinfo=UTC))
    with _staff(tenant) as s:
        december = policy_for_run(s)
        frozen = get_snapshot(s, december.snapshot_id).policy
    assert october.effective.pack_codes == [ITF]
    assert december.effective.pack_codes == [ITF, AVI]
    assert december.effective.product is None
    # The snapshot records what was applied: both packs and the merged body.
    assert frozen == december.effective
    assert {entry.value for entry in frozen.screening.mandatory_lists} == {
        "MX_LPB",
        UN,
    }


def test_a_tenant_wide_tenant_resolves_its_tenant_wide_policy_as_before(
    tenant: CreatedTenant, admin_engine: Engine
):
    _assign(tenant, (install_both_packs(admin_engine)[ITF], None))

    with _staff(tenant) as s:
        view = policy_for_run(s)
        tenant_wide = effective_policy(s, on=view.effective.on, product=None)
        assert view.snapshot_id == snapshot_policy(s, tenant_wide)
    assert view.effective == tenant_wide


# ---- runs ------------------------------------------------------------------------


def test_a_party_in_two_products_is_screened_under_the_combined_policy(
    tenant: CreatedTenant, two_products, lists, holder: ScreeningIndexHolder
):
    result = _check(tenant, _party(tenant, "EMONEY", "CRYPTO"))

    # The union of both packs' lists (MX_LPB is private and the tenant has none).
    assert _members(tenant, result.run) == {UN, OFAC, EU}
    # The strictest of each band, frozen in the run's snapshot and applied.
    assert _bands(tenant, result.run) == STRICTEST
    assert result.run.scoring_config_hash == scoring_config(STRICTEST).config_hash()
    (hit,) = [h for h in result.hits if h.source_entry_key == "7001"]
    assert hit.band is HitBand.ALERT
    assert hit.features["thresholds"] == {"alert": "0.65", "review": "0.50"}


@pytest.mark.parametrize(
    "products", [pytest.param((), id="no-relationship"), ("EMONEY",)]
)
def test_any_party_is_screened_under_every_pack_in_force(
    tenant: CreatedTenant,
    two_products,
    lists,
    holder: ScreeningIndexHolder,
    products: tuple[str, ...],
):
    # Screening is per party, not per product (Decision 14): a party screened at
    # onboarding, before any relationship, or with one product only, is screened
    # against every list the tenant's packs name.
    result = _check(tenant, _party(tenant, *products))

    assert _members(tenant, result.run) == {UN, OFAC, EU}
    assert _bands(tenant, result.run) == STRICTEST


def test_an_ad_hoc_check_and_a_disposition_work_for_a_per_product_tenant(
    tenant: CreatedTenant, two_products, lists, holder: ScreeningIndexHolder
):
    body = AdHocSubjectInput(
        type="PERSON",
        names=["Juan Carlos Pérez Gómez"],
        given_names="Juan Carlos",
        first_surname="Pérez",
        second_surname="Gómez",
        birth_date=date(1970, 5, 10),
    )
    with _staff(tenant) as s:
        subject = create_ad_hoc_subject(s, body)
        result = screen(s, subject, mode=RunMode.AD_HOC, trigger=CONSOLE)
    assert subject.retention_until is not None
    (match,) = result.new_matches

    with _staff(tenant, Role.ANALYST) as s:
        _ = disposition_match(
            s, match.id, decision=Decision.FALSE_POSITIVE, rationale="Otra persona"
        )
        disposition = s.scalars(
            select(MatchDisposition).where(MatchDisposition.match_id == match.id)
        ).one()
    assert disposition.policy_snapshot_id == result.run.policy_snapshot_id


def test_a_per_product_tenant_subscribes_the_union_of_its_lists(
    tenant: CreatedTenant, two_products, lists
):
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        report = reconcile_subscriptions(s, today=date(2026, 10, 19))

    assert report is not None
    assert set(report.added) == {UN, OFAC, EU}


# ---- a party is screened on creation ---------------------------------------------


@dataclass(frozen=True, slots=True)
class Wired:
    bus: OutboxEventBus
    runner: JobRunner


@pytest.fixture
def wired(
    tenant: CreatedTenant, per_product, admin_engine: Engine, tmp_path: Path
) -> Iterator[Wired]:
    seed_sources(admin_engine)
    _ = load_shared(OFAC, [JUAN], store=LocalFsObjectStore(tmp_path / "objects"))
    factory = get_sessionmaker()
    queue = PostgresJobQueue(factory)
    reset_subscribers()
    _ = register_screening(
        session_factory=factory, settings=get_settings(), queue=queue
    )
    yield Wired(OutboxEventBus(factory), JobRunner(queue, factory, worker_id="w-t"))
    reset_subscribers()
    set_index_holder(None)


def test_a_per_product_tenant_screens_a_new_party(tenant: CreatedTenant, wired: Wired):
    party_id = _party(tenant)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = s.execute(
            text("UPDATE outbox SET created_at = created_at - interval '1 minute'")
        )
    while wired.bus.dispatch_pending():
        pass

    assert wired.runner.run_once() == 1

    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        (job,) = s.scalars(select(JobRow).where(JobRow.kind == SCREEN_PARTY_KIND))
        assert job.status is JobStatus.DONE
    with _staff(tenant) as s:
        (run,) = s.scalars(select(ScreeningRun))
        subject = subject_for_party(s, party_id)
    assert run.mode is RunMode.NEW_SUBJECT
    assert run.subject_id == subject.id
    assert run.hit_count == 1
