"""Screening subjects: one per party, ad hoc inputs encrypted, status from findings.

Whenever a test expects an exception from inside a session, ``pytest.raises`` is the
outermost context manager, so the error rolls the session back.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from tests.helpers import everything_stored, leaked, staff_session, system_ctx
from tests.platform.policy_support import install_both_packs
from xolotl.modules.party import service
from xolotl.modules.party.enums import IdentifierType, PartyType
from xolotl.modules.party.schemas import PartyCreateRequest, PersonInput
from xolotl.modules.screening.catalog.models import PrivateList, PrivateListEntryRow
from xolotl.modules.screening.engine.enums import (
    HitBand,
    MatchStatus,
    RunMode,
    SubjectKind,
    SubjectStatus,
    TriggerType,
)
from xolotl.modules.screening.engine.models import (
    ScreeningListSet,
    ScreeningMatch,
    ScreeningRun,
    ScreeningSubject,
)
from xolotl.modules.screening.engine.queries import AdHocIdentifier, AdHocSubjectInput
from xolotl.modules.screening.engine.subjects import (
    create_ad_hoc_subject,
    refresh_subject_status,
    subject_for_party,
)
from xolotl.modules.screening.enums import ListKind
from xolotl.modules.screening.events import STATUS_CHANGED
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.crypto.encrypted import encrypt_json
from xolotl.platform.db import tenant_session
from xolotl.platform.errors import NotFoundError
from xolotl.platform.events.event import Event, event_from_row
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.ids import uuid7
from xolotl.platform.policy import (
    PolicyNotAssignedError,
    PolicySnapshot,
    assign_pack,
)

ANA = PartyCreateRequest(
    party_type=PartyType.PERSON,
    person=PersonInput(given_names="Ana María", first_surname="Pérez"),
)
AD_HOC = AdHocSubjectInput(
    type="PERSON",
    names=["Zacarías Quintanilla Ybarra"],
    given_names="Zacarías",
    first_surname="Quintanilla",
    second_surname="Ybarra",
    birth_date=date(1961, 2, 3),
    identifiers=[
        AdHocIdentifier(id_type=IdentifierType.CURP, value="PEGA760510MDFRMN03")
    ],
)


@pytest.fixture
def itf_pack(tenant: CreatedTenant, admin_engine: Engine) -> UUID:
    """Assign the ITF pack to every product of the tenant from 2026-01-01.

    Returns:
        The assignment's id.

    """
    version_id = install_both_packs(admin_engine)["MX/ITF-IFPE"]
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        return assign_pack(
            s,
            pack_version_id=version_id,
            product=None,
            effective_from=date(2026, 1, 1),
            approved_by=tenant.admin_user_id,
        )


def _status_events(tenant_id: UUID) -> list[Event]:
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.scalars(
            select(OutboxRow)
            .where(OutboxRow.type == STATUS_CHANGED)
            .order_by(OutboxRow.seq)
        ).all()
        return [event_from_row(row) for row in rows]


def _list_entry(s: Session, tenant_id: UUID) -> PrivateListEntryRow:
    # The tenant's MX_LPB list, created on first use, with one new entry.
    parent = s.scalar(select(PrivateList).where(PrivateList.list_code == "MX_LPB"))
    if parent is None:
        parent = PrivateList(
            tenant_id=tenant_id,
            list_code="MX_LPB",
            list_kind=ListKind.BLOCKED,
            title="Lista de personas bloqueadas",
            created_by="staff:test",
        )
        s.add(parent)
        s.flush()
    entry = PrivateListEntryRow(
        tenant_id=tenant_id,
        list_id=parent.id,
        source_entry_key=f"clave {uuid4().hex}",
        content_tag=uuid4().bytes * 2,
        entity_type="PERSON",
        entry=encrypt_json({"k": "v"}, tenant_id=tenant_id, session=s),
        from_seq=1,
    )
    s.add(entry)
    s.flush()
    return entry


def _finding(s: Session, subject: ScreeningSubject) -> ScreeningMatch:
    # A finding of a private entry, from a run of its own.
    tenant_id = subject.tenant_id
    list_set = ScreeningListSet(tenant_id=tenant_id, content_hash=uuid4().bytes * 2)
    snapshot = PolicySnapshot(
        tenant_id=tenant_id, content_hash=uuid4().bytes * 2, content={"on": "x"}
    )
    s.add_all([list_set, snapshot])
    s.flush()
    run = ScreeningRun(
        tenant_id=tenant_id,
        subject_id=subject.id,
        mode=RunMode.AD_HOC,
        trigger_type=TriggerType.CONSOLE,
        list_set_id=list_set.id,
        policy_snapshot_id=snapshot.id,
        matcher_version="test/1",
        scoring_config_hash=b"\x00" * 32,
        query_fingerprint=b"\x00" * 32,
        candidate_count=1,
        hit_count=1,
        new_match_count=1,
        outcome=SubjectStatus.PENDING_REVIEW,
        latency_ms=1,
        created_by="staff:test",
    )
    s.add(run)
    s.flush()
    entry = _list_entry(s, tenant_id)
    match = ScreeningMatch(
        tenant_id=tenant_id,
        subject_id=subject.id,
        list_code="MX_LPB",
        list_kind=ListKind.BLOCKED,
        private_list_entry_id=entry.id,
        source_entry_key=entry.source_entry_key,
        entry_content_hash=entry.content_tag,
        band=HitBand.ALERT,
        score=Decimal("0.9500"),
        origin_run_id=run.id,
    )
    s.add(match)
    s.flush()
    return match


def test_a_party_subject_is_created_once(engine: Engine, tenant: CreatedTenant):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ANA)
        first = subject_for_party(s, party.id)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        second = subject_for_party(s, party.id, for_update=True)
        count = s.scalar(select(func.count()).select_from(ScreeningSubject))
    assert first.id == second.id
    assert count == 1
    assert (second.kind, second.party_id, second.status, second.input) == (
        SubjectKind.PARTY,
        party.id,
        SubjectStatus.CLEAR,
        None,
    )
    with (
        pytest.raises(NotFoundError),
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = subject_for_party(s, uuid7())


def test_an_ad_hoc_subject_stores_its_input_encrypted_with_a_retention_date(
    engine: Engine,
    tenant: CreatedTenant,
    itf_pack: UUID,
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
):
    # 03:00 UTC on 20 October is still 19 October in Mexico City.
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 10, 20, 3, tzinfo=UTC))
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        subject = create_ad_hoc_subject(s, AD_HOC)
        assert subject.input is not None
        assert subject.input.reveal(session=s) == AD_HOC.model_dump(mode="json")
    assert (subject.kind, subject.party_id, subject.status) == (
        SubjectKind.AD_HOC,
        None,
        SubjectStatus.CLEAR,
    )
    # The ITF pack keeps KYC records for P10Y.
    assert subject.retention_until == date(2036, 10, 19)
    stored = everything_stored(admin_engine)
    for secret in ("Zacarías", "Quintanilla", "Ybarra", "PEGA760510MDFRMN03"):
        assert leaked(stored, secret.encode()) == []


def test_an_ad_hoc_subject_needs_a_policy(engine: Engine, tenant: CreatedTenant):
    with (
        pytest.raises(PolicyNotAssignedError),
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = create_ad_hoc_subject(s, AD_HOC)


def test_the_status_follows_the_findings_and_publishes_only_on_change(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ANA)
        subject = subject_for_party(s, party.id)
        assert refresh_subject_status(s, subject) is SubjectStatus.CLEAR
        first = _finding(s, subject)
        second = _finding(s, subject)
        assert refresh_subject_status(s, subject) is SubjectStatus.PENDING_REVIEW
    assert len(_status_events(tenant.tenant_id)) == 1
    (changed,) = _status_events(tenant.tenant_id)
    assert changed.data == {
        "subject_id": str(subject.id),
        "party_id": str(party.id),
        "status": "PENDING_REVIEW",
    }
    assert (changed.subject, changed.key, changed.source) == (
        f"screening_subject/{subject.id}",
        str(subject.id),
        "xolotl.screening",
    )

    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        subject = subject_for_party(s, party.id)
        assert refresh_subject_status(s, subject) is SubjectStatus.PENDING_REVIEW
    assert len(_status_events(tenant.tenant_id)) == 1

    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        subject = subject_for_party(s, party.id)
        s.get_one(
            ScreeningMatch, (tenant.tenant_id, first.id)
        ).status = MatchStatus.TRUE_MATCH
        assert refresh_subject_status(s, subject) is SubjectStatus.TRUE_MATCH

    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        subject = subject_for_party(s, party.id)
        s.get_one(
            ScreeningMatch, (tenant.tenant_id, first.id)
        ).status = MatchStatus.FALSE_POSITIVE
        s.get_one(
            ScreeningMatch, (tenant.tenant_id, second.id)
        ).status = MatchStatus.SUPERSEDED
        assert refresh_subject_status(s, subject) is SubjectStatus.CLEAR
        assert s.get_one(ScreeningSubject, (tenant.tenant_id, subject.id)).status is (
            SubjectStatus.CLEAR
        )
    assert [e.data["status"] for e in _status_events(tenant.tenant_id)] == [
        "PENDING_REVIEW",
        "TRUE_MATCH",
        "CLEAR",
    ]


def test_an_ad_hoc_subjects_status_event_has_no_party(
    engine: Engine, tenant: CreatedTenant, itf_pack: UUID
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        subject = create_ad_hoc_subject(s, AD_HOC)
        _ = _finding(s, subject)
        s.get_one(
            ScreeningMatch, (tenant.tenant_id, _finding(s, subject).id)
        ).status = MatchStatus.ESCALATED
        assert refresh_subject_status(s, subject) is SubjectStatus.PENDING_REVIEW
    (changed,) = _status_events(tenant.tenant_id)
    assert changed.data["party_id"] is None
