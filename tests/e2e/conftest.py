"""Fixtures of the end-to-end tests: draining the bus and the queue by hand."""

from sqlalchemy import text

from xolotl.platform.db import get_admin_engine
from xolotl.platform.services import PlatformServices

MAX_ROUNDS = 200


def _age_outbox() -> None:
    # The dispatcher leaves an event alone for a one-second grace window; the
    # demo does not wait for it.
    with get_admin_engine().begin() as conn:
        _ = conn.execute(
            text(
                """
                UPDATE outbox SET created_at = created_at - interval '1 minute'
                WHERE status = 'PENDING'
                """
            )
        )


def drain(services: PlatformServices) -> int:
    """Dispatch events and run jobs until a round does nothing.

    Jobs publish events and events enqueue jobs, so one round is a dispatch of what
    the outbox holds and a run of what is due. A round that did nothing ends it; a
    demo that is still busy after ``MAX_ROUNDS`` rounds fails.

    Returns:
        How many events were dispatched and jobs ran in all.

    Raises:
        AssertionError: When ``MAX_ROUNDS`` rounds all did something.

    """
    total = 0
    for _ in range(MAX_ROUNDS):
        _age_outbox()
        dispatched = services.bus.dispatch_pending()
        ran = services.job_runner.run_once()
        total += dispatched + ran
        if dispatched == 0 and ran == 0:
            return total
    msg = f"drain: the bus and the queue are still busy after {MAX_ROUNDS} rounds"
    raise AssertionError(msg)
