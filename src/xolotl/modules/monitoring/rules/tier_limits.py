"""The one tenant-facing tier-limit notice per relationship, limit and month (§12.2).

Both paths that see an account tier's limit crossed call ``notice_tier_limit``: the
processing hook for a ``TIER_LIMIT`` firing (``PROCESS``) and plan 4c's decide
path (``DECIDE``). The ``tier_limit_notice`` row's primary key makes the first
caller the only one that publishes ``monitoring.tier_limit_reached.v1``, the one
webhook that reaches the client, with ids and codes and never an amount (PRV-502).
"""

from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.events import (
    TIER_LIMIT_REACHED,
    publish_monitoring_event,
)
from xolotl.modules.monitoring.rules.definitions import HitOutcome
from xolotl.modules.monitoring.rules.enums import NoticeSource
from xolotl.modules.monitoring.rules.models import TierLimitNotice
from xolotl.platform.db import bound_context

LIMIT_CODE_MONTHLY_OPS = "monthly_ops"


def notice_tier_limit(
    s: Session,
    *,
    relationship_id: UUID,
    account_id: UUID | None,
    tier: str,
    limit_code: str,
    period_key: str,
    outcome: HitOutcome,
    source: NoticeSource,
) -> bool:
    """Record the notice and publish the limit event, once per period.

    Returns:
        True when this call inserted the notice (and published the event), False
        when the relationship already had it for ``limit_code`` and ``period_key``.

    """
    inserted = s.scalar(
        insert(TierLimitNotice)
        .values(
            tenant_id=bound_context(s).require_tenant_id(),
            relationship_id=relationship_id,
            limit_code=limit_code,
            period_key=period_key,
            source=source,
        )
        .on_conflict_do_nothing()
        .returning(TierLimitNotice.relationship_id)
    )
    if inserted is None:
        return False
    _ = publish_monitoring_event(
        s,
        TIER_LIMIT_REACHED,
        entity="customer_relationship",
        entity_id=relationship_id,
        key=str(relationship_id),
        data={
            "relationship_id": str(relationship_id),
            "account_id": None if account_id is None else str(account_id),
            "tier": tier,
            "limit_code": limit_code,
            "period_key": period_key,
            "outcome": outcome.value,
        },
    )
    return True
