"""A tenant's lists: subscriptions, catch-up and freshness (SP-03 spec §6.1, §6.4).

Every ``lists.poll`` run reconciles the tenant's ``tenant_list_subscription`` rows with
its effective policy, hands each new shared version over to re-screening by
publishing ``lists.version_published.v1`` (plan 3a Deviation 1: the screening
consumers open the batches), and tells ADMIN and MLRO about stale sources and a
missing LPB. Everything happens in the caller's (the job's) transaction.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.screening.catalog.models import (
    ListSource,
    ListVersion,
    PrivateList,
    PrivateListVersion,
    TenantListSubscription,
)
from xolotl.modules.screening.enums import ListRequirement, VersionStatus
from xolotl.modules.screening.events import (
    LISTS_VERSION_PUBLISHED,
    publish_screening_event,
)
from xolotl.platform import clock
from xolotl.platform.calendar import MEXICO_CITY
from xolotl.platform.db import bound_context
from xolotl.platform.enums import Role
from xolotl.platform.events import Event, Notifier
from xolotl.platform.events.models import Notification
from xolotl.platform.ids import uuid7
from xolotl.platform.policy import (
    PolicyNotAssignedError,
    ScreeningPolicy,
    effective_policy,
)

logger = logging.getLogger(__name__)

STALE_TEMPLATE = "lists.source_stale"
LPB_MISSING_TEMPLATE = "lists.lpb_missing"
# The tenant's private Lista de Personas Bloqueadas, named in mandatory_lists.
LPB_CODE = "MX_LPB"
SYSTEM = "system"
_NOTIFIED = (Role.ADMIN, Role.MLRO)


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """The list codes a reconcile subscribed, changed and unsubscribed."""

    added: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]


def _screening_policy(s: Session, today: date) -> ScreeningPolicy | None:
    try:
        return effective_policy(s, on=today, product=None).screening
    except PolicyNotAssignedError:
        logger.info("lists poll: no policy")
        return None


def _wanted(s: Session, screening: ScreeningPolicy) -> dict[str, ListRequirement]:
    """Map the catalog codes the policy names to their requirement.

    Returns:
        ``{code: requirement}``, mandatory codes first, in policy order.

    """
    catalog = set(s.scalars(select(ListSource.code)))
    named: dict[str, ListRequirement] = {}
    for item in screening.mandatory_lists:
        named[item.value] = ListRequirement.MANDATORY
    for code in screening.recommended_lists:
        _ = named.setdefault(code, ListRequirement.RECOMMENDED)
    wanted: dict[str, ListRequirement] = {}
    for code, requirement in named.items():
        if code in catalog:
            wanted[code] = requirement
        elif code != LPB_CODE:
            # The LPB is a private list, checked by remind_lpb_missing.
            logger.warning("lists poll: unknown list code", extra={"list_code": code})
    return wanted


def reconcile_subscriptions(s: Session, *, today: date) -> ReconcileReport | None:
    """Make the tenant's subscriptions follow its effective policy on ``today``.

    A mandatory code is subscribed and forced on; a recommended one is subscribed
    on, and a tenant's later choice to switch it off is kept; a subscription whose
    code left the policy is deleted. Codes the catalog does not hold are skipped.

    Returns:
        What changed, or ``None`` when no pack is in force for the tenant, in which
        case the subscriptions are left as they are (plan 3a Deviation 13).

    """
    screening = _screening_policy(s, today)
    if screening is None:
        return None
    wanted = _wanted(s, screening)
    tenant_id = bound_context(s).require_tenant_id()
    now = clock.now()
    rows = {
        row.list_code: row
        for row in s.scalars(
            select(TenantListSubscription)
            .order_by(TenantListSubscription.list_code)
            .with_for_update()
        )
    }
    added: list[str] = []
    changed: list[str] = []
    for code, requirement in wanted.items():
        row = rows.get(code)
        if row is None:
            # A poll running at the same time may have subscribed it already.
            inserted = s.execute(
                insert(TenantListSubscription)
                .values(
                    tenant_id=tenant_id,
                    id=uuid7(),
                    list_code=code,
                    requirement=requirement,
                    enabled=True,
                    changed_by=SYSTEM,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "list_code"])
                .returning(TenantListSubscription.id)
            ).first()
            if inserted is not None:
                added.append(code)
            continue
        forced_on = requirement is ListRequirement.MANDATORY and not row.enabled
        if row.requirement is not requirement or forced_on:
            row.requirement = requirement
            row.enabled = row.enabled or requirement is ListRequirement.MANDATORY
            row.changed_by = SYSTEM
            row.updated_at = now
            changed.append(code)
    removed = sorted(code for code in rows if code not in wanted)
    if removed:
        _ = s.execute(
            delete(TenantListSubscription).where(
                TenantListSubscription.list_code.in_(removed)
            )
        )
    s.flush()
    return ReconcileReport(
        added=tuple(sorted(added)),
        changed=tuple(sorted(changed)),
        removed=tuple(removed),
    )


def _publish_version(s: Session, version: ListVersion, *, initial: bool) -> Event:
    return publish_screening_event(
        s,
        LISTS_VERSION_PUBLISHED,
        entity="list_version",
        entity_id=version.id,
        key=version.list_code,
        data={
            "list_code": version.list_code,
            "version_id": str(version.id),
            "seq": version.seq,
            "added": version.added,
            "changed": version.changed,
            "removed": version.removed,
            "initial": initial,
        },
    )


def catch_up(s: Session) -> list[Event]:
    """Hand every version newer than each enabled subscription's ``last_seq`` over.

    The subscription rows are locked first, so two polls of one tenant at once
    publish each version once: the second waits, then reads the advanced
    ``last_seq``. A subscription that has seen nothing (``last_seq = 0``) gets one
    ``initial`` event for the newest version, a full re-screen rather than one delta
    per historic version; later versions get one event each, in ``seq`` order.

    Returns:
        The events published, in order.

    """
    subscriptions = s.scalars(
        select(TenantListSubscription)
        .where(TenantListSubscription.enabled)
        .order_by(TenantListSubscription.list_code)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    events: list[Event] = []
    for subscription in subscriptions:
        versions = s.scalars(
            select(ListVersion)
            .where(
                ListVersion.list_code == subscription.list_code,
                ListVersion.seq > subscription.last_seq,
            )
            .order_by(ListVersion.seq)
        ).all()
        if not versions:
            continue
        if subscription.last_seq == 0:
            # The newest version is the active one.
            events.append(_publish_version(s, versions[-1], initial=True))
        else:
            events.extend(_publish_version(s, v, initial=False) for v in versions)
        subscription.last_seq = versions[-1].seq
    s.flush()
    return events


def is_stale(source: ListSource, *, now: datetime) -> bool:
    """Tell whether a source has gone more than twice its interval without success.

    Returns:
        Whether it never succeeded or last did more than ``2 * poll_interval`` ago.

    """
    return (
        source.last_success_at is None
        or now - source.last_success_at > 2 * source.poll_interval
    )


def check_staleness(s: Session, notifier: Notifier, *, now: datetime) -> list[str]:
    """Notify ADMIN and MLRO once per stale episode of each subscribed source.

    A stale source of an enabled subscription with no ``stale_notified_at`` is
    notified and flagged; a fresh one has its flag cleared, ending the episode.

    Returns:
        The codes notified by this call.

    """
    rows = s.execute(
        select(TenantListSubscription, ListSource)
        .join(ListSource, ListSource.code == TenantListSubscription.list_code)
        .where(TenantListSubscription.enabled)
        .order_by(TenantListSubscription.list_code)
    ).all()
    notified: list[str] = []
    for subscription, source in rows:
        if not is_stale(source, now=now):
            if subscription.stale_notified_at is not None:
                subscription.stale_notified_at = None
            continue
        if subscription.stale_notified_at is not None:
            continue
        last = source.last_success_at
        for role in _NOTIFIED:
            _ = notifier.notify(
                s,
                template=STALE_TEMPLATE,
                data={
                    "list_code": source.code,
                    "last_success_at": None if last is None else last.isoformat(),
                },
                recipient_role=role,
            )
        subscription.stale_notified_at = now
        notified.append(source.code)
    s.flush()
    return notified


def remind_lpb_missing(s: Session, notifier: Notifier, *, now: datetime) -> bool:
    """Remind ADMIN and MLRO, once a Mexico City day, that the LPB is missing.

    Only when the effective policy makes ``MX_LPB`` mandatory and the tenant has no
    active ``MX_LPB`` private version (plan 3a Deviation 12: "once a day" is no
    reminder since today's 00:00 in Mexico City).

    Returns:
        Whether a reminder was written.

    """
    mexico_city = ZoneInfo(MEXICO_CITY)
    today = now.astimezone(mexico_city).date()
    screening = _screening_policy(s, today)
    if screening is None or LPB_CODE not in {
        item.value for item in screening.mandatory_lists
    }:
        return False
    active = s.scalars(
        select(PrivateListVersion.id)
        .join(
            PrivateList,
            (PrivateList.tenant_id == PrivateListVersion.tenant_id)
            & (PrivateList.id == PrivateListVersion.list_id),
        )
        .where(
            PrivateList.list_code == LPB_CODE,
            PrivateListVersion.status == VersionStatus.ACTIVE,
        )
        .limit(1)
    ).first()
    if active is not None:
        return False
    midnight = datetime.combine(today, time(0), tzinfo=mexico_city)
    reminded = s.scalars(
        select(Notification.id)
        .where(
            Notification.template == LPB_MISSING_TEMPLATE,
            Notification.created_at >= midnight,
        )
        .limit(1)
    ).first()
    if reminded is not None:
        return False
    for role in _NOTIFIED:
        _ = notifier.notify(
            s, template=LPB_MISSING_TEMPLATE, data={}, recipient_role=role
        )
    return True
