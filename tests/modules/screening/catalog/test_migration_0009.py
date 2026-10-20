"""Migration 0009: the shared list tables, subscriptions and private lists.

The tests that move the session database back to 0008 and forward again do it with
real commits; the ``database`` fixture always brings it back to head afterwards, so
the rest of the session still sees a database at head.
"""

from collections.abc import Generator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from xolotl.platform import migrate
from xolotl.platform.db import APP_ROLE
from xolotl.platform.ids import uuid7

REVISION, BEFORE = "0009_lists", "0008_party"
SHARED = ("list_source", "list_version", "list_entry", "list_entry_delta")
TENANT = (
    "tenant_list_subscription",
    "private_list",
    "private_list_version",
    "private_list_entry",
    "private_list_entry_delta",
)
OPERATIONAL = (
    "next_poll_at",
    "lease_until",
    "lease_holder",
    "etag",
    "last_modified",
    "last_attempt_at",
    "last_success_at",
    "last_change_at",
    "last_error",
    "consecutive_failures",
)
CATALOG = ("url", "title", "list_kind", "format", "poll_interval", "enabled")
FETCHED_AT = datetime(2026, 10, 19, tzinfo=UTC)


def go_down(revision: str) -> None:
    command.downgrade(migrate.alembic_config(), revision)


def tables_present(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(inspect(conn).get_table_names()) & {*SHARED, *TENANT}


# The test database, which a test may move back in time; it always ends at head.
@pytest.fixture
def database(admin_engine: Engine) -> Generator[Engine]:
    try:
        yield admin_engine
    finally:
        migrate.upgrade()


def _source(conn: Connection, code: str = "UN_CONSOLIDATED") -> None:
    _ = conn.execute(
        text(
            """
            INSERT INTO list_source
                (code, list_kind, title, jurisdiction, format, url, poll_interval)
            VALUES (:code, 'SANCTION', 'UN', 'UN', 'UN_XML', 'https://un.test/x',
                    interval '15 minutes')
            """
        ),
        {"code": code},
    )


def _version(
    conn: Connection, *, seq: int, status: str = "ACTIVE", sha: bytes = b"\x01" * 32
) -> UUID:
    version_id = uuid7()
    _ = conn.execute(
        text(
            """
            INSERT INTO list_version
                (id, list_code, seq, status, parser_version, fetched_at, loaded_by,
                 file_sha256, file_size, storage_key, entry_count, added, changed,
                 removed)
            VALUES (:id, 'UN_CONSOLIDATED', :seq, :status, 'un-xml/1', :at,
                    'cli:test', :sha, 10, 'lists/UN_CONSOLIDATED/x.xml', 1, 1, 0, 0)
            """
        ),
        {"id": version_id, "seq": seq, "status": status, "at": FETCHED_AT, "sha": sha},
    )
    return version_id


def _entry(conn: Connection, *, key: str = "QDi.1", from_seq: int = 1) -> UUID:
    entry_id = uuid7()
    _ = conn.execute(
        text(
            """
            INSERT INTO list_entry
                (id, list_code, source_entry_key, content_hash, entity_type,
                 primary_name, screenable, entry, from_seq)
            VALUES (:id, 'UN_CONSOLIDATED', :key, :hash, 'PERSON', 'A', true,
                    '{}'::jsonb, :from_seq)
            """
        ),
        {"id": entry_id, "key": key, "hash": b"\x02" * 32, "from_seq": from_seq},
    )
    return entry_id


def test_0009_follows_0008():
    script = ScriptDirectory.from_config(migrate.alembic_config())
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == BEFORE


def test_downgrade_drops_the_nine_tables_and_upgrade_recreates_them(
    database: Engine,
):
    go_down(BEFORE)
    assert tables_present(database) == set()
    migrate.upgrade()
    assert tables_present(database) == {*SHARED, *TENANT}


def test_tenant_tables_force_rls_and_shared_ones_have_none(database: Engine):
    with database.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
                WHERE relname = ANY(:names) AND relkind = 'r'
                """
            ),
            {"names": [*SHARED, *TENANT]},
        ).all()
    flags = {row[0]: (row[1], row[2]) for row in rows}
    assert flags == {
        **dict.fromkeys(SHARED, (False, False)),
        **dict.fromkeys(TENANT, (True, True)),
    }


def test_pg_trgm_is_installed(database: Engine):
    with database.connect() as conn:
        assert conn.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"))


def _may(conn: Connection, table: str, column: str, privilege: str) -> bool:
    return bool(
        conn.scalar(
            text("SELECT has_column_privilege(:role, :table, :column, :privilege)"),
            {
                "role": APP_ROLE,
                "table": table,
                "column": column,
                "privilege": privilege,
            },
        )
    )


def _may_table(conn: Connection, table: str, privilege: str) -> bool:
    return bool(
        conn.scalar(
            text("SELECT has_table_privilege(:role, :table, :privilege)"),
            {"role": APP_ROLE, "table": table, "privilege": privilege},
        )
    )


def test_the_app_role_may_update_only_the_operational_columns_of_list_source(
    database: Engine,
):
    with database.connect() as conn:
        assert all(_may(conn, "list_source", c, "UPDATE") for c in OPERATIONAL)
        assert not any(_may(conn, "list_source", c, "UPDATE") for c in CATALOG)
        assert _may_table(conn, "list_source", "SELECT")
        assert not _may_table(conn, "list_source", "INSERT")
        assert not _may_table(conn, "list_source", "DELETE")


def test_the_app_role_may_update_only_status_and_to_seq(database: Engine):
    with database.connect() as conn:
        assert _may(conn, "list_version", "status", "UPDATE")
        assert not _may(conn, "list_version", "entry_count", "UPDATE")
        assert _may(conn, "list_entry", "to_seq", "UPDATE")
        assert not _may(conn, "list_entry", "entry", "UPDATE")
        for table in ("list_version", "list_entry", "list_entry_delta"):
            assert _may_table(conn, table, "SELECT"), table
            assert _may_table(conn, table, "INSERT"), table
            assert not _may_table(conn, table, "DELETE"), table
        assert not _may_table(conn, "list_entry_delta", "UPDATE")


def test_only_one_active_version_and_one_current_entry_per_key(admin_engine: Engine):
    with admin_engine.begin() as conn:
        _source(conn)
        _ = _version(conn, seq=1)
        _ = _entry(conn)
    with (
        pytest.raises(IntegrityError, match="ux_list_version_active"),
        admin_engine.begin() as conn,
    ):
        _ = _version(conn, seq=2, sha=b"\x03" * 32)
    with (
        pytest.raises(IntegrityError, match="ux_list_entry_current"),
        admin_engine.begin() as conn,
    ):
        _ = _entry(conn, from_seq=2)


def test_the_delta_check_refuses_an_added_row_with_a_previous_entry(
    admin_engine: Engine,
):
    with admin_engine.begin() as conn:
        _source(conn)
        version_id = _version(conn, seq=1)
        entry_id = _entry(conn)
    statement = text(
        """
        INSERT INTO list_entry_delta
            (version_id, source_entry_key, change, prev_entry_id, new_entry_id)
        VALUES (:version_id, 'QDi.1', 'ADDED', :prev, :new)
        """
    )
    with (
        pytest.raises(IntegrityError, match="ck_list_entry_delta_change"),
        admin_engine.begin() as conn,
    ):
        _ = conn.execute(
            statement, {"version_id": version_id, "prev": entry_id, "new": entry_id}
        )
    with admin_engine.begin() as conn:
        _ = conn.execute(
            statement, {"version_id": version_id, "prev": None, "new": entry_id}
        )


def test_private_list_codes_and_kinds_are_checked(admin_engine: Engine):
    statement = text(
        """
        INSERT INTO private_list
            (tenant_id, id, list_code, list_kind, title, created_by)
        VALUES (:tenant_id, :id, :code, :kind, 't', 'staff:test')
        """
    )
    accepted = (("MX_LPB", "BLOCKED"), ("INTERNAL_VIP", "PEP"))
    refused = (("MX_LPB", "SANCTION"), ("INTERNAL_VIP", "BLOCKED"), ("OTHER", "PEP"))
    for code, kind in accepted:
        with admin_engine.begin() as conn:
            _ = conn.execute(
                statement,
                {"tenant_id": uuid7(), "id": uuid7(), "code": code, "kind": kind},
            )
    for code, kind in refused:
        with (
            pytest.raises(IntegrityError, match="ck_private_list_code_kind"),
            admin_engine.begin() as conn,
        ):
            _ = conn.execute(
                statement,
                {"tenant_id": uuid7(), "id": uuid7(), "code": code, "kind": kind},
            )


def test_a_reverted_file_may_be_stored_again(admin_engine: Engine):
    # Deviation 2: A, B, A is a real change, so one sha256 may appear twice.
    with admin_engine.begin() as conn:
        _source(conn)
        _ = _version(conn, seq=1, status="SUPERSEDED", sha=b"\x0a" * 32)
        _ = _version(conn, seq=2, status="SUPERSEDED", sha=b"\x0b" * 32)
        _ = _version(conn, seq=3, sha=b"\x0a" * 32)
    with admin_engine.connect() as conn:
        count = conn.scalar(
            text("SELECT count(*) FROM list_version WHERE file_sha256 = :sha"),
            {"sha": b"\x0a" * 32},
        )
    assert count == 2
