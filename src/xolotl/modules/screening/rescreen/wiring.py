"""The re-screen composition: job handlers and catalog-event consumers (spec §10)."""

from xolotl.platform.jobs.queue import PostgresJobQueue


def register_rescreen(*, queue: PostgresJobQueue) -> None:
    """Register the re-screen job handlers and catalog-event consumers.

    ``wire_platform`` calls it right after ``register_screening``. The list-delta,
    private-list, periodic and re-index jobs and their consumers register here as
    they are built; none exists yet, so the queue is not used.
    """
    _ = queue
