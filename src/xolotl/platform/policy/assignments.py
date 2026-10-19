"""Which packs a tenant runs under, and the overrides it adds (spec §5.3).

An assignment applies one pack version to one product (or, with no product, to
every product) from a date. Program spec D21 needs exactly one pack per product,
so a tenant-wide assignment never overlaps a product assignment; product
assignments of different products coexist.
"""

from collections.abc import Sequence
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.orm import Session

from xolotl.platform import clock
from xolotl.platform.audit import AuditEvent, record
from xolotl.platform.db import bound_context
from xolotl.platform.errors import DomainError, NotFoundError
from xolotl.platform.policy.errors import PolicyNotAssignedError
from xolotl.platform.policy.merge import merge_packs
from xolotl.platform.policy.models import (
    PolicyPack,
    PolicyPackVersion,
    TenantOverrideVersion,
    TenantPackAssignment,
)
from xolotl.platform.policy.overrides import TenantOverride, validate_against
from xolotl.platform.policy.schema import PackConfig

_D21 = (
    "a tenant-wide assignment may not overlap a product assignment "
    "(D21: two packs need distinct products)"
)


def _in_force(on: date) -> ColumnElement[bool]:
    return (TenantPackAssignment.effective_from <= on) & or_(
        TenantPackAssignment.effective_to.is_(None),
        TenantPackAssignment.effective_to >= on,
    )


def _by_pack_code() -> Select[TenantPackAssignment]:
    return (
        select(TenantPackAssignment)
        .join(
            PolicyPackVersion,
            PolicyPackVersion.id == TenantPackAssignment.pack_version_id,
        )
        .join(PolicyPack, PolicyPack.id == PolicyPackVersion.pack_id)
        .order_by(PolicyPack.code, TenantPackAssignment.effective_from)
    )


def assignments_on(
    session: Session, *, on: date, product: str | None
) -> list[TenantPackAssignment]:
    """List the assignments in force on ``on`` that apply to ``product``.

    Returns:
        The tenant-wide rows plus, when ``product`` is given, that product's rows,
        ordered by the pack code of their version.

    """
    applies = TenantPackAssignment.product.is_(None)
    if product is not None:
        applies = or_(applies, TenantPackAssignment.product == product)
    return list(session.scalars(_by_pack_code().where(_in_force(on), applies)))


def assignments_in_force(session: Session, *, on: date) -> list[TenantPackAssignment]:
    """List every assignment in force on ``on``, whatever its product.

    Returns:
        The rows, ordered by the pack code of their version.

    """
    return list(session.scalars(_by_pack_code().where(_in_force(on))))


def configs_in_force(
    session: Session, rows: Sequence[TenantPackAssignment], *, on: date
) -> list[tuple[UUID, PackConfig]]:
    """Read the configs of the rows' pack versions, keeping those in force on ``on``.

    Returns:
        ``(pack_version_id, config)`` per distinct version, in the rows' order.

    """
    configs: list[tuple[UUID, PackConfig]] = []
    seen: set[UUID] = set()
    for row in rows:
        if row.pack_version_id in seen:
            continue
        seen.add(row.pack_version_id)
        version = session.get_one(PolicyPackVersion, row.pack_version_id)
        config = PackConfig.model_validate(version.config)
        if config.effective_from <= on and (
            config.effective_to is None or config.effective_to >= on
        ):
            configs.append((version.id, config))
    return configs


def _check_overlaps(
    session: Session, tenant_id: UUID, product: str | None, effective_from: date
) -> TenantPackAssignment | None:
    tenant_rows = select(TenantPackAssignment).where(
        TenantPackAssignment.tenant_id == tenant_id
    )
    reaches = or_(
        TenantPackAssignment.effective_to.is_(None),
        TenantPackAssignment.effective_to >= effective_from,
    )
    closed: TenantPackAssignment | None = None
    same = tenant_rows.where(
        TenantPackAssignment.product.is_not_distinct_from(product)
    ).order_by(TenantPackAssignment.effective_from)
    for row in session.scalars(same):
        if row.effective_to is None and row.effective_from < effective_from:
            row.effective_to = effective_from - timedelta(days=1)
            closed = row
        elif row.effective_to is None or row.effective_to >= effective_from:
            msg = "an assignment for that product already covers that date"
            raise DomainError(msg)
    other = (
        TenantPackAssignment.product.is_not(None)
        if product is None
        else TenantPackAssignment.product.is_(None)
    )
    clash = session.scalar(
        select(func.count()).select_from(tenant_rows.where(other, reaches).subquery())
    )
    if clash:
        raise DomainError(_D21)
    return closed


def assign_pack(
    session: Session,
    *,
    pack_version_id: UUID,
    product: str | None,
    effective_from: date,
    approved_by: UUID,
) -> UUID:
    """Assign a pack version to a product (or to every product) from a date.

    An open assignment of the same product that starts earlier is closed the day
    before ``effective_from``. Audits ``policy.pack_assigned``.

    Returns:
        The id of the new assignment.

    Another assignment of the same product covering the dates, or a tenant-wide
    and a product assignment that would overlap (D21), raise ``DomainError``
    (409 ``CONFLICT``).

    Raises:
        NotFoundError: When the pack version does not exist.

    """
    version = session.get(PolicyPackVersion, pack_version_id)
    if version is None:
        msg = "pack version not found"
        raise NotFoundError(msg)
    pack = session.get_one(PolicyPack, version.pack_id)
    tenant_id = bound_context(session).require_tenant_id()
    closed = _check_overlaps(session, tenant_id, product, effective_from)
    row = TenantPackAssignment(
        tenant_id=tenant_id,
        pack_version_id=version.id,
        product=product,
        effective_from=effective_from,
        approved_by=approved_by,
    )
    session.add(row)
    session.flush()
    _ = record(
        session,
        AuditEvent(
            action="policy.pack_assigned",
            entity_type="tenant_pack_assignment",
            entity_id=str(row.id),
            detail={
                "pack": pack.code,
                "version": version.version,
                "pack_version_id": str(version.id),
                "product": product,
                "effective_from": effective_from.isoformat(),
                "closed_assignment_id": None if closed is None else str(closed.id),
            },
        ),
    )
    return row.id


def create_override(
    session: Session, overrides: TenantOverride, *, approved_by: UUID
) -> int:
    """Store a new override version, in force from today (UTC).

    The override is validated once against the strictest merge of every pack
    version in force today, whatever its product, so a value that tightens it
    tightens every product's policy. Audits ``policy.override_created``.

    Returns:
        The new version number, counting up from 1.

    Raises:
        PolicyNotAssignedError: When no pack is in force today (404
            ``POLICY_NOT_ASSIGNED``). A loosening override raises
            ``PolicyLooseningError`` and an invalid one ``ValidationFailedError``
            (``validate_against``).

    """
    effective_from = clock.now().date()
    rows = assignments_in_force(session, on=effective_from)
    configs = [c for _, c in configs_in_force(session, rows, on=effective_from)]
    if not configs:
        raise PolicyNotAssignedError
    validate_against(overrides, merge_packs(configs))
    tenant_id = bound_context(session).require_tenant_id()
    latest = session.scalar(
        select(func.max(TenantOverrideVersion.version)).where(
            TenantOverrideVersion.tenant_id == tenant_id
        )
    )
    version = (latest or 0) + 1
    row = TenantOverrideVersion(
        tenant_id=tenant_id,
        version=version,
        overrides=overrides.model_dump(mode="json"),
        effective_from=effective_from,
        approved_by=approved_by,
    )
    session.add(row)
    session.flush()
    _ = record(
        session,
        AuditEvent(
            action="policy.override_created",
            entity_type="tenant_override_version",
            entity_id=str(row.id),
            detail={"version": version, "effective_from": effective_from.isoformat()},
        ),
    )
    return version
