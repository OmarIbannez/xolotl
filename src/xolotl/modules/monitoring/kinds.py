"""The job kinds of the monitoring module and their dedup keys (SP-04 spec §8.5)."""

from uuid import UUID

PROCESS_KIND = "monitoring.process"
INGEST_FILE_KIND = "monitoring.ingest_file"
SWEEP_KIND = "monitoring.sweep"
AGG_BACKFILL_KIND = "monitoring.agg_backfill"
BACKFILL_KIND = "monitoring.backfill"
PARTITIONS_KIND = "monitoring.partitions"


def process_key(party_id: UUID) -> str:
    """Key one party's processing job: one pending or running job per party.

    Returns:
        ``process:<party_id>``.

    """
    return f"process:{party_id}"
