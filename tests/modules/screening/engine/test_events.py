"""The screening event envelope and the staff-only list (spec §11)."""

from sqlalchemy import Engine, select

from tests.helpers import system_ctx
from xolotl.modules.screening import events
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import tenant_session
from xolotl.platform.events.event import event_from_row
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.ids import uuid7


def test_only_run_and_status_events_may_reach_a_webhook():
    assert set(events.STAFF_ONLY_EVENT_TYPES) == {
        "private_list.version_activated.v1",
        "screening.match_created.v1",
        "screening.match_dispositioned.v1",
        "screening.true_match_confirmed.v1",
        "screening.match_delisting_suggested.v1",
    }
    assert events.RUN_COMPLETED not in events.STAFF_ONLY_EVENT_TYPES
    assert events.STATUS_CHANGED not in events.STAFF_ONLY_EVENT_TYPES


def test_an_event_is_keyed_as_asked_and_names_its_entity(
    engine: Engine, tenant: CreatedTenant
):
    match_id, subject_id, snapshot_id = uuid7(), uuid7(), uuid7()
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        published = events.publish_screening_event(
            s,
            events.MATCH_CREATED,
            entity="screening_match",
            entity_id=match_id,
            key=str(subject_id),
            data={"match_id": str(match_id)},
            policy_snapshot_id=snapshot_id,
        )
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        rows = s.scalars(
            select(OutboxRow).where(OutboxRow.type == events.MATCH_CREATED)
        ).all()
        stored = [event_from_row(row) for row in rows]
    assert [event.id for event in stored] == [published.id]
    event = stored[0]
    assert event.source == "xolotl.screening"
    assert event.subject == f"screening_match/{match_id}"
    assert event.key == str(subject_id)
    assert event.policy_snapshot_id == snapshot_id
    assert event.data == {"match_id": str(match_id)}
