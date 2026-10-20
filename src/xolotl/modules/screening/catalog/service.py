"""A tenant's view of its shared lists, and its subscription choices (SP-03 spec §12).

The overview joins each subscription to its source and the source's active version,
so the console's freshness badge needs one call (plan 3a Deviation 16). Only a
recommended list may be switched off; a change is audited.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.modules.screening.catalog.models import ListSource, TenantListSubscription
from xolotl.modules.screening.catalog.queries import active_versions
from xolotl.modules.screening.catalog.subscriptions import is_stale
from xolotl.modules.screening.enums import ListRequirement
from xolotl.modules.screening.errors import ListMandatoryError
from xolotl.modules.screening.schemas import ListOverview
from xolotl.platform import clock
from xolotl.platform.audit import AuditEvent, record
from xolotl.platform.db import bound_context
from xolotl.platform.errors import NotFoundError


def list_overview(s: Session, *, now: datetime) -> list[ListOverview]:
    """Describe each of the tenant's subscriptions, enabled or not.

    Returns:
        One row per subscription, ordered by list code.

    """
    rows = s.execute(
        select(TenantListSubscription, ListSource)
        .join(ListSource, ListSource.code == TenantListSubscription.list_code)
        .order_by(TenantListSubscription.list_code)
    ).all()
    versions = active_versions(s)
    overview: list[ListOverview] = []
    for subscription, source in rows:
        version = versions.get(source.code)
        overview.append(
            ListOverview(
                code=source.code,
                list_kind=source.list_kind,
                title=source.title,
                jurisdiction=source.jurisdiction,
                requirement=subscription.requirement,
                enabled=subscription.enabled,
                active_seq=None if version is None else version.seq,
                published_at=None if version is None else version.published_at,
                fetched_at=None if version is None else version.fetched_at,
                entry_count=None if version is None else version.entry_count,
                stale=is_stale(source, now=now),
                failing=source.consecutive_failures > 0,
                last_error=source.last_error,
                last_success_at=source.last_success_at,
            )
        )
    return overview


def set_subscription_enabled(
    s: Session, code: str, *, enabled: bool
) -> TenantListSubscription:
    """Switch one of the tenant's list subscriptions on or off.

    Audited ``lists.subscription_changed`` only when the value changes.

    Returns:
        The subscription, flushed.

    Raises:
        NotFoundError: When the tenant is not subscribed to ``code``.
        ListMandatoryError: When a mandatory list would be switched off.

    """
    subscription = s.scalar(
        select(TenantListSubscription)
        .where(TenantListSubscription.list_code == code)
        .with_for_update()
    )
    if subscription is None:
        msg = "list subscription not found"
        raise NotFoundError(msg)
    if subscription.requirement is ListRequirement.MANDATORY and not enabled:
        raise ListMandatoryError
    if subscription.enabled == enabled:
        return subscription
    subscription.enabled = enabled
    subscription.changed_by = bound_context(s).actor_id
    subscription.updated_at = clock.now()
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="lists.subscription_changed",
            entity_type="tenant_list_subscription",
            entity_id=str(subscription.id),
            detail={"list_code": code, "enabled": enabled},
        ),
    )
    return subscription
