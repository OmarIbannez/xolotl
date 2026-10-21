"""One ``monitoring.process`` job per party (SP-04 spec §6.1 step 4, §8.5)."""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from xolotl.modules.monitoring.kinds import PROCESS_KIND, process_key
from xolotl.platform.jobs.queue import PostgresJobQueue


def enqueue_processing(
    session: Session, queue: PostgresJobQueue, party_ids: Iterable[UUID]
) -> int:
    """Enqueue each party's processing job in the caller's transaction.

    The queue's dedup index drops an enqueue while a job for the party is already
    pending or running; the sweep covers a running job past its last check.

    Returns:
        How many jobs were created.

    """
    created = 0
    for party_id in sorted(set(party_ids)):
        job_id = queue.enqueue(
            session,
            kind=PROCESS_KIND,
            key=process_key(party_id),
            payload={"party_id": str(party_id)},
        )
        created += job_id is not None
    return created
