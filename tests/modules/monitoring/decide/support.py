"""The stage and helpers of the decide tests (plan 4c C5).

The stage is B7's tenant A (both packs by product, the library loaded,
``CASH_STRUCTURING``, ``COUNTERPARTY_TRUE_MATCH`` and ``TIER_N1_MONTHLY_OPS`` active
from 30 June 2026) plus ``TIER_N2_MONTHLY_OPS``, an N1 and an N2 relationship and an
API key holding ``transactions:write`` and ``transactions:read`` (the product owner
kept the decide route on ``transactions:write``, 2026-10-21). Decisions are asked
for on Monday 14 December 2026 at 12:00 Mexico City, when the N1 limit is 750 UDI
at the UDI of 30 November (8.602000).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, func, select, update

from tests.helpers import system_ctx
from tests.modules.monitoring.rules.hook_support import (
    ACTIVATION,
    EFFECTIVE_FROM,
    TRUE_MATCH,
    Stage,
    add_relationship,
    age_outbox,
    build_stage,
    deposit,
    feed,
    local,
    run_backfills,
)
from tests.modules.monitoring.rules.lifecycle_support import activated, freeze
from tests.modules.party.route_support import machine_headers
from xolotl.modules.monitoring.decide.models import TxnDecision
from xolotl.modules.monitoring.rules.assignments import clear_rule_cache
from xolotl.modules.monitoring.rules.models import MonitoringRuleVersion
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.db import tenant_session

DECIDE = "/v1/transactions/decide"
DECISIONS = "/v1/monitoring/decisions"
TIER_N2 = "TIER_N2_MONTHLY_OPS"
N1 = "cust-n1"
N2 = "cust-n2"
NOW = local(14, 12)
# Long enough that a slow machine never answers with the fail mode by accident.
TEST_BUDGET_MS = 5000
# 350, 100 and 50 UDI at the UDI of 30 November 2026 (8.602000).
UDI_350 = "3010.70"
UDI_100 = "860.20"
UDI_50 = "430.10"


@dataclass(frozen=True, slots=True)
class DecideStage:
    """B7's stage with ``TIER_N2_MONTHLY_OPS`` too, two customers and a key."""

    stage: Stage
    n1: UUID
    n2: UUID
    writer: dict[str, str]

    @property
    def tenant(self) -> CreatedTenant:
        return self.stage.tenant

    @property
    def tenant_id(self) -> UUID:
        return self.stage.tenant_id


def build_decide_stage(
    tenant: CreatedTenant, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> DecideStage:
    """Build the stage.

    Returns:
        The stage.

    """
    stage = build_stage(tenant, admin_engine, monkeypatch)
    with monkeypatch.context() as patched:
        freeze(patched, ACTIVATION)
        version = activated(
            tenant,
            stage.approver,
            stage.services.queue,
            TIER_N2,
            effective_from=EFFECTIVE_FROM,
        )
    stage.versions[TIER_N2] = version.id
    _ = run_backfills(stage.services)
    n1 = add_relationship(tenant, N1, "N1")
    n2 = add_relationship(tenant, N2, "N2")
    writer = machine_headers(tenant, "transactions:write", "transactions:read")
    return DecideStage(stage, n1, n2, writer)


def mark_sync(admin_engine: Engine, stage: DecideStage, code: str = TRUE_MATCH) -> None:
    """Make the active version of ``code`` a sync rule (no tenant may tune it)."""
    with admin_engine.begin() as conn:
        _ = conn.execute(
            update(MonitoringRuleVersion)
            .where(MonitoringRuleVersion.id == stage.stage.versions[code])
            .values(sync=True)
        )
    clear_rule_cache()


def candidate(
    external_id: str,
    amount: str,
    *,
    customer_ref: str = N1,
    at: datetime = NOW,
    **overrides: object,
) -> dict[str, object]:
    """Build an MXN cash deposit about to be made, five minutes before ``at``.

    Returns:
        The item as a client sends it to the decide route.

    """
    item = deposit(
        external_id,
        at - timedelta(minutes=5),
        amount,
        customer_ref=customer_ref,
        currency="MXN",
    )
    item.update(overrides)
    return item


def ask(
    client: TestClient,
    headers: Mapping[str, str],
    item: Mapping[str, object],
    *,
    key: str | None = None,
    at: datetime = NOW,
) -> Response:
    """Post ``item`` to the decide route with the clock frozen at ``at``.

    Returns:
        The response.

    """
    sent = dict(headers)
    sent["Idempotency-Key"] = key or f"decide-{item['external_id']}"
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(clock, "now", lambda: at)
        return client.post(DECIDE, json=dict(item), headers=sent)


def month_to_date(stage: DecideStage, *pesos: str) -> list[UUID]:
    """Deposit and process ``pesos`` for the N1 customer on 7, 8, ... December.

    Returns:
        The ids of the processed rows.

    """
    return feed(
        stage.stage,
        [
            deposit(f"mtd-{day}", local(day), amount, customer_ref=N1, currency="MXN")
            for day, amount in enumerate(pesos, start=7)
        ],
    )


def decision_row(tenant_id: UUID, decision_id: str | UUID) -> TxnDecision | None:
    with tenant_session(system_ctx(tenant_id)) as s:
        return s.scalar(
            select(TxnDecision).where(TxnDecision.id == UUID(str(decision_id)))
        )


def decision_count(tenant_id: UUID) -> int:
    with tenant_session(system_ctx(tenant_id)) as s:
        return s.scalar(select(func.count()).select_from(TxnDecision)) or 0


def dispatch_all(stage: DecideStage) -> int:
    """Hand every pending event to its subscribers, a key's events in turn.

    The bus hands out one event per key at a time, so it is asked until it has
    nothing left.

    Returns:
        How many events were handled.

    """
    age_outbox(stage.tenant_id)
    handled = 0
    while step := stage.stage.services.bus.dispatch_pending():
        handled += step
    return handled
