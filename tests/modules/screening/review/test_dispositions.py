"""Staff decisions on findings: four eyes, whitelist, events and encrypted rationale.

Whenever a test expects an exception from inside a session, ``pytest.raises`` is the
outermost context manager, so the error rolls the session back.
"""

from uuid import UUID

import pytest
from sqlalchemy import Engine, select

from tests.helpers import (
    audit_events,
    everything_stored,
    leaked,
    staff_session,
    system_ctx,
)
from tests.modules.screening.review.support import add_staff, assign_itf, party_finding
from xolotl.modules.screening.engine.enums import (
    BlockedReason,
    DecidedByType,
    Decision,
    MatchStatus,
    SubjectStatus,
    SuggestedDisposition,
)
from xolotl.modules.screening.engine.models import ScreeningMatch, ScreeningSubject
from xolotl.modules.screening.errors import DispositionNotAllowedError
from xolotl.modules.screening.events import (
    MATCH_DISPOSITIONED,
    TRUE_MATCH_CONFIRMED,
)
from xolotl.modules.screening.review.dispositions import disposition_match
from xolotl.modules.screening.review.models import MatchDisposition, ScreeningWhitelist
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import ActorType, Role
from xolotl.platform.errors import ForbiddenError, NotFoundError, ValidationFailedError
from xolotl.platform.events.event import Event, event_from_row
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.ids import uuid7
from xolotl.platform.tenancy.context import TenantContext

RATIONALE = "Fecha de nacimiento distinta a la del listado"


@pytest.fixture
def itf(tenant: CreatedTenant, admin_engine: Engine) -> None:
    assign_itf(tenant, admin_engine)


def _decide(
    tenant: CreatedTenant,
    user_id: UUID,
    role: Role,
    match_id: UUID,
    decision: Decision,
    rationale: str = RATIONALE,
) -> MatchDisposition:
    with staff_session(tenant.tenant_id, user_id, roles=(role,)) as s:
        return disposition_match(s, match_id, decision=decision, rationale=rationale)


def _state(tenant: CreatedTenant, subject_id: UUID, match_id: UUID):
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        match = s.get_one(ScreeningMatch, (tenant.tenant_id, match_id))
        subject = s.get_one(ScreeningSubject, (tenant.tenant_id, subject_id))
        return match.status, match.closed_at, subject.status


def _events(tenant_id: UUID, event_type: str) -> list[Event]:
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.scalars(
            select(OutboxRow)
            .where(OutboxRow.type == event_type)
            .order_by(OutboxRow.seq)
        ).all()
        return [event_from_row(row) for row in rows]


def test_an_analyst_escalates_and_a_senior_confirms_a_true_match(
    engine: Engine, tenant: CreatedTenant, itf: None
):
    analyst = add_staff(tenant, Role.ANALYST)
    senior = add_staff(tenant, Role.SENIOR_ANALYST)
    subject_id, match_id = party_finding(tenant)

    escalation = _decide(
        tenant, analyst, Role.ANALYST, match_id, Decision.POSSIBLE_TRUE_MATCH
    )
    status, closed_at, subject_status = _state(tenant, subject_id, match_id)
    assert (status, closed_at, subject_status) == (
        MatchStatus.ESCALATED,
        None,
        SubjectStatus.PENDING_REVIEW,
    )
    assert escalation.prior_disposition_id is None

    confirmation = _decide(
        tenant, senior, Role.SENIOR_ANALYST, match_id, Decision.TRUE_MATCH
    )
    status, closed_at, subject_status = _state(tenant, subject_id, match_id)
    assert (status, subject_status) == (
        MatchStatus.TRUE_MATCH,
        SubjectStatus.TRUE_MATCH,
    )
    assert closed_at is not None
    assert (
        confirmation.prior_disposition_id,
        confirmation.decided_by,
        confirmation.decided_by_type,
    ) == (escalation.id, senior, DecidedByType.STAFF)
    assert confirmation.policy_snapshot_id == escalation.policy_snapshot_id

    (confirmed,) = _events(tenant.tenant_id, TRUE_MATCH_CONFIRMED)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        party_id = s.get_one(ScreeningSubject, (tenant.tenant_id, subject_id)).party_id
    assert confirmed.data == {
        "match_id": str(match_id),
        "subject_id": str(subject_id),
        "party_id": str(party_id),
        "list_code": "MX_LPB",
        "on_true_match": {
            "action": "SUSPEND_IMMEDIATELY",
            "report": "MX_F41_ROI24",
            "notify_client": False,
        },
    }
    assert confirmed.policy_snapshot_id == confirmation.policy_snapshot_id
    assert (confirmed.subject, confirmed.key) == (
        f"screening_match/{match_id}",
        str(subject_id),
    )
    dispositioned = _events(tenant.tenant_id, MATCH_DISPOSITIONED)
    assert [e.data for e in dispositioned] == [
        {
            "match_id": str(match_id),
            "decision": "POSSIBLE_TRUE_MATCH",
            "disposition_id": str(escalation.id),
        },
        {
            "match_id": str(match_id),
            "decision": "TRUE_MATCH",
            "disposition_id": str(confirmation.id),
        },
    ]
    assert all(e.policy_snapshot_id is not None for e in dispositioned)


def test_the_escalating_user_cannot_confirm(
    engine: Engine, tenant: CreatedTenant, itf: None
):
    senior = add_staff(tenant, Role.SENIOR_ANALYST)
    subject_id, match_id = party_finding(tenant)
    _ = _decide(
        tenant, senior, Role.SENIOR_ANALYST, match_id, Decision.POSSIBLE_TRUE_MATCH
    )
    with pytest.raises(DispositionNotAllowedError) as caught:
        _ = _decide(tenant, senior, Role.SENIOR_ANALYST, match_id, Decision.TRUE_MATCH)
    assert (caught.value.status, caught.value.code) == (409, "DISPOSITION_NOT_ALLOWED")
    assert caught.value.blocked_reasons == ("SAME_REVIEWER",)
    assert _state(tenant, subject_id, match_id)[0] is MatchStatus.ESCALATED


def test_a_false_positive_whitelists_the_exact_content(
    engine: Engine, tenant: CreatedTenant, itf: None
):
    analyst = add_staff(tenant, Role.ANALYST)
    subject_id, match_id = party_finding(tenant)
    disposition = _decide(
        tenant, analyst, Role.ANALYST, match_id, Decision.FALSE_POSITIVE
    )
    status, closed_at, subject_status = _state(tenant, subject_id, match_id)
    assert (status, subject_status) == (MatchStatus.FALSE_POSITIVE, SubjectStatus.CLEAR)
    assert closed_at is not None
    assert disposition.prior_disposition_id is None
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        match = s.get_one(ScreeningMatch, (tenant.tenant_id, match_id))
        (row,) = s.scalars(select(ScreeningWhitelist)).all()
        assert (
            row.subject_id,
            row.list_code,
            row.source_entry_key,
            row.entry_content_hash,
            row.disposition_id,
            row.revoked_at,
        ) == (
            subject_id,
            match.list_code,
            match.source_entry_key,
            match.entry_content_hash,
            disposition.id,
            None,
        )


def test_a_second_review_clearance_needs_another_person(
    engine: Engine, tenant: CreatedTenant, itf: None
):
    analyst = add_staff(tenant, Role.ANALYST)
    mlro = add_staff(tenant, Role.MLRO)
    subject_id, match_id = party_finding(tenant)
    escalation = _decide(
        tenant, mlro, Role.MLRO, match_id, Decision.POSSIBLE_TRUE_MATCH
    )
    with pytest.raises(DispositionNotAllowedError) as caught:
        _ = _decide(tenant, analyst, Role.ANALYST, match_id, Decision.FALSE_POSITIVE)
    assert caught.value.blocked_reasons == ("ROLE_REQUIRED",)
    with pytest.raises(DispositionNotAllowedError) as caught:
        _ = _decide(tenant, mlro, Role.MLRO, match_id, Decision.FALSE_POSITIVE)
    assert caught.value.blocked_reasons == ("SAME_REVIEWER",)

    senior = add_staff(tenant, Role.SENIOR_ANALYST)
    clearance = _decide(
        tenant, senior, Role.SENIOR_ANALYST, match_id, Decision.FALSE_POSITIVE
    )
    assert clearance.prior_disposition_id == escalation.id
    assert _state(tenant, subject_id, match_id)[::2] == (
        MatchStatus.FALSE_POSITIVE,
        SubjectStatus.CLEAR,
    )


def test_delisted_needs_the_suggestion_and_a_senior_role(
    engine: Engine, tenant: CreatedTenant, itf: None
):
    analyst = add_staff(tenant, Role.ANALYST)
    senior = add_staff(tenant, Role.SENIOR_ANALYST)
    _, plain = party_finding(tenant)
    with pytest.raises(DispositionNotAllowedError) as caught:
        _ = _decide(tenant, senior, Role.SENIOR_ANALYST, plain, Decision.DELISTED)
    assert caught.value.blocked_reasons == ("NOT_DELISTED",)

    subject_id, suggested = party_finding(
        tenant, suggested=SuggestedDisposition.DELISTED
    )
    with pytest.raises(DispositionNotAllowedError) as caught:
        _ = _decide(tenant, analyst, Role.ANALYST, suggested, Decision.DELISTED)
    assert caught.value.blocked_reasons == ("ROLE_REQUIRED",)
    delisted = _decide(
        tenant, senior, Role.SENIOR_ANALYST, suggested, Decision.DELISTED
    )
    assert delisted.prior_disposition_id is None
    status, closed_at, subject_status = _state(tenant, subject_id, suggested)
    assert (status, subject_status) == (MatchStatus.DELISTED, SubjectStatus.CLEAR)
    assert closed_at is not None


def test_the_rationale_is_encrypted_and_never_audited(
    engine: Engine, tenant: CreatedTenant, itf: None, admin_engine: Engine
):
    analyst = add_staff(tenant, Role.ANALYST)
    _, match_id = party_finding(tenant)
    secret = "Homonimia confirmada con expediente 4471-ZQ"
    disposition = _decide(
        tenant,
        analyst,
        Role.ANALYST,
        match_id,
        Decision.FALSE_POSITIVE,
        rationale=f"  {secret}  ",
    )
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        row = s.get_one(MatchDisposition, (tenant.tenant_id, disposition.id))
        assert row.rationale.reveal(session=s) == secret
    assert leaked(everything_stored(admin_engine), secret.encode()) == []
    (audited,) = [
        e
        for e in audit_events(tenant.tenant_id)
        if e.action == "screening.match_dispositioned"
    ]
    assert (audited.entity_type, audited.entity_id, audited.actor_id) == (
        "screening_match",
        str(match_id),
        str(analyst),
    )
    assert audited.detail == {
        "decision": "FALSE_POSITIVE",
        "disposition_id": str(disposition.id),
    }


@pytest.mark.parametrize("rationale", ["corta", "   nueve   ", "x" * 2001])
def test_a_rationale_must_hold_ten_to_two_thousand_characters(
    engine: Engine, tenant: CreatedTenant, rationale: str
):
    analyst = add_staff(tenant, Role.ANALYST)
    _, match_id = party_finding(tenant)
    with pytest.raises(ValidationFailedError):
        _ = _decide(
            tenant, analyst, Role.ANALYST, match_id, Decision.FALSE_POSITIVE, rationale
        )


def test_api_clients_never_disposition(engine: Engine, tenant: CreatedTenant):
    _, match_id = party_finding(tenant)
    ctx = TenantContext(
        tenant_id=tenant.tenant_id,
        actor_type=ActorType.API_CLIENT,
        actor_id=str(uuid7()),
        roles=frozenset({Role.MLRO}),
    )
    with pytest.raises(ForbiddenError), tenant_session(ctx) as s:
        _ = disposition_match(
            s, match_id, decision=Decision.FALSE_POSITIVE, rationale=RATIONALE
        )


def test_superseded_is_not_a_staff_decision(engine: Engine, tenant: CreatedTenant):
    mlro = add_staff(tenant, Role.MLRO)
    _, match_id = party_finding(tenant)
    with pytest.raises(ValidationFailedError):
        _ = _decide(tenant, mlro, Role.MLRO, match_id, Decision.SUPERSEDED)


def test_an_unknown_finding_is_not_found(engine: Engine, tenant: CreatedTenant):
    mlro = add_staff(tenant, Role.MLRO)
    with pytest.raises(NotFoundError):
        _ = _decide(tenant, mlro, Role.MLRO, uuid7(), Decision.FALSE_POSITIVE)


def test_an_auditor_reads_but_never_decides(
    engine: Engine, tenant: CreatedTenant, itf: None
):
    auditor = add_staff(tenant, Role.AUDITOR_RO)
    _, match_id = party_finding(tenant)
    with pytest.raises(DispositionNotAllowedError) as caught:
        _ = _decide(tenant, auditor, Role.AUDITOR_RO, match_id, Decision.FALSE_POSITIVE)
    assert caught.value.blocked_reasons == (BlockedReason.ROLE_REQUIRED.value,)
