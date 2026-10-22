"""The captures processing hook: reportable events on every processed row (§8.5).

``CapturesHook`` runs after the rules hook, inside the row's database transaction
and under the party's processing lock, so the events, groups and period rows it
writes commit or roll back with the row. The captures are the transaction's
policy snapshot's, never a tenant rule version's (spec Decision 2).
"""

from collections.abc import Sequence
from datetime import date

from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.declarations import AggregateDeclaration
from xolotl.modules.monitoring.captures.definitions import capture_definitions
from xolotl.modules.monitoring.captures.events import capture_transaction
from xolotl.modules.monitoring.enrich.policy import SnapshotRef
from xolotl.modules.monitoring.process.hooks import ProcessingContext
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction


class CapturesHook:
    """Plan 4a's ``ProcessingHook`` for the snapshot's captures."""

    name: str = "captures"

    @staticmethod
    def declarations(
        s: Session, snapshot: SnapshotRef, *, on: date, product: str | None
    ) -> Sequence[AggregateDeclaration]:
        """Name the accumulators of the snapshot's captures.

        Returns:
            One ``ACCUM`` declaration per ``ACCUMULATION`` capture.

        """
        del s, on, product
        return tuple(
            declaration
            for definition in capture_definitions(snapshot.policy)
            for declaration in definition.declarations()
        )

    @staticmethod
    def evaluate(s: Session, txn: EnrichedTransaction, ctx: ProcessingContext) -> None:
        """Capture the row for every capture of its snapshot."""
        _ = capture_transaction(
            s,
            capture_definitions(ctx.snapshot.policy),
            txn,
            store=ctx.store,
            units=ctx.units,
            holidays=ctx.holidays,
            late=ctx.late,
        )
