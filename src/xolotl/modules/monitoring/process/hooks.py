"""The processing hooks: what runs on each row after its aggregates (spec §8.5).

A hook reports the aggregate declarations it reads under a policy snapshot and
evaluates a transaction inside the row's database transaction, after
``store.add``, so whatever it writes commits or rolls back with the row. Plan 4b
registers its rules (B7) and captures (B10) hooks from ``register_rules``; plan
4a registers none (reconciliation finding 4).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.declarations import AggregateDeclaration
from xolotl.modules.monitoring.aggregates.store import AggregateStore
from xolotl.modules.monitoring.enrich.policy import SnapshotRef
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction
from xolotl.platform.calendar import Holidays
from xolotl.platform.jobs.queue import PostgresJobQueue
from xolotl.platform.units import UnitService


@dataclass(frozen=True, slots=True)
class ProcessingContext:
    """What a hook may read while it evaluates one row.

    ``store`` already includes the row; ``declarations`` are the ones the store
    maintained for it; ``late`` is the row's late flag.
    """

    txn: EnrichedTransaction
    row: Transaction
    store: AggregateStore
    declarations: Sequence[AggregateDeclaration]
    snapshot: SnapshotRef
    units: UnitService
    holidays: Holidays
    late: bool
    queue: PostgresJobQueue


class ProcessingHook(Protocol):
    """A step of processing that runs after the row's aggregates are updated."""

    name: str

    def declarations(
        self, s: Session, snapshot: SnapshotRef, *, on: date, product: str | None
    ) -> Sequence[AggregateDeclaration]:
        """Name the aggregates this hook reads under ``snapshot``."""
        ...

    def evaluate(
        self, s: Session, txn: EnrichedTransaction, ctx: ProcessingContext
    ) -> None:
        """Evaluate one transaction in the row's database transaction."""
        ...


_HOOKS: list[ProcessingHook] = []


def register_processing_hook(hook: ProcessingHook) -> None:
    """Append ``hook``; a hook of the same name registered before is replaced.

    A replaced hook keeps its place in the order, so composing twice is harmless.
    """
    for index, registered in enumerate(_HOOKS):
        if registered.name == hook.name:
            _HOOKS[index] = hook
            return
    _HOOKS.append(hook)


def processing_hooks() -> tuple[ProcessingHook, ...]:
    """List the registered hooks.

    Returns:
        The hooks, in registration order.

    """
    return tuple(_HOOKS)


def reset_processing_hooks() -> None:
    """Forget every registered hook (tests only)."""
    _HOOKS.clear()
