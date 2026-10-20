"""The re-screen composition: job handlers and catalog-event consumers (spec §10)."""

from xolotl.modules.screening.events import (
    LISTS_VERSION_PUBLISHED,
    PRIVATE_LIST_VERSION_ACTIVATED,
)
from xolotl.modules.screening.rescreen.list_delta import (
    LIST_DELTA_KIND,
    handle_list_delta,
    on_list_version_published,
)
from xolotl.modules.screening.rescreen.party_events import SCREENING_CONSUMER
from xolotl.modules.screening.rescreen.walkers import (
    PERIODIC_RESCREEN_KIND,
    PRIVATE_LIST_RESCREEN_KIND,
    REINDEX_KIND,
    handle_periodic_rescreen,
    handle_private_list_rescreen,
    handle_reindex,
    on_private_list_version_activated,
)
from xolotl.platform.events import register_subscriber
from xolotl.platform.jobs.queue import PostgresJobQueue, register_job_handler


def register_rescreen(*, queue: PostgresJobQueue) -> None:
    """Register the re-screen job handlers and catalog-event consumers.

    ``wire_platform`` calls it right after ``register_screening``. Registering again
    replaces the handlers and the subscriptions, so composing twice is harmless.
    """
    register_job_handler(LIST_DELTA_KIND, handle_list_delta(queue))
    register_job_handler(
        PRIVATE_LIST_RESCREEN_KIND, handle_private_list_rescreen(queue)
    )
    register_job_handler(PERIODIC_RESCREEN_KIND, handle_periodic_rescreen(queue))
    register_job_handler(REINDEX_KIND, handle_reindex(queue))
    register_subscriber(
        LISTS_VERSION_PUBLISHED,
        on_list_version_published(queue),
        consumer=SCREENING_CONSUMER,
    )
    register_subscriber(
        PRIVATE_LIST_VERSION_ACTIVATED,
        on_private_list_version_activated(queue),
        consumer=SCREENING_CONSUMER,
    )
