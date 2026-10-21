"""The monitoring module's composition root (SP-04 spec §6, §8.5, §14).

``wire_platform`` calls ``register_monitoring`` once per process, after
``register_rescreen`` and before plan 4b's ``register_rules``. It marks the
staff-only monitoring events, registers the PII tags of the nested input models
(the pure core cannot), routes ``monitoring.ingest_file`` to ``IngestFileJob``
and ``monitoring.process`` to ``ProcessJob``. Plans 4a (A9) and 4c (C5, C7, C8)
append their handlers.
Calling it again replaces every registration, so composing twice is harmless.
"""

from xolotl.modules.monitoring.events import STAFF_ONLY_EVENT_TYPES
from xolotl.modules.monitoring.kinds import INGEST_FILE_KIND, PROCESS_KIND
from xolotl.modules.monitoring.process.job import ProcessJob
from xolotl.modules.monitoring.txn.files import IngestFileJob
from xolotl.modules.monitoring.txn.schema import CryptoContext, DeviceContext
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyIdentifier,
    CounterpartyInput,
)
from xolotl.platform.config import Settings
from xolotl.platform.db import SessionFactory
from xolotl.platform.events import register_staff_only
from xolotl.platform.jobs.queue import PostgresJobQueue, register_job_handler
from xolotl.platform.logging import register_pii_model
from xolotl.platform.storage import ObjectStore


def register_monitoring(
    *,
    session_factory: SessionFactory,
    settings: Settings,
    queue: PostgresJobQueue,
    store: ObjectStore,
) -> None:
    """Mark the staff-only events, tag the nested PII and wire the two jobs."""
    for event_type in STAFF_ONLY_EVENT_TYPES:
        register_staff_only(event_type)
    # A nested model's PII tags redact only once registered.
    for model in (
        CounterpartyInput,
        CounterpartyIdentifier,
        DeviceContext,
        CryptoContext,
    ):
        register_pii_model(model)
    ingest = IngestFileJob(
        session_factory=session_factory, store=store, queue=queue, settings=settings
    )
    register_job_handler(INGEST_FILE_KIND, ingest.run)
    process = ProcessJob(
        session_factory=session_factory, queue=queue, settings=settings
    )
    register_job_handler(PROCESS_KIND, process.run)
