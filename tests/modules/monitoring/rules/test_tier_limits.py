"""The tier-limit hit and its one tenant-facing notice (spec §12.2, B7).

The N1 limit is 750 UDI of monthly deposits at ``LAST_DAY_PREV_MONTH``: in
December 2026 that is the UDI of 30 November (8.602000 in the fixture), so two
deposits of 3,300 MXN (about 383.6 UDI each) cross it on the second.
"""

from uuid import UUID

import pytest
from sqlalchemy import select

from tests.helpers import system_ctx
from tests.modules.monitoring.rules.hook_support import (
    TIER_N1,
    Stage,
    add_relationship,
    deposit,
    events_of,
    feed,
    hits,
    links,
    local,
)
from xolotl.modules.monitoring.events import TIER_LIMIT_REACHED
from xolotl.modules.monitoring.rules.definitions import HitOutcome
from xolotl.modules.monitoring.rules.enums import NoticeSource
from xolotl.modules.monitoring.rules.models import TierLimitNotice
from xolotl.modules.monitoring.rules.tier_limits import (
    LIMIT_CODE_MONTHLY_OPS,
    notice_tier_limit,
)
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.party.models import CustomerRelationship
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import tenant_session

REF = "cust-n1"
PAYLOAD_KEYS = {
    "relationship_id",
    "account_id",
    "tier",
    "limit_code",
    "period_key",
    "outcome",
}


@pytest.fixture
def n1(stage: Stage) -> UUID:
    return add_relationship(stage.tenant, REF, "N1")


def _pesos(number: int, day: int) -> dict[str, object]:
    return deposit(
        f"mxn-{number}", local(day), "3300.00", customer_ref=REF, currency="MXN"
    )


def _notices(tenant_id: UUID) -> list[TierLimitNotice]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return list(s.scalars(select(TierLimitNotice)))


def test_an_n1_relationship_over_750_udi_gets_a_block_hit_and_one_notice(
    stage: Stage, n1: UUID
):
    first, second = feed(stage, [_pesos(1, 7), _pesos(2, 8)])

    [hit] = hits(stage, TIER_N1)
    assert (hit.outcome, hit.severity) == ("BLOCK", "HIGH")
    assert hit.dedup_key == f"RELATIONSHIP:{n1}:2026-12"
    assert hit.cooldown_until is None
    assert links(stage, hit.id) == {second: "TRIGGER", first: "WINDOW"}
    [notice] = _notices(stage.tenant_id)
    assert (notice.relationship_id, notice.limit_code, notice.period_key) == (
        n1,
        LIMIT_CODE_MONTHLY_OPS,
        "2026-12",
    )
    assert notice.source is NoticeSource.PROCESS
    assert len(events_of(stage.tenant_id, TIER_LIMIT_REACHED)) == 1


def test_the_limit_payload_carries_ids_and_codes_only(stage: Stage, n1: UUID):
    _, second = feed(stage, [_pesos(1, 7), _pesos(2, 8)])
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        account_id = s.scalars(
            select(Transaction.account_id).where(Transaction.id == second)
        ).one()

    [event] = events_of(stage.tenant_id, TIER_LIMIT_REACHED)
    assert set(event.data) == PAYLOAD_KEYS
    assert event.data == {
        "relationship_id": str(n1),
        "account_id": str(account_id),
        "tier": "N1",
        "limit_code": "monthly_ops",
        "period_key": "2026-12",
        "outcome": "BLOCK",
    }
    assert event.key == str(n1)


def test_a_second_overrun_in_the_month_sends_no_second_webhook(stage: Stage, n1: UUID):
    _ = feed(stage, [_pesos(1, 7), _pesos(2, 8)])
    [third] = feed(stage, [_pesos(3, 9)])

    [hit] = hits(stage, TIER_N1)
    assert links(stage, hit.id)[third] == "WINDOW"
    assert hit.fired_count == 2
    assert len(events_of(stage.tenant_id, TIER_LIMIT_REACHED)) == 1
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        assert not notice_tier_limit(
            s,
            relationship_id=n1,
            account_id=None,
            tier="N1",
            limit_code=LIMIT_CODE_MONTHLY_OPS,
            period_key="2026-12",
            outcome=HitOutcome.BLOCK,
            source=NoticeSource.PROCESS,
        )
    assert len(_notices(stage.tenant_id)) == 1


def test_a_notice_from_the_decide_source_blocks_the_process_one(
    tenant: CreatedTenant, customer: CustomerRelationship
):
    def notice(source: NoticeSource) -> bool:
        with tenant_session(system_ctx(tenant.tenant_id)) as s:
            return notice_tier_limit(
                s,
                relationship_id=customer.id,
                account_id=None,
                tier="N1",
                limit_code=LIMIT_CODE_MONTHLY_OPS,
                period_key="2026-12",
                outcome=HitOutcome.BLOCK,
                source=source,
            )

    assert notice(NoticeSource.DECIDE)
    assert not notice(NoticeSource.PROCESS)
    [row] = _notices(tenant.tenant_id)
    assert row.source is NoticeSource.DECIDE
    [event] = events_of(tenant.tenant_id, TIER_LIMIT_REACHED)
    assert event.data["account_id"] is None
    # Another month is another notice.
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert notice_tier_limit(
            s,
            relationship_id=customer.id,
            account_id=None,
            tier="N1",
            limit_code=LIMIT_CODE_MONTHLY_OPS,
            period_key="2027-01",
            outcome=HitOutcome.REVIEW,
            source=NoticeSource.PROCESS,
        )
