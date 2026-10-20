"""Delisting suggestions on findings whose entry left the list (SP-03 spec §10.2; F1).

A finding still awaiting or holding a decision (``OPEN``, ``ESCALATED``,
``TRUE_MATCH``) gets ``suggested_disposition = DELISTED`` and one staff-only
``screening.match_delisting_suggested.v1``, ever. Closed findings are left alone.
"""

from uuid import UUID

from sqlalchemy import select, text

from tests.helpers import staff_session, system_ctx
from tests.modules.screening.rescreen.conftest import job_session
from tests.modules.screening.review.support import ANA, add_finding
from tests.platform.webhooks_support import Stack, deliveries, make_endpoint
from xolotl.modules.party import service
from xolotl.modules.screening.engine.enums import MatchStatus, SuggestedDisposition
from xolotl.modules.screening.engine.models import ScreeningMatch
from xolotl.modules.screening.engine.subjects import subject_for_party
from xolotl.modules.screening.events import MATCH_DELISTING_SUGGESTED
from xolotl.modules.screening.rescreen.delisting import suggest_delisting
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import tenant_session
from xolotl.platform.events import Event
from xolotl.platform.events.event import event_from_row
from xolotl.platform.events.models import OutboxRow

LPB = "MX_LPB"


def _findings(
    tenant: CreatedTenant, *statuses: MatchStatus, list_code: str = LPB
) -> list[tuple[UUID, str]]:
    """Add one finding per status to a new party.

    Returns:
        ``(match id, source entry key)`` per finding, in the order given.

    """
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ANA)
        subject = subject_for_party(s, party.id)
        matches = [
            add_finding(s, subject, status=status, list_code=list_code)
            for status in statuses
        ]
        return [(m.id, m.source_entry_key) for m in matches]


def _suggested(tenant: CreatedTenant) -> dict[UUID, SuggestedDisposition | None]:
    with job_session(tenant) as s:
        return dict(
            s.execute(
                select(ScreeningMatch.id, ScreeningMatch.suggested_disposition)
            ).all()
        )


def _events(tenant: CreatedTenant) -> list[Event]:
    with job_session(tenant) as s:
        rows = s.scalars(
            select(OutboxRow).where(OutboxRow.type == MATCH_DELISTING_SUGGESTED)
        )
        return [event_from_row(row) for row in rows]


def test_open_escalated_and_true_match_findings_get_the_suggestion(
    tenant: CreatedTenant,
):
    statuses = (
        MatchStatus.OPEN,
        MatchStatus.ESCALATED,
        MatchStatus.TRUE_MATCH,
        MatchStatus.FALSE_POSITIVE,
        MatchStatus.DELISTED,
        MatchStatus.SUPERSEDED,
    )
    findings = _findings(tenant, *statuses)
    # A key of another list names another entry.
    [(other_id, other_key)] = _findings(
        tenant, MatchStatus.OPEN, list_code="INTERNAL_PEP"
    )
    keys = [key for _, key in findings] + [other_key]

    with job_session(tenant) as s:
        suggested = suggest_delisting(s, list_code=LPB, entry_keys=keys)

    awaiting = {match_id for match_id, _ in findings[:3]}
    assert set(suggested) == awaiting
    stored = _suggested(tenant)
    assert {m for m, v in stored.items() if v is SuggestedDisposition.DELISTED} == (
        awaiting
    )
    assert stored[other_id] is None
    events = _events(tenant)
    assert {str(e.data["match_id"]) for e in events} == {str(m) for m in awaiting}
    assert len(events) == len(awaiting)
    assert {str(e.data["list_code"]) for e in events} == {LPB}
    assert all(set(e.data) == {"match_id", "list_code"} for e in events)


def test_no_entry_keys_change_nothing(tenant: CreatedTenant):
    _ = _findings(tenant, MatchStatus.OPEN)

    with job_session(tenant) as s:
        assert suggest_delisting(s, list_code=LPB, entry_keys=()) == []

    assert set(_suggested(tenant).values()) == {None}
    assert _events(tenant) == []


def test_a_second_suggestion_publishes_nothing(tenant: CreatedTenant):
    [(match_id, key)] = _findings(tenant, MatchStatus.OPEN)

    with job_session(tenant) as s:
        first = suggest_delisting(s, list_code=LPB, entry_keys=[key])
    with job_session(tenant) as s:
        second = suggest_delisting(s, list_code=LPB, entry_keys=[key])

    assert (first, second) == ([match_id], [])
    assert len(_events(tenant)) == 1


def test_the_suggestion_event_is_staff_only(
    tenant: CreatedTenant, webhook_stack: Stack
):
    [(_, key)] = _findings(tenant, MatchStatus.OPEN)
    webhook_stack.settle(tenant.tenant_id)
    _ = make_endpoint(tenant.tenant_id, event_types=["*"])

    with job_session(tenant) as s:
        assert suggest_delisting(s, list_code=LPB, entry_keys=[key])
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        _ = s.execute(
            text("UPDATE outbox SET created_at = created_at - interval '1 minute'")
        )
    while webhook_stack.bus.dispatch_pending():
        pass

    assert len(_events(tenant)) == 1
    assert deliveries(tenant.tenant_id) == []
