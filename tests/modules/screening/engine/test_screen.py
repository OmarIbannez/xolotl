"""``screen()``: runs, list sets, hits, findings and status (SP-03 spec §8.3, §8.4).

Scores are asserted by band, never by value: the library's corpus owns the values.
Whenever a test expects an exception from inside a session, ``pytest.raises`` is the
outermost context manager, so the error rolls the session back.
"""

import dataclasses
import threading
from collections.abc import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Unpack
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from tests.helpers import staff_session, system_ctx
from tests.modules.screening.catalog.support import load_shared, seed_sources
from tests.modules.screening.engine.conftest import UN, FakeMonotonic
from tests.modules.screening.review.support import assign_itf
from tests.platform.jobs_support import FakeClock
from tests.platform.webhooks_support import Stack, deliveries, make_endpoint
from xolotl.modules.party import service
from xolotl.modules.party.enums import IdentifierType, PartyType
from xolotl.modules.party.schemas import (
    IdentifierInput,
    OrganizationInput,
    PartyCreateRequest,
    PersonInput,
)
from xolotl.modules.screening.catalog.models import (
    ListEntryRow,
    TenantListSubscription,
)
from xolotl.modules.screening.composition import register_screening
from xolotl.modules.screening.engine.entries import indexed_shared_entry
from xolotl.modules.screening.engine.enums import (
    AutoReason,
    BatchKind,
    BatchStatus,
    DecidedByType,
    Decision,
    HitBand,
    MatchStatus,
    RunMode,
    SubjectStatus,
    TriggerType,
)
from xolotl.modules.screening.engine.holder import ScreeningIndexHolder
from xolotl.modules.screening.engine.models import (
    ScreeningBatch,
    ScreeningHit,
    ScreeningListSet,
    ScreeningListSetMember,
    ScreeningMatch,
    ScreeningRun,
)
from xolotl.modules.screening.engine.policy import thresholds_for, top_k_for
from xolotl.modules.screening.engine.queries import (
    AdHocSubjectInput,
    query_for_ad_hoc,
    query_for_party,
)
from xolotl.modules.screening.engine.screen import (
    RunResult,
    ScreenOptions,
    Trigger,
    screen,
)
from xolotl.modules.screening.engine.subjects import (
    create_ad_hoc_subject,
    subject_for_party,
)
from xolotl.modules.screening.enums import ListKind, ListRequirement
from xolotl.modules.screening.events import (
    MATCH_CREATED,
    RUN_COMPLETED,
    STAFF_ONLY_EVENT_TYPES,
    STATUS_CHANGED,
)
from xolotl.modules.screening.lists import (
    EntityType,
    EntryIdentifier,
    EntryName,
    IdType,
    ListEntry,
    NameQuality,
    PartialDate,
)
from xolotl.modules.screening.matching import (
    MATCHER_VERSION,
    BandThresholds,
    MemoryIndex,
    ScoringConfig,
)
from xolotl.modules.screening.review.dispositions import disposition_match
from xolotl.modules.screening.review.models import MatchDisposition
from xolotl.modules.screening.review.whitelist import revoke_whitelist
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.events.registry import reset_subscribers
from xolotl.platform.policy import (
    PolicyNotAssignedError,
    ScreeningBands,
    TenantOverride,
    create_override,
    get_snapshot,
)
from xolotl.platform.storage.local import LocalFsObjectStore

OFAC = "OFAC_SDN"
EU = "EU_FSF"
SAT = "MX_SAT_69B"
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
# Retrieved for the party's tokens, but scored below the review band.
NEAR_MISSES = (
    ListEntry(
        list_code=OFAC,
        entry_key="7002",
        entity_type=EntityType.PERSON,
        primary_name=EntryName("CARLOS GOMEZ RUIZ", NameQuality.PRIMARY),
        source_ref="sdn 7002",
    ),
    ListEntry(
        list_code=OFAC,
        entry_key="7003",
        entity_type=EntityType.PERSON,
        primary_name=EntryName("JUAN HERNANDEZ LOPEZ", NameQuality.PRIMARY),
        source_ref="sdn 7003",
    ),
)
PERSON = PartyCreateRequest(
    party_type=PartyType.PERSON,
    person=PersonInput(
        given_names="Juan Carlos",
        first_surname="Pérez",
        second_surname="Gómez",
        birth_date=BIRTH,
    ),
)
CONSOLE = Trigger(TriggerType.CONSOLE, "req-1")


@pytest.fixture
def itf_pack(tenant: CreatedTenant, admin_engine: Engine) -> None:
    assign_itf(tenant, admin_engine)


@pytest.fixture
def lists(admin_engine: Engine, store: LocalFsObjectStore, un_entries) -> None:
    seed_sources(admin_engine)
    _ = load_shared(OFAC, (JUAN, *NEAR_MISSES), store=store)
    _ = load_shared(UN, un_entries, store=store)


@contextmanager
def _staff(tenant: CreatedTenant, *roles: Role) -> Generator[Session]:
    with staff_session(
        tenant.tenant_id, tenant.admin_user_id, roles=roles or (Role.MLRO,)
    ) as s:
        yield s


def _party(tenant: CreatedTenant, request: PartyCreateRequest = PERSON) -> UUID:
    with _staff(tenant) as s:
        return service.create_party(s, request).id


def _check(
    tenant: CreatedTenant, party_id: UUID, **options: Unpack[ScreenOptions]
) -> RunResult:
    with _staff(tenant) as s:
        subject = subject_for_party(s, party_id, for_update=True)
        return screen(s, subject, mode=RunMode.AD_HOC, trigger=CONSOLE, **options)


def _members(tenant: CreatedTenant, run: ScreeningRun) -> dict[str, UUID | None]:
    with _staff(tenant) as s:
        rows = s.scalars(
            select(ScreeningListSetMember).where(
                ScreeningListSetMember.list_set_id == run.list_set_id
            )
        )
        return {
            m.list_code: m.list_version_id or m.private_list_version_id for m in rows
        }


def _matches(tenant: CreatedTenant) -> list[ScreeningMatch]:
    with _staff(tenant) as s:
        return list(
            s.scalars(
                select(ScreeningMatch).order_by(
                    ScreeningMatch.created_at, ScreeningMatch.id
                )
            )
        )


def _outbox_types(tenant: CreatedTenant) -> list[str]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return list(s.scalars(select(OutboxRow.type).order_by(OutboxRow.seq)))


def _ofac_hit(result: RunResult) -> ScreeningHit:
    (hit,) = [h for h in result.hits if h.source_entry_key == "7001"]
    return hit


def _false_positive(tenant: CreatedTenant, match_id: UUID) -> None:
    with _staff(tenant, Role.ANALYST) as s:
        _ = disposition_match(
            s,
            match_id,
            decision=Decision.FALSE_POSITIVE,
            rationale="Fecha de nacimiento distinta",
        )


# ---- a run and what it stores --------------------------------------------------


def test_a_party_check_creates_an_alert_finding_with_a_stored_explanation(
    tenant: CreatedTenant, itf_pack, lists, holder
):
    result = _check(tenant, _party(tenant))

    assert [h.band for h in result.hits] == [HitBand.ALERT]
    hit = result.hits[0]
    features = hit.features
    assert features["v"] == 1
    assert features["alignment"]
    assert features["list_kind"] == "SANCTION"
    assert features["thresholds"] == {"alert": "0.70", "review": "0.55"}
    assert features["matcher_version"] == MATCHER_VERSION
    entry_ref = features["entry_ref"]
    assert isinstance(entry_ref, dict)
    assert entry_ref["list_code"] == OFAC
    assert entry_ref["entry_key"] == "7001"
    assert entry_ref["list_entry_id"] == str(hit.list_entry_id)
    assert entry_ref["list_version_seq"] == 1
    assert hit.entry_content_hash == JUAN.content_hash()
    (finding,) = _matches(tenant)
    assert finding.status is MatchStatus.OPEN
    assert finding.band is HitBand.ALERT
    assert finding.list_kind is ListKind.SANCTION
    assert finding.origin_run_id == result.run.id
    assert hit.match_id == finding.id
    assert [m.id for m in result.new_matches] == [finding.id]
    assert result.subject.status is SubjectStatus.PENDING_REVIEW
    assert result.run.outcome is SubjectStatus.PENDING_REVIEW
    assert result.run.hit_count == 1
    assert result.run.new_match_count == 1
    assert result.subject.last_run_id == result.run.id
    assert result.subject.last_screened_at is not None


def test_an_ad_hoc_subject_is_screened_by_its_decrypted_input(
    tenant: CreatedTenant, itf_pack, lists, holder
):
    body = AdHocSubjectInput(
        type="PERSON",
        names=["Juan Carlos Pérez Gómez"],
        given_names="Juan Carlos",
        first_surname="Pérez",
        second_surname="Gómez",
        birth_date=BIRTH,
    )
    with _staff(tenant) as s:
        subject = create_ad_hoc_subject(s, body)
        result = screen(s, subject, mode=RunMode.AD_HOC, trigger=CONSOLE)

    assert [h.band for h in result.hits] == [HitBand.ALERT]
    assert result.run.query_fingerprint == query_for_ad_hoc(body).fingerprint()
    assert result.subject.status is SubjectStatus.PENDING_REVIEW


def test_a_run_records_everything_needed_to_reproduce_it(
    tenant: CreatedTenant, itf_pack, lists, holder: ScreeningIndexHolder
):
    party_id = _party(tenant)

    run = _check(tenant, party_id).run

    with _staff(tenant) as s:
        shard = holder.shared(s)
        fingerprint = query_for_party(s, party_id).fingerprint()
        bands = get_snapshot(s, run.policy_snapshot_id).policy.screening.bands
    held = {code: v.version_id for code, v in shard.versions.items()}
    assert _members(tenant, run) == held
    assert set(held) == {OFAC, UN}
    assert run.policy_snapshot_id is not None
    assert run.matcher_version == MATCHER_VERSION
    # The configuration is rebuilt from the run's own policy snapshot.
    config = ScoringConfig(
        id_match_floor=bands.id_match_floor, id_conflict_cap=bands.id_conflict_cap
    )
    assert (config.id_match_floor, config.id_conflict_cap) == (
        Decimal("0.95"),
        Decimal("0.30"),
    )
    assert run.scoring_config_hash == config.config_hash()
    assert run.query_fingerprint == fingerprint
    assert run.mode is RunMode.AD_HOC
    assert run.trigger_type is TriggerType.CONSOLE
    assert run.trigger_ref == "req-1"
    assert run.created_by == str(tenant.admin_user_id)
    assert run.restricted_to_delta is False
    assert run.batch_id is None
    assert run.latency_ms >= 0


def test_the_list_set_is_shared_by_runs_over_the_same_versions(
    tenant: CreatedTenant, itf_pack, lists, holder
):
    party_id = _party(tenant)

    first = _check(tenant, party_id).run
    second = _check(tenant, party_id).run

    assert first.list_set_id == second.list_set_id
    with _staff(tenant) as s:
        assert s.scalar(select(func.count()).select_from(ScreeningListSet)) == 1
        members = s.scalar(select(func.count()).select_from(ScreeningListSetMember))
    assert members == 2


def test_lists_follow_the_policy_and_a_disabled_recommended_list_is_skipped(
    tenant: CreatedTenant, itf_pack, lists, holder
):
    party_id = _party(tenant)
    # No subscription rows yet: OFAC is screened because the policy recommends it.
    assert OFAC in _members(tenant, _check(tenant, party_id).run)
    with _staff(tenant) as s:
        s.add(
            TenantListSubscription(
                tenant_id=tenant.tenant_id,
                list_code=OFAC,
                requirement=ListRequirement.RECOMMENDED,
                enabled=False,
            )
        )

    result = _check(tenant, party_id)

    assert set(_members(tenant, result.run)) == {UN}
    assert result.hits == ()


def test_a_staff_list_filter_narrows_the_set(
    tenant: CreatedTenant, itf_pack, lists, holder
):
    result = _check(tenant, _party(tenant), lists={UN})

    assert set(_members(tenant, result.run)) == {UN}
    assert result.hits == ()
    assert _matches(tenant) == []


def test_bands_come_from_the_policy_per_list_kind(
    tenant: CreatedTenant,
    itf_pack,
    lists,
    holder,
    store: LocalFsObjectStore,
    monkeypatch: pytest.MonkeyPatch,
):
    bands = ScreeningBands()
    sanctions = BandThresholds(alert=Decimal("0.70"), review=Decimal("0.55"))
    for kind in ("SANCTION", "BLOCKED", "INTERNAL", "FISCAL"):
        assert thresholds_for(kind, bands) == sanctions
    pep = thresholds_for("PEP", bands)
    assert pep == BandThresholds(alert=Decimal("0.80"), review=Decimal("0.65"))
    assert (top_k_for("PEP"), top_k_for("SANCTION")) == (25, 50)
    # A shorter name on another list: in the review band under the pack's bands.
    _ = load_shared(
        EU,
        [
            ListEntry(
                list_code=EU,
                entry_key="eu-1",
                entity_type=EntityType.PERSON,
                primary_name=EntryName("JUAN PEREZ", NameQuality.PRIMARY),
                source_ref="eu 1",
            )
        ],
        store=store,
    )
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 10, 19, 18, tzinfo=UTC))
    party_id = _party(tenant)
    (before,) = [h for h in _check(tenant, party_id).hits if h.list_code == EU]
    assert before.band is HitBand.LOW_CONFIDENCE
    with _staff(tenant) as s:
        # Tightening: the alert band comes down to exactly this hit's score.
        _ = create_override(
            s,
            TenantOverride.model_validate({
                "bands": {"sanctions_alert": str(before.score)}
            }),
            approved_by=tenant.admin_user_id,
        )

    (after,) = [h for h in _check(tenant, party_id).hits if h.list_code == EU]

    assert after.band is HitBand.ALERT
    assert after.score == before.score
    assert after.features["thresholds"] == {
        "alert": str(before.score),
        "review": "0.55",
    }


# ---- findings ------------------------------------------------------------------


def test_a_rescreen_of_the_same_content_raises_no_second_finding(
    tenant: CreatedTenant, itf_pack, lists, holder
):
    party_id = _party(tenant)
    first = _check(tenant, party_id)

    second = _check(tenant, party_id)

    assert _ofac_hit(second).match_id == _ofac_hit(first).match_id
    assert second.run.new_match_count == 0
    assert second.new_matches == ()
    assert len(_matches(tenant)) == 1
    assert _outbox_types(tenant).count(MATCH_CREATED) == 1


def test_concurrent_runs_create_one_finding(
    tenant: CreatedTenant, itf_pack, lists, holder
):
    party_id = _party(tenant)
    with _staff(tenant) as s:
        # The subject exists before the race, and the shared shard is loaded.
        _ = subject_for_party(s, party_id)
        _ = holder.shared(s)
    barrier = threading.Barrier(2)

    def run() -> int:
        with _staff(tenant) as s:
            subject = subject_for_party(s, party_id)
            _ = barrier.wait(timeout=10)
            result = screen(s, subject, mode=RunMode.AD_HOC, trigger=CONSOLE)
            return result.run.new_match_count

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        counts = sorted(f.result(timeout=60) for f in futures)

    assert counts == [0, 1]
    (finding,) = _matches(tenant)
    with _staff(tenant) as s:
        runs = list(s.scalars(select(ScreeningRun)))
        linked = set(
            s.scalars(
                select(ScreeningHit.match_id).where(
                    ScreeningHit.source_entry_key == "7001"
                )
            )
        )
    assert sorted(r.new_match_count for r in runs) == [0, 1]
    # The run that came second saw the first run's finding.
    assert [r.outcome for r in runs] == [SubjectStatus.PENDING_REVIEW] * 2
    assert linked == {finding.id}


def test_a_whitelisted_entry_is_suppressed_and_logged_as_a_hit(
    tenant: CreatedTenant, itf_pack, lists, holder
):
    party_id = _party(tenant)
    finding = _check(tenant, party_id).new_matches[0]
    _false_positive(tenant, finding.id)

    result = _check(tenant, party_id)

    hit = _ofac_hit(result)
    assert hit.band is HitBand.WHITELISTED
    assert hit.auto_reason is AutoReason.WHITELIST
    assert hit.match_id is None
    assert result.run.new_match_count == 0
    assert [m.status for m in _matches(tenant)] == [MatchStatus.FALSE_POSITIVE]
    assert result.subject.status is SubjectStatus.CLEAR


def test_a_changed_entry_realerts_and_supersedes_the_open_finding(
    tenant: CreatedTenant,
    itf_pack,
    lists,
    holder,
    store: LocalFsObjectStore,
    fake_monotonic: FakeMonotonic,
):
    party_id = _party(tenant)
    old = _check(tenant, party_id).new_matches[0]
    changed = ListEntry(
        list_code=OFAC,
        entry_key="7001",
        entity_type=EntityType.PERSON,
        primary_name=JUAN.primary_name,
        birth_dates=JUAN.birth_dates,
        nationalities=("MX",),
        identifiers=JUAN.identifiers,
        source_ref="sdn 7001",
    )
    _ = load_shared(OFAC, (changed, *NEAR_MISSES), store=store)
    fake_monotonic.advance(60)

    result = _check(tenant, party_id)

    (new,) = result.new_matches
    assert new.entry_content_hash == changed.content_hash()
    entry_ref = _ofac_hit(result).features["entry_ref"]
    assert isinstance(entry_ref, dict)
    assert entry_ref["list_version_seq"] == 2
    with _staff(tenant) as s:
        older = s.get_one(ScreeningMatch, (tenant.tenant_id, old.id))
        (disposition,) = s.scalars(
            select(MatchDisposition).where(MatchDisposition.match_id == old.id)
        )
        rationale = disposition.rationale.reveal(session=s)
    assert older.status is MatchStatus.SUPERSEDED
    assert older.superseded_by_match_id == new.id
    assert older.closed_at is not None
    assert disposition.decision is Decision.SUPERSEDED
    assert disposition.decided_by_type is DecidedByType.SYSTEM
    assert disposition.decided_by is None
    assert disposition.prior_disposition_id is None
    assert disposition.policy_snapshot_id == result.run.policy_snapshot_id
    assert rationale == "SUPERSEDED_BY_CHANGED_ENTRY"
    assert result.subject.status is SubjectStatus.PENDING_REVIEW


def test_a_revoked_whitelist_reopens_the_false_positive_finding(
    tenant: CreatedTenant, itf_pack, lists, holder
):
    party_id = _party(tenant)
    finding = _check(tenant, party_id).new_matches[0]
    _false_positive(tenant, finding.id)
    with _staff(tenant) as s:
        whitelist_id = s.scalar(
            text("SELECT id FROM screening_whitelist WHERE subject_id = :id"),
            {"id": finding.subject_id},
        )
        _ = revoke_whitelist(s, whitelist_id)

    result = _check(tenant, party_id)

    hit = _ofac_hit(result)
    assert hit.band is HitBand.ALERT
    assert hit.match_id == finding.id
    assert [m.id for m in result.new_matches] == [finding.id]
    assert result.run.new_match_count == 1
    (reopened,) = _matches(tenant)
    assert reopened.status is MatchStatus.OPEN
    assert reopened.closed_at is None
    assert result.subject.status is SubjectStatus.PENDING_REVIEW
    assert _outbox_types(tenant).count(MATCH_CREATED) == 2


def test_a_relisted_entry_reopens_its_delisted_finding_and_alerts_again(
    tenant: CreatedTenant, itf_pack, lists, holder
):
    # Product owner decision (2026-10-20): Deviation 8 extends to DELISTED.
    party_id = _party(tenant)
    finding = _check(tenant, party_id).new_matches[0]
    with _staff(tenant) as s:
        # What a delisting leaves behind; the entry's content is unchanged.
        delisted = s.get_one(ScreeningMatch, (tenant.tenant_id, finding.id))
        delisted.status = MatchStatus.DELISTED
        delisted.closed_at = clock.now()

    result = _check(tenant, party_id)

    assert _ofac_hit(result).match_id == finding.id
    assert [m.id for m in result.new_matches] == [finding.id]
    assert result.run.new_match_count == 1
    (reopened,) = _matches(tenant)
    assert reopened.status is MatchStatus.OPEN
    assert reopened.closed_at is None
    assert result.subject.status is SubjectStatus.PENDING_REVIEW
    assert _outbox_types(tenant).count(MATCH_CREATED) == 2


def test_content_returning_to_an_earlier_version_reopens_its_finding(
    tenant: CreatedTenant,
    itf_pack,
    lists,
    holder,
    store: LocalFsObjectStore,
    fake_monotonic: FakeMonotonic,
):
    party_id = _party(tenant)
    first = _check(tenant, party_id).new_matches[0]
    changed = dataclasses.replace(JUAN, nationalities=("MX",))
    _ = load_shared(OFAC, (changed, *NEAR_MISSES), store=store)
    fake_monotonic.advance(60)
    second = _check(tenant, party_id).new_matches[0]
    _ = load_shared(OFAC, (JUAN, *NEAR_MISSES), store=store)
    fake_monotonic.advance(60)

    result = _check(tenant, party_id)

    assert [m.id for m in result.new_matches] == [first.id]
    assert _ofac_hit(result).match_id == first.id
    with _staff(tenant) as s:
        a = s.get_one(ScreeningMatch, (tenant.tenant_id, first.id))
        b = s.get_one(ScreeningMatch, (tenant.tenant_id, second.id))
        decisions = list(
            s.scalars(
                select(MatchDisposition.decision).where(
                    MatchDisposition.match_id == second.id
                )
            )
        )
    assert (a.status, a.superseded_by_match_id, a.closed_at) == (
        MatchStatus.OPEN,
        None,
        None,
    )
    assert b.status is MatchStatus.SUPERSEDED
    assert b.superseded_by_match_id == first.id
    assert b.closed_at is not None
    assert decisions == [Decision.SUPERSEDED]
    assert result.subject.status is SubjectStatus.PENDING_REVIEW
    assert _outbox_types(tenant).count(MATCH_CREATED) == 3


def test_a_stronger_band_raises_the_finding_and_never_lowers_it(
    tenant: CreatedTenant,
    itf_pack,
    lists,
    holder,
    store: LocalFsObjectStore,
    monkeypatch: pytest.MonkeyPatch,
):
    _ = load_shared(
        EU,
        [
            ListEntry(
                list_code=EU,
                entry_key="eu-1",
                entity_type=EntityType.PERSON,
                primary_name=EntryName("JUAN PEREZ", NameQuality.PRIMARY),
                source_ref="eu 1",
            )
        ],
        store=store,
    )
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 10, 19, 18, tzinfo=UTC))
    party_id = _party(tenant)
    (low,) = [h for h in _check(tenant, party_id).hits if h.list_code == EU]
    assert low.band is HitBand.LOW_CONFIDENCE
    with _staff(tenant) as s:
        _ = create_override(
            s,
            TenantOverride.model_validate({
                "bands": {"sanctions_alert": str(low.score)}
            }),
            approved_by=tenant.admin_user_id,
        )

    (high,) = [h for h in _check(tenant, party_id).hits if h.list_code == EU]

    assert high.band is HitBand.ALERT
    assert high.match_id == low.match_id

    def finding() -> ScreeningMatch:
        (match,) = [m for m in _matches(tenant) if m.id == low.match_id]
        return match

    raised = finding()
    assert (raised.band, raised.score) == (HitBand.ALERT, low.score)
    with _staff(tenant) as s:
        # A lower stored score is raised to the hit's; a higher one is kept.
        s.get_one(ScreeningMatch, (tenant.tenant_id, raised.id)).score = Decimal("0.10")
    _ = _check(tenant, party_id)
    assert finding().score == low.score
    with _staff(tenant) as s:
        s.get_one(ScreeningMatch, (tenant.tenant_id, raised.id)).score = Decimal("0.99")
    _ = _check(tenant, party_id)
    assert (finding().band, finding().score) == (HitBand.ALERT, Decimal("0.99"))


def test_an_id_conflict_is_stored_as_auto_discarded_without_a_finding(
    tenant: CreatedTenant, itf_pack, lists, holder
):
    request = PERSON.model_copy(
        update={
            "identifiers": [
                IdentifierInput(id_type=IdentifierType.PASSPORT, value="G12345678")
            ]
        }
    )

    result = _check(tenant, _party(tenant, request))

    hit = _ofac_hit(result)
    assert hit.band is HitBand.AUTO_DISCARDED
    assert hit.auto_reason is AutoReason.ID_CONFLICT_CAP
    assert hit.match_id is None
    assert _matches(tenant) == []
    assert result.subject.status is SubjectStatus.CLEAR


def test_below_cutoff_candidates_are_counted_not_stored(
    tenant: CreatedTenant, itf_pack, lists, holder
):
    run = _check(tenant, _party(tenant)).run

    assert run.candidate_count > run.hit_count == 1
    with _staff(tenant) as s:
        assert s.scalar(select(func.count()).select_from(ScreeningHit)) == 1


# ---- restricted runs (3c's batches) --------------------------------------------


def _row(tenant: CreatedTenant, key: str) -> ListEntryRow:
    with _staff(tenant) as s:
        return s.scalars(
            select(ListEntryRow).where(
                ListEntryRow.list_code == OFAC,
                ListEntryRow.source_entry_key == key,
                ListEntryRow.to_seq.is_(None),
            )
        ).one()


def test_only_entries_restricts_scoring_to_the_given_entries(
    tenant: CreatedTenant, itf_pack, lists, holder
):
    party_id = _party(tenant)
    juan = _row(tenant, "7001")
    miss = _row(tenant, "7002")

    result = _check(
        tenant,
        party_id,
        only_entries=[indexed_shared_entry(juan), indexed_shared_entry(miss)],
    )

    assert result.run.restricted_to_delta is True
    assert result.run.candidate_count <= 2
    assert [h.list_entry_id for h in result.hits] == [juan.id]
    nothing = _check(tenant, party_id, only_entries=[indexed_shared_entry(miss)])
    assert nothing.hits == ()


def test_only_index_scores_against_the_given_index_and_marks_the_run_restricted(
    tenant: CreatedTenant, itf_pack, lists, holder: ScreeningIndexHolder
):
    party_id = _party(tenant)
    juan = _row(tenant, "7001")
    with _staff(tenant) as s:
        idf = holder.shared(s).blocking_idf
    index = MemoryIndex.build([indexed_shared_entry(juan)], idf=idf)

    result = _check(tenant, party_id, lists={OFAC}, only_index=index)

    assert result.run.restricted_to_delta is True
    assert result.run.candidate_count == 1
    assert [h.list_entry_id for h in result.hits] == [juan.id]
    assert set(_members(tenant, result.run)) == {OFAC}


def test_only_entries_and_only_index_are_exclusive(
    tenant: CreatedTenant, itf_pack, lists, holder: ScreeningIndexHolder
):
    party_id = _party(tenant)
    juan = indexed_shared_entry(_row(tenant, "7001"))
    with _staff(tenant) as s:
        index = MemoryIndex.build([juan], idf=holder.shared(s).blocking_idf)

    with pytest.raises(ValueError, match="exclusive"):
        _ = _check(tenant, party_id, only_entries=[juan], only_index=index)
    with _staff(tenant) as s:
        assert s.scalar(select(func.count()).select_from(ScreeningRun)) == 0


def test_a_run_records_its_batch(tenant: CreatedTenant, itf_pack, lists, holder):
    party_id = _party(tenant)
    with _staff(tenant) as s:
        batch = ScreeningBatch(
            tenant_id=tenant.tenant_id,
            kind=BatchKind.LIST_DELTA,
            ref="batch-test",
            status=BatchStatus.RUNNING,
        )
        s.add(batch)
        s.flush()
        batch_id = batch.id

    run = _check(tenant, party_id, batch_id=batch_id).run

    assert run.batch_id == batch_id


# ---- the accepted defaults and the policy --------------------------------------


def test_a_69b_name_only_resemblance_reaches_low_confidence_at_most(
    tenant: CreatedTenant, itf_pack, lists, holder, store: LocalFsObjectStore
):
    _ = load_shared(
        SAT,
        [
            ListEntry(
                list_code=SAT,
                entry_key="CTL010101AB1",
                entity_type=EntityType.ORGANIZATION,
                primary_name=EntryName(
                    "COMERCIALIZADORA TLALOC SA DE CV", NameQuality.PRIMARY
                ),
                identifiers=(
                    EntryIdentifier(IdType.RFC, "CTL010101AB1", "CTL010101AB1"),
                ),
                source_ref="69b 1",
            )
        ],
        store=store,
    )
    organization = PartyCreateRequest(
        party_type=PartyType.ORGANIZATION,
        organization=OrganizationInput(legal_name="Comercializadora Tláloc SA de CV"),
    )

    result = _check(tenant, _party(tenant, organization))

    (hit,) = [h for h in result.hits if h.list_code == SAT]
    assert hit.band is HitBand.LOW_CONFIDENCE
    assert hit.features["list_kind"] == "FISCAL"


def test_a_tenant_without_a_policy_cannot_be_screened(
    tenant: CreatedTenant, lists, holder
):
    party_id = _party(tenant)

    with pytest.raises(PolicyNotAssignedError):
        _ = _check(tenant, party_id)
    with _staff(tenant) as s:
        assert s.scalar(select(func.count()).select_from(ScreeningRun)) == 0


# ---- events --------------------------------------------------------------------


@pytest.fixture
def webhook_stack(engine: Engine) -> Iterator[Stack]:
    reset_subscribers()
    yield Stack(FakeClock())
    reset_subscribers()


def test_a_wildcard_endpoint_gets_run_and_status_events_but_never_match_events(
    tenant: CreatedTenant, itf_pack, lists, holder, webhook_stack: Stack
):
    _ = register_screening(session_factory=get_sessionmaker(), settings=get_settings())
    party_id = _party(tenant)
    webhook_stack.settle(tenant.tenant_id)
    _ = make_endpoint(tenant.tenant_id, event_types=["*"])

    result = _check(tenant, party_id)
    assert result.new_matches
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = s.execute(
            text("UPDATE outbox SET created_at = created_at - interval '1 minute'")
        )
    # One event per key and pass: a subject's events go out in order.
    while webhook_stack.bus.dispatch_pending():
        pass

    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        types = dict(s.execute(select(OutboxRow.id, OutboxRow.type)).all())
    delivered = sorted(types[d.event_id] for d in deliveries(tenant.tenant_id))
    assert delivered == sorted([RUN_COMPLETED, STATUS_CHANGED])
    assert MATCH_CREATED in types.values()
    assert not set(delivered) & set(STAFF_ONLY_EVENT_TYPES)


def test_events_carry_ids_and_codes_only(
    tenant: CreatedTenant, itf_pack, lists, holder
):
    result = _check(tenant, _party(tenant))
    assert result.new_matches

    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        rows = s.scalars(
            select(OutboxRow).where(
                OutboxRow.type.in_([RUN_COMPLETED, STATUS_CHANGED, MATCH_CREATED])
            )
        ).all()
        payloads = repr([(row.key, row.payload) for row in rows]).lower()
    assert len(rows) == 3
    for plain in (
        "juan",
        "carlos",
        "perez",
        "pérez",
        "gomez",
        "gómez",
        "1970",
        "x0000001",
    ):
        assert plain not in payloads
