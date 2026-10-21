"""Accounts: one DEFAULT per relationship, a client reference bound once."""

import pytest
from sqlalchemy import func, select

from tests.helpers import staff_session
from tests.modules.monitoring.support import add_customer
from xolotl.modules.monitoring.enums import AccountKind
from xolotl.modules.monitoring.txn.accounts import (
    AccountRefConflictError,
    account_for,
    default_account,
    find_account,
)
from xolotl.modules.monitoring.txn.models import Account
from xolotl.modules.party.models import CustomerRelationship
from xolotl.modules.party.service import relationship_ref
from xolotl.modules.tenants.service import CreatedTenant


def _accounts(tenant: CreatedTenant) -> int | None:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        return s.scalar(select(func.count()).select_from(Account))


def test_the_default_account_is_one_per_relationship(
    tenant: CreatedTenant, customer: CustomerRelationship
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        ref = relationship_ref(s, customer.id)
        assert find_account(s, customer.id, None) is None
        first = default_account(s, ref)
        again = account_for(s, ref, None)
        found = find_account(s, customer.id, None)
    assert again.id == first.id
    assert found is not None
    assert found.id == first.id
    assert first.kind is AccountKind.DEFAULT
    assert first.external_ref == f"default:{customer.id}"
    assert first.party_id == customer.party_id
    assert _accounts(tenant) == 1


def test_an_account_ref_is_created_on_first_sight_and_reused(
    tenant: CreatedTenant, customer: CustomerRelationship
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        ref = relationship_ref(s, customer.id)
        assert find_account(s, customer.id, "acc-9") is None
        first = account_for(s, ref, "acc-9")
        seen = first.last_seen_at
        again = account_for(s, ref, "acc-9")
    assert again.id == first.id
    assert again.last_seen_at >= seen
    assert first.kind is AccountKind.OTHER
    assert first.relationship_id == customer.id
    assert _accounts(tenant) == 1


def test_an_account_ref_bound_to_another_relationship_conflicts(
    tenant: CreatedTenant, customer: CustomerRelationship
):
    other = add_customer(tenant, "cust-2")
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = account_for(s, relationship_ref(s, customer.id), "acc-9")
    with (
        pytest.raises(AccountRefConflictError),
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = account_for(s, relationship_ref(s, other.id), "acc-9")
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        bound = find_account(s, other.id, "acc-9")
    assert bound is not None
    assert bound.relationship_id == customer.id
