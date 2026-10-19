from datetime import date

from sqlalchemy import Engine, UniqueConstraint, text

from xolotl.platform.audit.partitions import (
    ensure_partitions,
    next_month,
    partition_name,
)
from xolotl.platform.db import APP_ROLE
from xolotl.platform.tenancy.ddl import (
    APPEND_ONLY_PRIVILEGES,
    TENANT_PREDICATE,
    restrictive_policy_statement,
    rls_statements,
    shared_grant_statements,
    tenant_fk,
    tenant_table_args,
)


def test_rls_statements_enable_force_policy_and_grant():
    stmts = rls_statements("widget")
    assert stmts[0] == "ALTER TABLE widget ENABLE ROW LEVEL SECURITY"
    assert stmts[1] == "ALTER TABLE widget FORCE ROW LEVEL SECURITY"
    assert stmts[2] == (
        "CREATE POLICY tenant_isolation ON widget AS PERMISSIVE FOR ALL "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    assert stmts[3] == f"GRANT SELECT, INSERT, UPDATE, DELETE ON widget TO {APP_ROLE}"
    append_only = rls_statements("ledger", grant=APPEND_ONLY_PRIVILEGES)
    assert append_only[3] == f"GRANT SELECT, INSERT ON ledger TO {APP_ROLE}"
    assert len(rls_statements("widget", grant=None)) == 3


def test_tenant_predicate_uses_nullif():
    assert TENANT_PREDICATE == (
        "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    )


def test_restrictive_policy_statement():
    assert restrictive_policy_statement("c", "c_vis", "visibility = 'TEAM'") == (
        "CREATE POLICY c_vis ON c AS RESTRICTIVE FOR ALL "
        "USING (visibility = 'TEAM') WITH CHECK (visibility = 'TEAM')"
    )


def test_shared_grants_follow_the_registry():
    assert shared_grant_statements("api_key_lookup") == [
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON api_key_lookup TO {APP_ROLE}"
    ]
    assert shared_grant_statements("policy_pack") == [
        f"GRANT SELECT ON policy_pack TO {APP_ROLE}"
    ]


def test_tenant_fk_is_composite():
    fk = tenant_fk("staff_user_id", "staff_user")
    assert list(fk.column_keys) == ["tenant_id", "staff_user_id"]
    assert [e.target_fullname for e in fk.elements] == [
        "staff_user.tenant_id",
        "staff_user.id",
    ]


def test_tenant_table_args_adds_no_duplicate_unique():
    # The primary key (tenant_id, id) already is the composite-FK target; Postgres
    # discards a UNIQUE (tenant_id, id) declared next to it.
    extra = UniqueConstraint("tenant_id", "email", name="uq_widget_tenant_id_email")
    assert tenant_table_args("widget") == ()
    assert tenant_table_args("widget", extra) == (extra,)


def test_partition_naming_and_month_arithmetic():
    assert partition_name(date(2026, 10, 1)) == "audit_event_y2026m10"
    assert next_month(date(2026, 12, 15)) == date(2027, 1, 1)
    assert next_month(date(2026, 1, 31)) == date(2026, 2, 1)


def test_partition_bounds_are_utc_whatever_the_session_time_zone(admin_engine: Engine):
    # The connection never commits, so the partition is rolled back on exit.
    with admin_engine.connect() as conn:
        _ = conn.execute(text("SET LOCAL TimeZone = 'America/Mexico_City'"))
        report = ensure_partitions(conn, months_ahead=0, start=date(2032, 3, 1))
        # Postgres renders a stored timestamptz in the session zone; read it in UTC.
        _ = conn.execute(text("SET LOCAL TimeZone = 'UTC'"))
        bounds = conn.scalar(
            text(
                """
                SELECT pg_get_expr(c.relpartbound, c.oid)
                FROM pg_class c WHERE c.relname = 'audit_event_y2032m03'
                """
            )
        )
    assert report.created == ["audit_event_y2032m03"]
    assert bounds == (
        "FOR VALUES FROM ('2032-03-01 00:00:00+00') TO ('2032-04-01 00:00:00+00')"
    )
