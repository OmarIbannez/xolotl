"""Re-screen a party when its data changes (SP-03 spec §10.1, FR-SC-001).

Consumer ``screening`` subscribes to ``party.created.v1``, ``party.updated.v1`` and
``party.identifier_added.v1``. In the dispatcher's transaction it re-indexes the
party's names and enqueues one ``screening.screen_party`` job per event (key
``screen_party:<party_id>:<event_id>``, so a redelivery enqueues nothing new). The
job screens the party with mode ``NEW_SUBJECT`` for a creation and ``DATA_CHANGE``
otherwise, unless a full run of the subject started after the event: a synchronous
check already covered it. A run that scored only part of the lists (a ``LIST_DELTA``
or ``PRIVATE_LIST`` run, or any run ``restricted_to_delta``) covers nothing of the
party's change, so it never suppresses the job.
"""

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.modules.party.enums import PartyStatus
from xolotl.modules.party.events import IDENTIFIER_ADDED, PARTY_CREATED, PARTY_UPDATED
from xolotl.modules.party.service import get_party
from xolotl.modules.screening.engine.enums import RunMode, TriggerType
from xolotl.modules.screening.engine.models import ScreeningRun
from xolotl.modules.screening.engine.names import index_party_names
from xolotl.modules.screening.engine.screen import Trigger, screen
from xolotl.modules.screening.engine.subjects import subject_for_party
from xolotl.platform.events import Event, Handler
from xolotl.platform.jobs.queue import Job, PostgresJobQueue

SCREENING_CONSUMER = "screening"
SCREEN_PARTY_KIND = "screening.screen_party"
PARTY_EVENT_MODES: Mapping[str, RunMode] = MappingProxyType({
    PARTY_CREATED: RunMode.NEW_SUBJECT,
    PARTY_UPDATED: RunMode.DATA_CHANGE,
    IDENTIFIER_ADDED: RunMode.DATA_CHANGE,
})
# Runs that score only a batch's entries, never the party against every list.
PARTIAL_RUN_MODES = (RunMode.LIST_DELTA, RunMode.PRIVATE_LIST)


def make_party_event_handler(queue: PostgresJobQueue) -> Handler:
    """Build the ``screening`` consumer's handler of the party events.

    Returns:
        A handler that re-indexes the party's names and enqueues its screen.

    """

    def on_party_event(s: Session, event: Event) -> None:
        party_id = UUID(str(event.data["party_id"]))
        _ = index_party_names(s, party_id)
        _ = queue.enqueue(
            s,
            kind=SCREEN_PARTY_KIND,
            key=f"screen_party:{party_id}:{event.id}",
            payload={
                "party_id": str(party_id),
                "event_id": str(event.id),
                "event_time": event.time.isoformat(),
                "mode": PARTY_EVENT_MODES[event.type].value,
            },
        )

    return on_party_event


def screen_party_job(s: Session, job: Job) -> None:
    """Screen the party of a ``screening.screen_party`` job, once per event.

    A purged party is not screened, and neither is one a full run started after
    the event already covered. A tenant without a policy fails the job, which
    retries.

    The guard compares the event's time, taken from the application clock, with the
    run's ``created_at``, which the database clock stamps. The MVP runs one
    application container next to its Postgres and assumes the two clocks agree; a
    skew only re-screens a party once more or, at worst, skips a re-screen that a
    run within the skew already covered.
    """
    payload = job.payload
    party_id = UUID(str(payload["party_id"]))
    if get_party(s, party_id).status is PartyStatus.PURGED:
        return
    subject = subject_for_party(s, party_id, for_update=True)
    event_time = datetime.fromisoformat(str(payload["event_time"]))
    later = s.scalar(
        select(ScreeningRun.id)
        .where(
            ScreeningRun.subject_id == subject.id,
            ScreeningRun.created_at > event_time,
            ScreeningRun.mode.not_in(PARTIAL_RUN_MODES),
            ScreeningRun.restricted_to_delta.is_(False),
        )
        .limit(1)
    )
    if later is not None:
        return
    _ = screen(
        s,
        subject,
        mode=RunMode(str(payload["mode"])),
        trigger=Trigger(TriggerType.EVENT, str(payload["event_id"])),
    )
