"""The screening module's composition root (SP-03 spec §8.1, §10.1 and §11).

``wire_platform`` calls ``register_screening`` once per process. It marks the
staff-only screening events, so no webhook carries them (FR-SC-013), installs the
process's index holder, and subscribes consumer ``screening`` to the party events
that re-index and re-screen a party, and to ``party.purged.v1``, which forgets a
purged party's name. It installs the first-sight queue that ``resolve`` enqueues a
new counterparty's screen on, and handles that job (SP-04 spec §11.2). It starts no
thread: ``build_app`` and ``build_worker`` start the warm-up themselves.
"""

from xolotl.modules.party.events import PARTY_PURGED
from xolotl.modules.screening.counterparty.first_sight import (
    FIRST_SIGHT_KIND,
    set_first_sight_queue,
)
from xolotl.modules.screening.counterparty.runs import handle_first_sight
from xolotl.modules.screening.engine.holder import (
    ScreeningIndexHolder,
    set_index_holder,
)
from xolotl.modules.screening.events import STAFF_ONLY_EVENT_TYPES
from xolotl.modules.screening.rescreen.party_events import (
    PARTY_EVENT_MODES,
    SCREEN_PARTY_KIND,
    SCREENING_CONSUMER,
    make_party_event_handler,
    screen_party_job,
)
from xolotl.modules.screening.rescreen.purge import on_party_purged
from xolotl.platform.config import Settings
from xolotl.platform.db import SessionFactory
from xolotl.platform.events import register_staff_only, register_subscriber
from xolotl.platform.jobs.queue import PostgresJobQueue, register_job_handler


def register_screening(
    *, session_factory: SessionFactory, settings: Settings, queue: PostgresJobQueue
) -> ScreeningIndexHolder:
    """Mark the staff-only events, install a new holder and wire the party events.

    Calling it again replaces the holder, the subscriptions and the job handler, so
    composing twice is harmless.

    Returns:
        The installed holder, not yet warmed.

    """
    for event_type in STAFF_ONLY_EVENT_TYPES:
        register_staff_only(event_type)
    holder = ScreeningIndexHolder(
        session_factory,
        refresh_seconds=settings.screening_index_refresh_seconds,
        private_shards=settings.screening_private_shards,
    )
    set_index_holder(holder)
    handler = make_party_event_handler(queue)
    for event_type in PARTY_EVENT_MODES:
        register_subscriber(event_type, handler, consumer=SCREENING_CONSUMER)
    register_subscriber(PARTY_PURGED, on_party_purged, consumer=SCREENING_CONSUMER)
    register_job_handler(SCREEN_PARTY_KIND, screen_party_job)
    set_first_sight_queue(queue)
    register_job_handler(FIRST_SIGHT_KIND, handle_first_sight)
    return holder
