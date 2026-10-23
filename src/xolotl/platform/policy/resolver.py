"""The effective policy for a date and product (spec §5.3, program spec D4).

``merge_packs`` (from ``merge``) folds the assigned packs into their strictest
body, value by value; ``effective_policy`` adds the latest override in force and
the identity of what it resolved, so a snapshot of it says exactly which packs and
override decided. ``strictest_policy`` does the same across every product in force,
for what is decided per party rather than per product (SP-03 Decision 14).
"""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.platform.policy.assignments import (
    assignments_in_force,
    assignments_on,
    configs_in_force,
)
from xolotl.platform.policy.errors import PolicyNotAssignedError
from xolotl.platform.policy.merge import merge_packs
from xolotl.platform.policy.models import TenantOverrideVersion
from xolotl.platform.policy.overrides import TenantOverride, apply_override
from xolotl.platform.policy.schema import PackConfig, PolicyBody

__all__ = ["EffectivePolicy", "effective_policy", "merge_packs", "strictest_policy"]


class EffectivePolicy(PolicyBody):
    """A merged, overridden body and what it was resolved from."""

    on: date
    product: str | None
    country: str
    pack_codes: list[str]
    pack_version_ids: list[UUID]
    override_version: int | None


def effective_policy(
    session: Session, *, on: date, product: str | None
) -> EffectivePolicy:
    """Resolve the tenant's policy for ``on`` and ``product``.

    Returns:
        The merged body of the pack versions in force, with the latest override
        version effective on ``on`` applied.

    Raises:
        PolicyNotAssignedError: When no pack version is in force (404
            ``POLICY_NOT_ASSIGNED``).

    """
    rows = assignments_on(session, on=on, product=product)
    in_force = configs_in_force(session, rows, on=on)
    if not in_force:
        raise PolicyNotAssignedError
    return _resolve(session, in_force, on=on, product=product)


def strictest_policy(session: Session, *, on: date) -> EffectivePolicy:
    """Resolve the strictest merge of every pack in force on ``on``, any product.

    D21 forbids a tenant-wide assignment beside product assignments, so a tenant
    whose packs are assigned per product has no ``product=None`` policy; what is
    decided per party (screening, SP-03 Decision 14) takes every product's packs.
    For a tenant-wide tenant this is exactly its ``product=None`` policy.

    Returns:
        The merged body of every pack version in force, with the latest override
        version effective on ``on`` applied, and ``product`` ``None``.

    Raises:
        PolicyNotAssignedError: When no pack version is in force (404
            ``POLICY_NOT_ASSIGNED``).

    """
    rows = assignments_in_force(session, on=on)
    in_force = configs_in_force(session, rows, on=on)
    if not in_force:
        raise PolicyNotAssignedError
    return _resolve(session, in_force, on=on, product=None)


def _resolve(
    session: Session,
    in_force: Sequence[tuple[UUID, PackConfig]],
    *,
    on: date,
    product: str | None,
) -> EffectivePolicy:
    configs = [config for _, config in in_force]
    body = merge_packs(configs)
    latest = session.scalars(
        select(TenantOverrideVersion)
        .where(TenantOverrideVersion.effective_from <= on)
        .order_by(TenantOverrideVersion.version.desc())
        .limit(1)
    ).first()
    if latest is not None:
        body = apply_override(
            body,
            TenantOverride.model_validate(latest.overrides),
            version=latest.version,
            approved_on=latest.effective_from,
        )
    return EffectivePolicy(
        units=body.units,
        kyc=body.kyc,
        ubo=body.ubo,
        screening=body.screening,
        captures=body.captures,
        reports=body.reports,
        retention=body.retention,
        governance=body.governance,
        on=on,
        product=product,
        country=configs[0].country,
        pack_codes=[config.pack for config in configs],
        pack_version_ids=[version_id for version_id, _ in in_force],
        override_version=None if latest is None else latest.version,
    )
