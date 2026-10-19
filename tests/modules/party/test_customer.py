"""Customer relationships, their policy-driven retention, and the legal hold.

``pytest.raises`` is the outermost context manager wherever a session should roll
back, as in ``test_service.py``.
"""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select

from tests.helpers import audit_events, staff_session, system_ctx
from tests.modules.party.conftest import ANA, of_type, payloads_of
from tests.platform.policy_support import install_both_packs
from xolotl.modules.party import service
from xolotl.modules.party.customer import (
    close_relationship,
    open_relationship,
    set_legal_hold,
)
from xolotl.modules.party.enums import CustomerRelationshipStatus, PartyStatus
from xolotl.modules.party.models import CustomerRelationship, Party
from xolotl.modules.party.schemas import RelationshipOpenRequest
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import tenant_session
from xolotl.platform.errors import DomainError, NotFoundError, ValidationFailedError
from xolotl.platform.policy import PolicySnapshot, add_iso_duration, assign_pack

T_END = datetime(2027, 3, 1, 12, 0, tzinfo=UTC)


def _ana(tenant: CreatedTenant) -> UUID:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        return service.create_party(s, ANA).id


def _open(
    tenant: CreatedTenant, party_id: UUID, ref: str = "ref-1"
) -> CustomerRelationship:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        return open_relationship(
            s, RelationshipOpenRequest(party_id=party_id, external_ref=ref)
        )


def _party(tenant: CreatedTenant, party_id: UUID) -> Party:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return s.get_one(Party, (tenant.tenant_id, party_id))


def _actions(tenant: CreatedTenant, action: str) -> list[dict[str, object] | None]:
    return [e.detail for e in audit_events(tenant.tenant_id) if e.action == action]


def test_retention_arithmetic_is_plan_2as_add_iso_duration():
    assert add_iso_duration(date(2027, 3, 1), "P10Y") == date(2037, 3, 1)
    assert add_iso_duration(date(2026, 1, 31), "P1M") == date(2026, 2, 28)


def test_opening_a_relationship_audits_and_publishes_ids_only(
    engine: Engine, tenant: CreatedTenant, itf_pack: UUID
):
    ana = _ana(tenant)
    rel = _open(tenant, ana)
    assert rel.status is CustomerRelationshipStatus.ACTIVE
    assert rel.started_at is not None
    (opened,) = [
        e
        for e in audit_events(tenant.tenant_id)
        if e.action == "customer_relationship.opened"
    ]
    assert (opened.entity_type, opened.entity_id) == (
        "customer_relationship",
        str(rel.id),
    )
    assert opened.detail == {"party_id": str(ana)}
    (event,) = of_type(tenant.tenant_id, "party.relationship_opened.v1")
    assert event.data == {"relationship_id": str(rel.id), "party_id": str(ana)}
    assert event.subject == f"party/{ana}"
    assert "ref-1" not in payloads_of(tenant.tenant_id)


def test_external_ref_is_unique_and_a_purged_party_cannot_open(
    engine: Engine, tenant: CreatedTenant, itf_pack: UUID
):
    ana = _ana(tenant)
    _ = _open(tenant, ana)
    with pytest.raises(DomainError) as refused:
        _ = _open(tenant, ana)
    assert refused.value.detail == "external_ref already exists"
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        s.get_one(Party, (tenant.tenant_id, ana)).status = PartyStatus.PURGED
    with pytest.raises(DomainError) as refused:
        _ = _open(tenant, ana, "ref-2")
    assert refused.value.detail == "party is purged"


def test_a_naive_start_is_refused(
    engine: Engine, tenant: CreatedTenant, itf_pack: UUID
):
    ana = _ana(tenant)
    with (
        pytest.raises(ValidationFailedError) as refused,
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = open_relationship(
            s,
            RelationshipOpenRequest(
                party_id=ana,
                external_ref="ref-1",
                started_at=datetime(2027, 1, 1),  # ruff: ignore[call-datetime-without-tzinfo]
            ),
        )
    assert refused.value.detail == "started_at must carry a timezone"


def test_opening_without_a_pack_assignment_covering_the_product_fails(
    engine: Engine, tenant: CreatedTenant, admin_engine: Engine
):
    ana = _ana(tenant)
    with pytest.raises(NotFoundError) as refused:
        _ = _open(tenant, ana)
    assert refused.value.code == "POLICY_NOT_ASSIGNED"
    version_id = install_both_packs(admin_engine)["MX/ITF-IFPE"]
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = assign_pack(
            s,
            pack_version_id=version_id,
            product="EMONEY",
            effective_from=date(2026, 1, 1),
            approved_by=tenant.admin_user_id,
        )
    with pytest.raises(NotFoundError) as refused:
        _ = _open(tenant, ana)
    assert refused.value.code == "POLICY_NOT_ASSIGNED"
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        rel = open_relationship(
            s,
            RelationshipOpenRequest(
                party_id=ana, external_ref="ref-1", product="EMONEY"
            ),
        )
    assert rel.status is CustomerRelationshipStatus.ACTIVE
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        count = s.scalar(select(func.count()).select_from(CustomerRelationship))
    assert count == 1
    assert len(_actions(tenant, "customer_relationship.opened")) == 1


def test_closing_computes_retention_from_the_effective_policy_snapshot(
    engine: Engine, tenant: CreatedTenant, itf_pack: UUID
):
    ana = _ana(tenant)
    rel = _open(tenant, ana)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        closed_row = close_relationship(s, rel.id, ended_at=T_END)
    assert closed_row.status is CustomerRelationshipStatus.EXITED
    assert closed_row.ended_at == T_END
    assert _party(tenant, ana).retention_until == date(2037, 3, 1)
    (closed,) = of_type(tenant.tenant_id, "party.relationship_closed.v1")
    assert isinstance(closed.policy_snapshot_id, UUID)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        snapshot = s.get(PolicySnapshot, (tenant.tenant_id, closed.policy_snapshot_id))
    assert snapshot is not None
    assert closed.data == {
        "relationship_id": str(rel.id),
        "party_id": str(ana),
        "retention_until": "2037-03-01",
    }
    assert _actions(tenant, "customer_relationship.closed") == [
        {
            "party_id": str(ana),
            "retention_until": "2037-03-01",
            "policy_snapshot_id": str(closed.policy_snapshot_id),
        }
    ]


def test_closing_twice_is_a_no_op_and_an_earlier_closure_never_moves_retention_back(
    engine: Engine, tenant: CreatedTenant, itf_pack: UUID
):
    ana = _ana(tenant)
    rel = _open(tenant, ana)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = close_relationship(s, rel.id, ended_at=T_END)
    audited = len(audit_events(tenant.tenant_id))
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        again = close_relationship(s, rel.id)
    assert (again.id, again.ended_at) == (rel.id, T_END)
    assert len(audit_events(tenant.tenant_id)) == audited
    second = _open(tenant, ana, "ref-2")
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = close_relationship(s, second.id, ended_at=T_END.replace(year=2026))
    assert _party(tenant, ana).retention_until == date(2037, 3, 1)
    assert len(of_type(tenant.tenant_id, "party.relationship_closed.v1")) == 2


def test_closing_an_unknown_relationship_is_not_found(
    engine: Engine, tenant: CreatedTenant
):
    with (
        pytest.raises(NotFoundError) as refused,
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = close_relationship(s, UUID(int=1), ended_at=T_END)
    assert refused.value.detail == "relationship not found"


def test_closing_without_a_pack_assignment_fails(engine: Engine, tenant: CreatedTenant):
    ana = _ana(tenant)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        rel = CustomerRelationship(
            tenant_id=tenant.tenant_id,
            party_id=ana,
            external_ref="ref-1",
            status=CustomerRelationshipStatus.ACTIVE,
        )
        s.add(rel)
        s.flush()
        rel_id = rel.id
    with (
        pytest.raises(NotFoundError) as refused,
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = close_relationship(s, rel_id, ended_at=T_END)
    assert refused.value.code == "POLICY_NOT_ASSIGNED"
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        row = s.get_one(CustomerRelationship, (tenant.tenant_id, rel_id))
        assert row.status is CustomerRelationshipStatus.ACTIVE
    assert _party(tenant, ana).retention_until is None


def test_legal_hold_toggles_once_per_change(engine: Engine, tenant: CreatedTenant):
    ana = _ana(tenant)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        assert set_legal_hold(s, ana, legal_hold=True).legal_hold is True
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        assert set_legal_hold(s, ana, legal_hold=True).legal_hold is True
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        assert set_legal_hold(s, ana, legal_hold=False).legal_hold is False
    assert _actions(tenant, "party.legal_hold_set") == [
        {"legal_hold": True},
        {"legal_hold": False},
    ]
    assert _party(tenant, ana).legal_hold is False
