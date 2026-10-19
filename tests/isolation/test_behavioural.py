"""Behavioural half of the isolation harness (spec §4.4, item 2).

For every tenant table: tenant A inserts one row; tenant B reads zero rows,
updates and deletes nothing (on an append-only table the app role may not update
or delete at all), and cannot insert a row carrying A's tenant_id. A
platform-operator session with no tenant sees nothing and raises nothing.
"""

import pytest
from sqlalchemy import Engine, Executable, Table, delete, func, insert, select, update
from sqlalchemy.exc import ProgrammingError

from tests.helpers import make_tenant, platform_ctx, system_ctx
from tests.isolation import factories
from xolotl.models import metadata
from xolotl.modules.tenants.models import Tenant
from xolotl.platform.db import tenant_session
from xolotl.platform.tenancy.registry import APPEND_ONLY_TABLES, SHARED_TABLES
from xolotl.platform.tenancy.testing import isolation_factories

factories.register_all()


def _tenant_tables() -> list[Table]:
    return [
        t
        for t in metadata().sorted_tables
        if "tenant_id" in t.c and t.name not in SHARED_TABLES
    ]


def _count(table: Table, tenant_id, *, ctx=None) -> int:
    with tenant_session(ctx or system_ctx(tenant_id)) as s:
        return s.scalar(select(func.count()).select_from(table)) or 0


def _only_row(table: Table, tenant_id) -> dict[str, object]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return dict(s.execute(select(table)).mappings().one())


def _changes_nothing(table: Table, statement: Executable, tenant_id) -> None:
    # Row-level security leaves the statement no row of another tenant to match. On
    # an append-only table the app role holds no UPDATE or DELETE privilege, so the
    # statement is refused before row security is even consulted.
    if table.name in APPEND_ONLY_TABLES:
        with (
            pytest.raises(ProgrammingError, match="permission denied"),
            tenant_session(system_ctx(tenant_id)) as s,
        ):
            _ = s.connection().execute(statement)
        return
    with tenant_session(system_ctx(tenant_id)) as s:
        assert s.connection().execute(statement).rowcount == 0


def test_every_tenant_table_has_a_factory():
    missing = {t.name for t in _tenant_tables()} - set(isolation_factories())
    assert not missing, f"register an isolation factory for: {sorted(missing)}"


@pytest.mark.parametrize("table_name", [t.name for t in _tenant_tables()])
def test_tenant_b_cannot_see_touch_or_forge_tenant_a_rows(engine: Engine, table_name):
    a, b = make_tenant("alpha"), make_tenant("beta")
    table = metadata().tables[table_name]
    factory = isolation_factories()[table_name]

    with tenant_session(system_ctx(a)) as s:
        factory(s, a)
    assert _count(table, a) == 1
    row = _only_row(table, a)

    with tenant_session(system_ctx(b)) as s:
        assert s.scalar(select(func.count()).select_from(table)) == 0
    _changes_nothing(table, update(table).values(tenant_id=b), b)
    _changes_nothing(table, delete(table), b)

    # Forge A's own row as B with a plain Core INSERT (no RETURNING), so the table's
    # own WITH CHECK is what rejects it. The factory would insert parent rows first
    # (the error would then name the parent), and an ORM INSERT ... RETURNING makes
    # Postgres re-apply USING instead of WITH CHECK.
    with (
        pytest.raises(ProgrammingError, match=f'policy for table "{table_name}"'),
        tenant_session(system_ctx(b)) as s,
    ):
        _ = s.connection().execute(insert(table).values(row))

    assert _count(table, a) == 1
    assert _count(table, b) == 0


@pytest.mark.parametrize("table_name", [t.name for t in _tenant_tables()])
def test_platform_operator_without_tenant_sees_nothing(engine: Engine, table_name):
    a = make_tenant("alpha")
    table = metadata().tables[table_name]
    with tenant_session(system_ctx(a)) as s:
        isolation_factories()[table_name](s, a)
    assert _count(table, a, ctx=platform_ctx()) == 0


def test_tenant_rows_are_visible_only_to_themselves_and_the_platform(engine: Engine):
    a, b = make_tenant("alpha"), make_tenant("beta")
    with tenant_session(system_ctx(a)) as s:
        assert [t.id for t in s.scalars(select(Tenant))] == [a]
    with tenant_session(system_ctx(b)) as s:
        assert [t.id for t in s.scalars(select(Tenant))] == [b]
    with tenant_session(platform_ctx()) as s:
        assert {t.id for t in s.scalars(select(Tenant))} == {a, b}


# Append-only tables are left out: the app role may not UPDATE them at all, which
# the test above pins, so an UPDATE never reaches the policy.
@pytest.mark.parametrize(
    "table_name",
    [t.name for t in _tenant_tables() if t.name not in APPEND_ONLY_TABLES],
)
def test_tenant_cannot_move_its_row_to_another_tenant(engine: Engine, table_name):
    a, b = make_tenant("alpha"), make_tenant("beta")
    table = metadata().tables[table_name]
    with tenant_session(system_ctx(a)) as s:
        isolation_factories()[table_name](s, a)
    with (
        pytest.raises(ProgrammingError, match=f'policy for table "{table_name}"'),
        tenant_session(system_ctx(a)) as s,
    ):
        _ = s.execute(update(table).values(tenant_id=b))
    assert _count(table, a) == 1
