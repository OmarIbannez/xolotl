"""Structural half of the isolation harness (spec §4.4, items 1 and 3).

``SHARED_TABLES`` is the authority. A table listed there is not tenant-isolated and
must carry no row security, even when it references tenants (``api_key_lookup`` has
a ``tenant_id`` column). Every other base table in ``public`` is tenant-scoped, with
``tenant_id`` (``id`` for ``tenant``), forced RLS and exactly one permissive
``tenant_isolation`` policy. The application role owns nothing, is a member of no
owner role, and is neither a superuser nor ``BYPASSRLS``, so it cannot bypass RLS.
On tenant tables it holds exactly SELECT, INSERT, UPDATE and DELETE, or only SELECT
and INSERT on the append-only ones (``APPEND_ONLY_TABLES``), and it holds nothing on
any partition.
"""

from datetime import UTC, datetime

from sqlalchemy import Engine, text

from xolotl.platform.audit.partitions import next_month, partition_name
from xolotl.platform.db import APP_ROLE
from xolotl.platform.tenancy.registry import APPEND_ONLY_TABLES, SHARED_TABLES

RELATIONS = text(
    """
    SELECT c.oid, c.relname, c.relrowsecurity, c.relforcerowsecurity, c.relispartition,
           EXISTS (SELECT 1 FROM pg_attribute a
                   WHERE a.attrelid = c.oid AND a.attname = 'tenant_id'
                     AND NOT a.attisdropped) AS has_tenant_id
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
    ORDER BY c.relname
    """
)
POLICIES = text(
    """
    SELECT polname, polpermissive, polcmd,
           pg_get_expr(polqual, polrelid) AS using_expr,
           pg_get_expr(polwithcheck, polrelid) AS check_expr
    FROM pg_policy WHERE polrelid = :oid ORDER BY polname
    """
)
OWNER_ACCESS = text(
    """
    SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
      AND pg_has_role(:role, c.relowner, 'MEMBER')
    ORDER BY c.relname
    """
)
APP_PRIVILEGES = text(
    """
    SELECT c.relname,
           array_agg(p ORDER BY p) FILTER (WHERE has_table_privilege(:role, c.oid, p))
               AS granted
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE',
                            'REFERENCES', 'TRIGGER']) AS p
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition
    GROUP BY c.relname
    """
)
PARTITION_PRIVILEGES = text(
    """
    SELECT c.relname || ': ' || p
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE',
                            'REFERENCES', 'TRIGGER']) AS p
    WHERE n.nspname = 'public' AND c.relispartition
      AND has_table_privilege(:role, c.oid, p)
    ORDER BY 1
    """
)


def _relations(engine: Engine):
    with engine.connect() as conn:
        return conn.execute(RELATIONS).mappings().all()


def _tenant_scoped(row) -> bool:
    if row["relname"] in SHARED_TABLES:
        # The registry decides: a shared table may reference tenants without being
        # isolated; test_shared_tables_carry_no_row_security holds it to that.
        return False
    return bool(row["has_tenant_id"]) or row["relname"] == "tenant"


def test_every_table_has_tenant_id_or_is_registered_shared(admin_engine: Engine):
    rows = _relations(admin_engine)
    without = {r["relname"] for r in rows if not _tenant_scoped(r)}
    unregistered = without - SHARED_TABLES
    stale = SHARED_TABLES - without
    assert not unregistered, (
        f"tables without tenant_id not in SHARED_TABLES: {unregistered}"
    )
    assert not stale, f"SHARED_TABLES lists tables that do not exist in public: {stale}"


def test_shared_tables_carry_no_row_security(admin_engine: Engine):
    with admin_engine.connect() as conn:
        shared = {
            row["relname"]: row
            for row in conn.execute(RELATIONS).mappings().all()
            if row["relname"] in SHARED_TABLES
        }
        for name in sorted(SHARED_TABLES):
            assert name in shared, f"{name} is in SHARED_TABLES but is not a table"
            row = shared[name]
            assert not row["relrowsecurity"], f"{name} is shared but has RLS enabled"
            policies = conn.execute(POLICIES, {"oid": row["oid"]}).mappings().all()
            assert not policies, (
                f"{name} is shared but has policies {[p['polname'] for p in policies]}"
            )


def test_tenant_tables_force_rls_with_exactly_one_permissive_policy(
    admin_engine: Engine,
):
    reference: str | None = None
    with admin_engine.connect() as conn:
        for row in conn.execute(RELATIONS).mappings().all():
            if not _tenant_scoped(row):
                continue
            name = row["relname"]
            assert row["relrowsecurity"], (
                f"{name} must ENABLE and FORCE ROW LEVEL SECURITY"
            )
            assert row["relforcerowsecurity"], (
                f"{name} must ENABLE and FORCE ROW LEVEL SECURITY"
            )
            policies = conn.execute(POLICIES, {"oid": row["oid"]}).mappings().all()
            permissive = [p for p in policies if p["polpermissive"]]
            assert [p["polname"] for p in permissive] == ["tenant_isolation"], (
                f"{name}: permissive policies {[p['polname'] for p in permissive]}"
            )
            policy = permissive[0]
            assert policy["polcmd"] == "*", f"{name}: tenant_isolation must be FOR ALL"
            assert policy["using_expr"] == policy["check_expr"], (
                f"{name}: USING != WITH CHECK"
            )
            if name == "tenant":
                assert "PLATFORM_OPERATOR" in policy["using_expr"]
                continue
            lowered = policy["using_expr"].lower()
            assert "nullif" in lowered, f"{name}: {policy['using_expr']}"
            assert "app.tenant_id" in lowered, f"{name}: {policy['using_expr']}"
            reference = reference or policy["using_expr"]
            assert policy["using_expr"] == reference, (
                f"{name}: predicate differs from the kernel predicate {reference}"
            )


def test_app_role_owns_nothing_and_cannot_bypass_rls(admin_engine: Engine):
    with admin_engine.connect() as conn:
        # A superuser or BYPASSRLS role ignores row security altogether.
        bypass = conn.scalar(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = :role"),
            {"role": APP_ROLE},
        )
        # Owning a table, or any membership of its owner role (inherited, or reachable
        # with SET ROLE), lets the app role switch row security off.
        as_owner = conn.scalars(OWNER_ACCESS, {"role": APP_ROLE}).all()
    assert bypass is False, f"{APP_ROLE} must be neither superuser nor BYPASSRLS"
    assert as_owner == [], f"{APP_ROLE} can act as the owner of {as_owner}"


def test_app_role_privileges_on_tenant_tables_follow_the_registry(
    admin_engine: Engine,
):
    with admin_engine.connect() as conn:
        granted = {
            row["relname"]: set(row["granted"] or ())
            for row in conn.execute(APP_PRIVILEGES, {"role": APP_ROLE}).mappings()
        }
        tenant_tables = sorted(
            row["relname"]
            for row in conn.execute(RELATIONS).mappings()
            if _tenant_scoped(row) and not row["relispartition"]
        )
    assert "audit_event" in tenant_tables
    for name in tenant_tables:
        expected = (
            {"SELECT", "INSERT"}
            if name in APPEND_ONLY_TABLES
            else {"SELECT", "INSERT", "UPDATE", "DELETE"}
        )
        assert granted[name] == expected, f"{name}: {sorted(granted[name])}"


def test_app_role_holds_no_privilege_on_any_partition(admin_engine: Engine):
    # The app role reaches audit rows through the parent table only.
    with admin_engine.connect() as conn:
        granted = conn.scalars(PARTITION_PRIVILEGES, {"role": APP_ROLE}).all()
    assert granted == [], f"{APP_ROLE} holds {granted}"


def test_audit_partitions_exist_for_this_month_and_three_ahead(admin_engine: Engine):
    month = datetime.now(UTC).date().replace(day=1)
    expected = {"audit_event_default"}
    for _ in range(4):
        expected.add(partition_name(month))
        month = next_month(month)
    names = {r["relname"] for r in _relations(admin_engine)}
    assert expected <= names, f"missing partitions: {expected - names}"
