"""The rules processing hook, its declarations and staff-only events (B7)."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import system_ctx
from tests.modules.monitoring.rules.hook_support import (
    CASH,
    TIER_N1,
    Stage,
    add_relationship,
    age_outbox,
    deposit,
    feed,
    hits,
    ingest,
    local,
    statuses,
)
from tests.platform.webhooks_support import deliveries, make_endpoint
from xolotl.modules.monitoring.aggregates.declarations import AggregateDeclaration
from xolotl.modules.monitoring.aggregates.models import AggDeclaration
from xolotl.modules.monitoring.enrich.policy import SnapshotRef, snapshot_for
from xolotl.modules.monitoring.events import (
    RULES_STAFF_ONLY_EVENT_TYPES,
    TIER_LIMIT_REACHED,
)
from xolotl.modules.monitoring.process.hooks import (
    ProcessingContext,
    processing_hooks,
    register_processing_hook,
    reset_processing_hooks,
)
from xolotl.modules.monitoring.process.job import process_party
from xolotl.modules.monitoring.rules.assignments import active_rules
from xolotl.modules.monitoring.rules.hook import RulesHook
from xolotl.modules.monitoring.rules.wiring import register_rules
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.events import is_staff_only
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.jobs.queue import PostgresJobQueue

REF = "cust-n3"


def _near(number: int, day: int, amount: str) -> dict[str, object]:
    return deposit(f"dep-{number}", local(day), amount, customer_ref=REF)


class BoomError(RuntimeError):
    pass


@dataclass
class ExplodingHook:
    """Raises after the rules hook has written its hit."""

    name: str = "boom"
    calls: int = 0

    def declarations(
        self, s: Session, snapshot: SnapshotRef, *, on: date, product: str | None
    ) -> Sequence[AggregateDeclaration]:
        _ = (s, snapshot, on, product)
        self.calls += 1
        return ()

    def evaluate(
        self, s: Session, txn: EnrichedTransaction, ctx: ProcessingContext
    ) -> None:
        _ = (s, txn, ctx)
        self.calls += 1
        raise BoomError


def test_the_hook_runs_inside_the_processing_transaction(stage: Stage):
    _ = add_relationship(stage.tenant, REF, "N3")
    _ = feed(stage, [_near(1, 7, "3600.00"), _near(2, 8, "3700.00")])
    register_processing_hook(ExplodingHook())
    assert [hook.name for hook in processing_hooks()][-1] == "boom"
    [third] = ingest(stage, [_near(3, 9, "3800.00")])
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        party_id = s.scalars(select(Transaction.party_id)).first()
    assert party_id is not None

    with pytest.raises(BoomError):
        _ = process_party(
            get_sessionmaker(),
            stage.tenant_id,
            party_id,
            queue=stage.services.queue,
            job=None,
            settings=get_settings(),
            actor="cli:test",
        )

    assert statuses(stage)[third] == "RECEIVED"
    assert hits(stage, CASH) == []


def test_the_hook_declares_the_active_rules_aggregates(stage: Stage):
    _ = add_relationship(stage.tenant, REF, "N3")
    _ = feed(stage, [_near(1, 7, "3600.00")])

    with tenant_session(system_ctx(stage.tenant_id)) as s:
        ruleset = active_rules(s, on=local(7).date(), product="EMONEY")
        declared = RulesHook().declarations(
            s,
            snapshot_for(s, on=local(7).date(), product="EMONEY", cache={}),
            on=local(7).date(),
            product="EMONEY",
        )
        stored = set(s.scalars(select(AggDeclaration.agg_id)))
    assert {rule.rule_code for rule in ruleset.rules} == {
        CASH,
        "COUNTERPARTY_TRUE_MATCH",
        TIER_N1,
    }
    assert tuple(declared) == ruleset.declarations
    expected = {item.agg_id() for item in ruleset.declarations}
    assert len(expected) == 3
    assert expected <= stored


def _delivered_types(tenant_id: UUID) -> set[str]:
    delivered = {row.event_id for row in deliveries(tenant_id)}
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.scalars(select(OutboxRow).where(OutboxRow.id.in_(delivered)))
        return {row.type for row in rows}


def test_a_wildcard_endpoint_gets_the_limit_event_but_never_a_hit_event(
    stage: Stage,
):
    _ = make_endpoint(stage.tenant_id, event_types=["*"])
    # An N1 customer: the near deposits are a structuring hit and an overrun.
    _ = add_relationship(stage.tenant, REF, "N1")
    _ = feed(
        stage,
        [_near(1, 7, "3600.00"), _near(2, 8, "3700.00"), _near(3, 9, "3800.00")],
    )
    assert hits(stage, CASH)
    assert hits(stage, TIER_N1)

    age_outbox(stage.tenant_id)
    while stage.services.bus.dispatch_pending():
        pass

    types = _delivered_types(stage.tenant_id)
    assert TIER_LIMIT_REACHED in types
    assert not types & set(RULES_STAFF_ONLY_EVENT_TYPES)
    assert not [t for t in types if t.startswith("monitoring.rule_hit")]


@pytest.fixture
def no_hooks() -> Iterator[None]:
    reset_processing_hooks()
    yield
    reset_processing_hooks()


@pytest.mark.usefixtures("no_hooks")
def test_register_rules_marks_every_staff_only_type(queue: PostgresJobQueue):
    register_rules(queue=queue, screening_status=None)
    for event_type in RULES_STAFF_ONLY_EVENT_TYPES:
        assert is_staff_only(event_type)
    assert not is_staff_only(TIER_LIMIT_REACHED)
    assert "rules" in [hook.name for hook in processing_hooks()]
