"""The tenant's side of policy packs: effective policy, regulatory profile, branches.

``effective_with_conversions`` resolves and snapshots the effective policy and
converts its tier limits and capture thresholds into MXN best-effort, so the pack
viewer renders even when a unit value is missing (the decision paths call
``UnitService`` directly and get the 503). The regulatory profile's RFC and a
branch's address are stored encrypted under the tenant's DATA key; responses carry
the RFC masked and no address, and nothing here reveals either to a person.
"""

from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.tenants.models import (
    StaffUser,
    TenantBranch,
    TenantRegulatoryProfile,
)
from xolotl.modules.tenants.schemas_policy import (
    AssignmentResponse,
    BranchCreateRequest,
    BranchResponse,
    ConversionResponse,
    EffectivePolicyResponse,
    RegulatoryProfileRequest,
    RegulatoryProfileResponse,
)
from xolotl.platform.audit import AuditEvent, record
from xolotl.platform.crypto.encrypted import encrypt
from xolotl.platform.db import bound_context
from xolotl.platform.errors import DomainError, NotFoundError, ValidationFailedError
from xolotl.platform.ids import uuid7
from xolotl.platform.policy import (
    PolicyPack,
    PolicyPackVersion,
    Quantity,
    TenantPackAssignment,
    effective_policy,
    get_snapshot,
    snapshot_policy,
)
from xolotl.platform.text import RFC_SHAPE, mask_tail
from xolotl.platform.units import (
    UNIT_OF,
    ConversionRule,
    UnitService,
    UnitValueMissingError,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

_QUOTE_CCY = "MXN"


def _convert(
    units: UnitService,
    quantity: Quantity,
    on: date,
    rule: ConversionRule,
    missing: set[str],
) -> ConversionResponse | None:
    try:
        conversion = units.threshold_in_ccy(quantity, _QUOTE_CCY, on, rule)
    except (UnitValueMissingError, ValidationFailedError):
        # A missing value, or PREV_MONTH_AVG, which SP-02 cannot convert.
        missing.add(UNIT_OF.get(quantity.unit, quantity.unit))
        return None
    return ConversionResponse(
        amount=conversion.amount,
        currency=conversion.currency,
        unit=conversion.unit,
        rate=conversion.rate,
        unit_value_id=conversion.unit_value_id,
        rule=conversion.rule,
        resolved_date=conversion.resolved_date,
    )


def effective_with_conversions(
    s: Session, *, on: date, product: str | None
) -> EffectivePolicyResponse:
    """Resolve, snapshot and convert the tenant's policy for ``on`` and ``product``.

    With no pack in force, ``effective_policy`` raises ``PolicyNotAssignedError``
    (404 ``POLICY_NOT_ASSIGNED``) and nothing is snapshotted.

    Returns:
        The effective policy, its snapshot id and hex hash, the MXN amount of every
        tier limit and capture threshold (null where a unit value is missing) and
        the units that were missing, sorted.

    """
    policy = effective_policy(s, on=on, product=product)
    snapshot = get_snapshot(s, snapshot_policy(s, policy))
    units = UnitService(s)
    tier_rule = policy.units.tier_limit_rule
    tier_conversion = ConversionRule.SAME_DAY if tier_rule is None else tier_rule.value
    converted: dict[str, ConversionResponse | None] = {}
    missing: set[str] = set()
    for tier in policy.kyc.tiers:
        limits = (
            ("monthly_ops", tier.limits.monthly_ops),
            ("max_balance", tier.limits.max_balance),
        )
        for name, limit in limits:
            if limit is not None:
                converted[f"kyc.tiers[{tier.code}].limits.{name}"] = _convert(
                    units, limit.value, on, tier_conversion, missing
                )
    for capture in policy.captures:
        converted[f"captures[{capture.code}].threshold"] = _convert(
            units, capture.threshold.value, on, capture.conversion_rule, missing
        )
    return EffectivePolicyResponse(
        policy=policy,
        snapshot_id=snapshot.snapshot_id,
        content_hash=snapshot.content_hash,
        converted=converted,
        missing_units=sorted(missing),
    )


def assignment_responses(
    s: Session, rows: Sequence[TenantPackAssignment]
) -> list[AssignmentResponse]:
    """Describe assignments with the code and number of their pack version.

    Returns:
        One response per row, in the rows' order.

    """
    version_ids = {row.pack_version_id for row in rows}
    versions = {
        version.id: version
        for version in s.scalars(
            select(PolicyPackVersion).where(PolicyPackVersion.id.in_(version_ids))
        )
    }
    pack_ids = {version.pack_id for version in versions.values()}
    codes = {
        pack.id: pack.code
        for pack in s.scalars(select(PolicyPack).where(PolicyPack.id.in_(pack_ids)))
    }
    responses: list[AssignmentResponse] = []
    for row in rows:
        version = versions[row.pack_version_id]
        responses.append(
            AssignmentResponse(
                id=row.id,
                pack_version_id=row.pack_version_id,
                pack=codes[version.pack_id],
                version=version.version,
                product=row.product,
                effective_from=row.effective_from,
                effective_to=row.effective_to,
                approved_by=row.approved_by,
                created_at=row.created_at,
            )
        )
    return responses


def _profile_response(
    row: TenantRegulatoryProfile, rfc: str
) -> RegulatoryProfileResponse:
    return RegulatoryProfileResponse(
        id=row.id,
        rfc_masked=mask_tail(rfc),
        legal_name=row.legal_name,
        casfim_supervisor=row.casfim_supervisor,
        casfim_entity_key=row.casfim_entity_key,
        spplld_activity_key=row.spplld_activity_key,
        platform_domain=row.platform_domain,
        entidad_colegiada=row.entidad_colegiada,
        compliance_officer_user_id=row.compliance_officer_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def set_regulatory_profile(
    s: Session, body: RegulatoryProfileRequest
) -> RegulatoryProfileResponse:
    """Create or replace the tenant's single regulatory profile.

    Audits ``tenant.regulatory_profile_set`` with the names of the fields set (never
    their values, never the RFC).

    Returns:
        The profile, with the RFC masked.

    Raises:
        ValidationFailedError: When the RFC does not have an RFC's shape, or the
            compliance officer is not a staff user of the tenant.

    """
    rfc = body.rfc.strip().upper()
    if RFC_SHAPE.fullmatch(rfc) is None:
        msg = "invalid RFC"
        raise ValidationFailedError(msg)
    tenant_id = bound_context(s).require_tenant_id()
    officer = body.compliance_officer_user_id
    if officer is not None and s.get(StaffUser, (tenant_id, officer)) is None:
        msg = "unknown compliance officer"
        raise ValidationFailedError(msg)
    fields = body.model_dump(exclude={"rfc"})
    given: dict[str, object] = {
        "legal_name": body.legal_name,
        "casfim_supervisor": body.casfim_supervisor,
        "casfim_entity_key": body.casfim_entity_key,
        "spplld_activity_key": body.spplld_activity_key,
        "platform_domain": body.platform_domain,
        "entidad_colegiada": body.entidad_colegiada,
        "compliance_officer_user_id": officer,
    }
    set_fields: list[JsonValue] = [
        name for name in sorted(given) if given[name] not in {None, False}
    ]
    encrypted = encrypt(rfc, tenant_id=tenant_id, session=s)
    # Insert, or leave the tenant's row to the update below: two first PUTs at once
    # cannot both insert, and the second updates what the first created.
    created = s.scalar(
        insert(TenantRegulatoryProfile)
        .values(tenant_id=tenant_id, id=uuid7(), rfc=encrypted, **fields)
        .on_conflict_do_nothing(index_elements=["tenant_id"])
        .returning(TenantRegulatoryProfile.id)
    )
    row = s.scalars(
        select(TenantRegulatoryProfile)
        .where(TenantRegulatoryProfile.tenant_id == tenant_id)
        .with_for_update()
    ).one()
    if created is None:
        row.rfc = encrypted
        row.legal_name = body.legal_name
        row.casfim_supervisor = body.casfim_supervisor
        row.casfim_entity_key = body.casfim_entity_key
        row.spplld_activity_key = body.spplld_activity_key
        row.platform_domain = body.platform_domain
        row.entidad_colegiada = body.entidad_colegiada
        row.compliance_officer_user_id = officer
        s.flush()
        s.refresh(row)
    _ = record(
        s,
        AuditEvent(
            action="tenant.regulatory_profile_set",
            entity_type="tenant_regulatory_profile",
            entity_id=str(row.id),
            detail={"fields": set_fields},
        ),
    )
    return _profile_response(row, rfc)


def get_regulatory_profile(s: Session) -> RegulatoryProfileResponse:
    """Read the tenant's regulatory profile.

    The RFC is decrypted to be masked (processing, not showing it), so no
    ``VIEW_PII`` is recorded.

    Returns:
        The profile, with the RFC masked.

    Raises:
        NotFoundError: When the tenant has not set one.

    """
    row = s.scalars(select(TenantRegulatoryProfile)).first()
    if row is None:
        msg = "regulatory profile not set"
        raise NotFoundError(msg)
    return _profile_response(row, row.rfc.reveal(session=s))


def branch_response(row: TenantBranch) -> BranchResponse:
    """Describe a branch without its address, which is encrypted.

    Returns:
        The branch's public fields.

    """
    return BranchResponse(
        id=row.id,
        code=row.code,
        name=row.name,
        state_code=row.state_code,
        municipality_code=row.municipality_code,
        status=row.status,
        created_at=row.created_at,
    )


def create_branch(s: Session, body: BranchCreateRequest) -> BranchResponse:
    """Add a branch to the tenant's catalogue; audits ``tenant.branch_created``.

    Returns:
        The new branch, without its address.

    Raises:
        DomainError: When the tenant already has a branch with that code (409
            ``CONFLICT``).

    """
    tenant_id = bound_context(s).require_tenant_id()
    created = s.scalar(
        insert(TenantBranch)
        .values(
            tenant_id=tenant_id,
            id=uuid7(),
            code=body.code,
            name=body.name,
            address=encrypt(body.address, tenant_id=tenant_id, session=s),
            state_code=body.state_code,
            municipality_code=body.municipality_code,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "code"])
        .returning(TenantBranch.id)
    )
    if created is None:
        msg = "branch code already exists"
        raise DomainError(msg)
    row = s.get_one(TenantBranch, (tenant_id, created))
    _ = record(
        s,
        AuditEvent(
            action="tenant.branch_created",
            entity_type="tenant_branch",
            entity_id=str(row.id),
            detail={"code": row.code},
        ),
    )
    return branch_response(row)
