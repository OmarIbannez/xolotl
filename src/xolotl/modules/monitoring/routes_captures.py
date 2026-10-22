"""The staff captures API (SP-04 spec §10, §15; plan 4b B11).

``GET /v1/monitoring/captures`` lists reportable events, newest first; the detail
adds the pre-mapped report fields, the unit values and the accumulation group.
``/periods`` lists the capture periods with their reconciliation, and an MLRO or
ADMIN closes one now (``POST /periods/{id}/close``, spec §10.4). ``/effective``
lists the policy's captures and tier limits with their provenance (the read-only
Capturas y límites page).

Every route is staff only (``require_access()`` without a scope refuses every API
client): a capture says a customer is being reported, which never reaches the
customer's side (tipping-off, spec §10.1, D20, PRV-502). Another tenant's event or
period is ``NOT_FOUND`` (row-level security). Literal routes come before
``/{event_id}``.
"""

import base64
import binascii
from collections.abc import Sequence
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query
from sqlalchemy import Select, and_, func, or_, select, tuple_
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.captures.definitions import capture_definitions
from xolotl.modules.monitoring.captures.enums import CaptureKind, CapturePeriodStatus
from xolotl.modules.monitoring.captures.groups import group_members
from xolotl.modules.monitoring.captures.models import (
    CaptureGroup,
    CapturePeriod,
    ReportableEvent,
)
from xolotl.modules.monitoring.captures.periods import close_period
from xolotl.modules.monitoring.captures.record import LIVE
from xolotl.modules.monitoring.enrich.policy import policy_date
from xolotl.modules.monitoring.rules.tier_limits import LIMIT_CODE_MONTHLY_OPS
from xolotl.modules.monitoring.schemas import (
    CaptureGroupView,
    CaptureLimitView,
    CapturePeriodQuery,
    CapturePeriodResponse,
    CaptureQuery,
    ReportableEventDetail,
    ReportableEventSummary,
)
from xolotl.platform import clock
from xolotl.platform.api.pagination import Page, PageDep, PageRequest, paginate
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.db import bound_context
from xolotl.platform.enums import Role
from xolotl.platform.errors import NotFoundError, ValidationFailedError
from xolotl.platform.policy import EffectivePolicy, effective_policy, get_snapshot
from xolotl.platform.units import ConversionRule, UnitService

router = v1_router(prefix="/monitoring/captures", tags=["monitoring-captures"])

# Staff only: a route without a scope refuses every API client.
CaptureStaff = Annotated[Actor, Depends(require_access())]
PeriodCloser = Annotated[
    Actor, Depends(require_access(roles=(Role.MLRO, Role.ADMIN), write=True))
]
TIER_LIMIT = "TIER_LIMIT"


def _captures_query(query: CaptureQuery) -> Select[ReportableEvent]:
    stmt = select(ReportableEvent)
    if query.capture_code is not None:
        stmt = stmt.where(ReportableEvent.capture_code == query.capture_code)
    if query.report_code is not None:
        stmt = stmt.where(ReportableEvent.report_code == query.report_code)
    if query.period_key is not None:
        stmt = stmt.where(ReportableEvent.period_key == query.period_key)
    if query.status is not None:
        stmt = stmt.where(ReportableEvent.status == query.status)
    if query.relationship_id is not None:
        stmt = stmt.where(ReportableEvent.relationship_id == query.relationship_id)
    if query.transaction_id is not None:
        stmt = stmt.where(ReportableEvent.transaction_id == query.transaction_id)
    return stmt


def _event(session: Session, event_id: UUID) -> ReportableEvent:
    tenant_id = bound_context(session).require_tenant_id()
    row = session.get(ReportableEvent, (tenant_id, event_id))
    if row is None:
        msg = "reportable event not found"
        raise NotFoundError(msg)
    return row


def _group_view(session: Session, event: ReportableEvent) -> CaptureGroupView | None:
    if event.capture_group_id is None:
        return None
    tenant_id = bound_context(session).require_tenant_id()
    group = session.get_one(CaptureGroup, (tenant_id, event.capture_group_id))
    policy = get_snapshot(session, event.policy_snapshot_id).policy
    definition = next(
        (d for d in capture_definitions(policy) if d.code == event.capture_code),
        None,
    )
    members = (
        0 if definition is None else len(group_members(session, definition, group))
    )
    return CaptureGroupView(
        id=group.id,
        status=group.status,
        window_start=group.window_start,
        window_end=group.window_end,
        threshold_crossed_at=group.threshold_crossed_at,
        accumulated_minor=group.accumulated_minor,
        accumulated_unit=group.accumulated_unit,
        member_count=members,
        superseded_by_id=group.superseded_by_id,
    )


def _detail(session: Session, event: ReportableEvent) -> ReportableEventDetail:
    return ReportableEventDetail(
        id=event.id,
        capture_code=event.capture_code,
        report_code=event.report_code,
        capture_kind=event.capture_kind,
        transaction_id=event.transaction_id,
        occurred_at=event.occurred_at,
        party_id=event.party_id,
        relationship_id=event.relationship_id,
        product=event.product,
        period_key=event.period_key,
        capture_group_id=event.capture_group_id,
        status=event.status,
        late=event.late,
        captured_at=event.captured_at,
        fields=event.fields,
        unit_values=event.unit_values,
        group=_group_view(session, event),
        supersedes_id=event.supersedes_id,
        superseded_by_id=event.superseded_by_id,
        void_reason=event.void_reason,
        policy_snapshot_id=event.policy_snapshot_id,
    )


# ---- periods ------------------------------------------------------------------


def _encode(period_key: str, period_id: UUID) -> str:
    raw = f"{period_key}|{period_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(cursor: str) -> tuple[str, UUID]:
    msg = "invalid cursor"
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        key, separator, ident = raw.partition("|")
        if separator != "|":
            raise ValidationFailedError(msg)
        return key, UUID(ident)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ValidationFailedError(msg) from exc


def _period_page(
    session: Session, query: CapturePeriodQuery, page: PageRequest
) -> tuple[list[CapturePeriod], str | None]:
    stmt = select(CapturePeriod)
    if query.capture_code is not None:
        stmt = stmt.where(CapturePeriod.capture_code == query.capture_code)
    if query.status is not None:
        stmt = stmt.where(CapturePeriod.status == query.status)
    if page.cursor is not None:
        after_key, after_id = _decode(page.cursor)
        stmt = stmt.where(
            or_(
                CapturePeriod.period_key < after_key,
                and_(
                    CapturePeriod.period_key == after_key,
                    CapturePeriod.id < after_id,
                ),
            )
        )
    rows = list(
        session.scalars(
            stmt.order_by(
                CapturePeriod.period_key.desc(), CapturePeriod.id.desc()
            ).limit(page.limit + 1)
        )
    )
    if len(rows) <= page.limit:
        return rows, None
    rows = rows[: page.limit]
    return rows, _encode(rows[-1].period_key, rows[-1].id)


def _live_counts(
    session: Session, rows: Sequence[CapturePeriod]
) -> dict[tuple[str, str], int]:
    pairs = [(row.capture_code, row.period_key) for row in rows]
    if not pairs:
        return {}
    found = session.execute(
        select(ReportableEvent.capture_code, ReportableEvent.period_key, func.count())
        .where(
            tuple_(ReportableEvent.capture_code, ReportableEvent.period_key).in_(pairs),
            ReportableEvent.status.in_(LIVE),
        )
        .group_by(ReportableEvent.capture_code, ReportableEvent.period_key)
    ).all()
    return {(code, key): count for code, key, count in found}


def _period_view(row: CapturePeriod, live: int | None) -> CapturePeriodResponse:
    discrepancies = (
        None
        if row.discrepancies is None
        else {
            name: [UUID(str(item)) for item in ids]
            for name, ids in row.discrepancies.items()
            if isinstance(ids, list)
        }
    )
    return CapturePeriodResponse(
        id=row.id,
        capture_code=row.capture_code,
        report_code=row.report_code,
        period_key=row.period_key,
        period_start=row.period_start,
        period_end=row.period_end,
        status=row.status,
        event_count=row.event_count if live is None else live,
        reconciled_count=row.reconciled_count,
        reconciled_at=row.reconciled_at,
        discrepancies=discrepancies,
        closed_at=row.closed_at,
        reopened_at=row.reopened_at,
        change_count=row.change_count,
    )


def _period_views(
    session: Session, rows: Sequence[CapturePeriod]
) -> list[CapturePeriodResponse]:
    # An open period's count moves with every event; a closed one's is the close's.
    counts = _live_counts(
        session, [row for row in rows if row.status is not CapturePeriodStatus.CLOSED]
    )
    return [
        _period_view(
            row,
            None
            if row.status is CapturePeriodStatus.CLOSED
            else counts.get((row.capture_code, row.period_key), 0),
        )
        for row in rows
    ]


# ---- the effective captures and limits ------------------------------------------


def _source(policy: EffectivePolicy) -> str:
    packs = ", ".join(policy.pack_codes)
    if policy.override_version is None:
        return packs
    return f"{packs}; override v{policy.override_version}"


def _limit_views(policy: EffectivePolicy) -> list[CaptureLimitView]:
    source = _source(policy)
    views = [
        CaptureLimitView(
            code=capture.code,
            kind=CaptureKind(capture.kind),
            threshold=str(capture.threshold.value.amount),
            unit=capture.threshold.value.unit,
            conversion_rule=capture.conversion_rule,
            window=None if capture.window is None else capture.window.value,
            period=capture.period.value,
            report_code=capture.report,
            legal_basis=capture.threshold.legal_basis,
            confidence=capture.threshold.confidence.value,
            source=source,
        )
        for capture in policy.captures
    ]
    rule = policy.units.tier_limit_rule
    conversion = ConversionRule.SAME_DAY if rule is None else rule.value
    for tier in policy.kyc.tiers:
        limit = tier.limits.monthly_ops
        if limit is None:
            continue
        views.append(
            CaptureLimitView(
                code=f"{tier.code}/{LIMIT_CODE_MONTHLY_OPS}",
                kind=TIER_LIMIT,
                threshold=str(limit.value.amount),
                unit=limit.value.unit,
                conversion_rule=conversion,
                window=None,
                period="MONTH",
                report_code=None,
                legal_basis=limit.legal_basis,
                confidence=limit.confidence.value,
                source=source,
            )
        )
    return views


# ---- routes ---------------------------------------------------------------------


@router.get("")
def list_captures(
    _staff: CaptureStaff,
    session: SessionDep,
    page: PageDep,
    query: Annotated[CaptureQuery, Query()],
) -> Page[ReportableEventSummary]:
    rows, next_cursor = paginate(
        session,
        _captures_query(query),
        ts_col=ReportableEvent.captured_at,
        id_col=ReportableEvent.id,
        key=lambda row: (row.captured_at, row.id),
        page=page,
    )
    return Page(
        items=[ReportableEventSummary.model_validate(row) for row in rows],
        next_cursor=next_cursor,
    )


@router.get("/periods")
def list_capture_periods(
    _staff: CaptureStaff,
    session: SessionDep,
    page: PageDep,
    query: Annotated[CapturePeriodQuery, Query()],
) -> Page[CapturePeriodResponse]:
    rows, next_cursor = _period_page(session, query, page)
    return Page(items=_period_views(session, rows), next_cursor=next_cursor)


@router.post("/periods/{period_id}/close")
def close_capture_period(
    period_id: UUID, _closer: PeriodCloser, session: SessionDep
) -> CapturePeriodResponse:
    tenant_id = bound_context(session).require_tenant_id()
    period = session.get(CapturePeriod, (tenant_id, period_id))
    if period is None:
        msg = "capture period not found"
        raise NotFoundError(msg)
    closed = close_period(session, period, units=UnitService(session))
    return _period_view(closed, None)


@router.get("/effective")
def list_effective_captures(
    _staff: CaptureStaff,
    session: SessionDep,
    on: date | None = None,
    product: Annotated[str | None, Query(max_length=64)] = None,
) -> list[CaptureLimitView]:
    day = policy_date(clock.now()) if on is None else on
    return _limit_views(effective_policy(session, on=day, product=product))


@router.get("/{event_id}")
def get_capture(
    event_id: UUID, _staff: CaptureStaff, session: SessionDep
) -> ReportableEventDetail:
    return _detail(session, _event(session, event_id))
