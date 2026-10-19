"""Registry of row factories used by the behavioural isolation harness.

A factory inserts exactly one row of its table for the given tenant (plus any
parent rows it needs) and flushes. The harness in ``tests/isolation`` fails
when a tenant table has no factory, so every new table must register one.
"""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

IsolationFactory = Callable[[Session, UUID], None]

_FACTORIES: dict[str, IsolationFactory] = {}


def register_isolation_factory(table: str, factory: IsolationFactory) -> None:
    _FACTORIES[table] = factory


def isolation_factories() -> dict[str, IsolationFactory]:
    return dict(_FACTORIES)
