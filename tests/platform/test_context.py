from uuid import uuid4

import pytest

from xolotl.platform.enums import ActorType, Role
from xolotl.platform.errors import ContextMissingError
from xolotl.platform.tenancy.context import (
    TenantContext,
    bind_context,
    current_context,
    require_context,
    roles_guc,
    tenant_guc,
)


def test_platform_operator_may_have_no_tenant():
    ctx = TenantContext.platform("cli:omar")
    assert ctx.tenant_id is None
    assert ctx.actor_type is ActorType.PLATFORM_OPERATOR
    assert not tenant_guc(ctx)
    with pytest.raises(ContextMissingError):
        _ = ctx.require_tenant_id()


def test_other_actor_types_require_a_tenant():
    with pytest.raises(ValueError, match="PLATFORM_OPERATOR"):
        _ = TenantContext(tenant_id=None, actor_type=ActorType.STAFF, actor_id="x")


def test_system_context_and_guc_values():
    tenant_id = uuid4()
    ctx = TenantContext.system(tenant_id, "job:123")
    assert ctx.actor_type is ActorType.SYSTEM
    assert ctx.require_tenant_id() == tenant_id
    assert tenant_guc(ctx) == str(tenant_id)


def test_roles_guc_is_sorted_and_comma_delimited():
    assert roles_guc({Role.MLRO, Role.ADMIN}) == ",ADMIN,MLRO,"
    assert not roles_guc(())


def test_bind_and_require_context():
    assert current_context.get() is None
    with pytest.raises(ContextMissingError):
        _ = require_context()
    outer = TenantContext.platform("cli:a")
    inner = TenantContext.system(uuid4(), "job:1")
    with bind_context(outer):
        assert require_context() is outer
        with bind_context(inner):
            assert require_context() is inner
        assert require_context() is outer
    assert current_context.get() is None


def test_context_is_frozen_and_hashable():
    ctx = TenantContext.system(uuid4(), "job:1")
    attr = "actor_id"
    with pytest.raises(AttributeError):
        setattr(ctx, attr, "other")
    assert {ctx: 1}[ctx] == 1
