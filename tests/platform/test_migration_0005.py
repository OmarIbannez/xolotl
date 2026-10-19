"""Migration 0005: the replay cache of a database that stored it in the clear.

``idempotency_key.response_body`` was JSONB: the first response of every ``POST`` for 24
hours, a new API key and a new member's initial password among them. 0005 empties the
table, with no backfill, and makes the column ``bytea`` for the ciphertext of
``EncryptedJson``. The rows are a replay cache: losing them on upgrade only means a
client that retries across the upgrade runs its request again.

The session database runs every migration once, on an empty schema. These tests start
from a database as 0004 left it, fill it with plaintext rows through the owner's
connection, and migrate across 0005 in both directions with real commits. Whatever a
test does, the database is brought back to head afterwards (the ``database`` fixture),
so these tests can run in any order and leave the rest of the session a database at
head. The last test runs the migration as an owner that row-level security binds, which
is what a managed server hands out.
"""

import json
from collections.abc import Generator
from uuid import UUID

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, inspect, make_url, text

from tests.helpers import everything_stored, leaked
from xolotl.platform import db, migrate
from xolotl.platform.ids import uuid7

REVISION = "0005_encrypt_idempotency"
BEFORE = "0004_encrypt_totp"
INDEX = "ix_idempotency_key_tenant_id_expires_at"
SECRET = "xk_sbx_0badcafe_00112233445566778899aabbccddeeff"  # a key a client was given


def go_down(revision: str) -> None:
    command.downgrade(migrate.alembic_config(), revision)


# The test database, which a test may move back in time; it always ends at head.
@pytest.fixture
def database(admin_engine: Engine) -> Generator[Engine]:
    try:
        yield admin_engine
    finally:
        migrate.upgrade()


# ---- rows as 0004 left them ----------------------------------------------------------


def seed_tenant(conn: Connection, slug: str) -> UUID:
    tenant_id = uuid7()
    _ = conn.execute(
        text(
            """
            INSERT INTO tenant (id, slug, legal_name, country_code, env, status)
            VALUES (:id, :slug, :name, 'MX', 'SANDBOX', 'ACTIVE')
            """
        ),
        {"id": tenant_id, "slug": slug, "name": slug.title()},
    )
    return tenant_id


def seed_cached_response(
    conn: Connection, tenant_id: UUID, key: str, *, status: int = 201
) -> None:
    """Add a row the way 1a stored it: a JSON record, with a secret in it.

    A ``status`` of 0 is a reservation whose request is still running; it holds ``{}``.
    """
    record = {} if status == 0 else {"status": status, "body": {"key": SECRET}}
    _ = conn.execute(
        text(
            """
            INSERT INTO idempotency_key
                (tenant_id, actor_id, key, request_hash, status_code, response_body,
                 expires_at)
            VALUES
                (:tenant_id, 'staff:1', :key, :digest, :status,
                 CAST(:record AS jsonb), now() + interval '1 day')
            """
        ),
        {
            "tenant_id": tenant_id,
            "key": key,
            "digest": b"\x00" * 32,
            "status": status,
            "record": json.dumps(record),
        },
    )


def seed_sealed_row(conn: Connection, tenant_id: UUID, key: str) -> None:
    """Add a row the way 0005 stores them: ``bytea`` that is not JSON."""
    _ = conn.execute(
        text(
            """
            INSERT INTO idempotency_key
                (tenant_id, actor_id, key, request_hash, status_code, response_body,
                 expires_at)
            VALUES
                (:tenant_id, 'staff:1', :key, :digest, 201, :sealed,
                 now() + interval '1 day')
            """
        ),
        {
            "tenant_id": tenant_id,
            "key": key,
            "digest": b"\x00" * 32,
            "sealed": b"\x00\x01" + bytes(range(60)),
        },
    )


# ---- what the database holds ---------------------------------------------------------


def row_count(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM idempotency_key")).scalar_one()


# The type and nullability of idempotency_key.response_body.
def body_column(engine: Engine) -> tuple[str, bool]:
    columns = {c["name"]: c for c in inspect(engine).get_columns("idempotency_key")}
    column = columns["response_body"]
    return str(column["type"]), column["nullable"]


# Everything else about the table that the migration must leave as it was.
def assert_table_otherwise_unchanged(engine: Engine) -> None:
    inspector = inspect(engine)
    indexes = {
        i["name"]: i["column_names"] for i in inspector.get_indexes("idempotency_key")
    }
    assert indexes[INDEX] == ["tenant_id", "expires_at"]
    primary_key = inspector.get_pk_constraint("idempotency_key")
    assert primary_key["name"] == "pk_idempotency_key"
    assert primary_key["constrained_columns"] == ["tenant_id", "actor_id", "key"]
    columns = {c["name"]: c for c in inspector.get_columns("idempotency_key")}
    assert set(columns) == {
        "tenant_id",
        "actor_id",
        "key",
        "request_hash",
        "status_code",
        "response_body",
        "created_at",
        "expires_at",
    }
    assert all(not c["nullable"] for c in columns.values())
    with engine.connect() as conn:
        forced = conn.execute(
            text(
                """
                SELECT relrowsecurity, relforcerowsecurity FROM pg_class
                WHERE oid = 'idempotency_key'::regclass
                """
            )
        ).one()
        policies = conn.scalars(
            text(
                """
                SELECT polname FROM pg_policy
                WHERE polrelid = 'idempotency_key'::regclass
                """
            )
        ).all()
        privileges = conn.execute(
            text(
                """
                SELECT has_table_privilege(:app, 'idempotency_key', 'SELECT'),
                       has_table_privilege(:app, 'idempotency_key', 'INSERT'),
                       has_table_privilege(:app, 'idempotency_key', 'UPDATE'),
                       has_table_privilege(:app, 'idempotency_key', 'DELETE')
                """
            ),
            {"app": db.APP_ROLE},
        ).one()
    assert tuple(forced) == (True, True)
    assert policies == ["tenant_isolation"]
    assert tuple(privileges) == (True, True, True, True)


# ---- the revision chain --------------------------------------------------------------


def test_0005_follows_0004_and_is_the_only_head():
    script = ScriptDirectory.from_config(migrate.alembic_config())
    assert script.get_heads() == [REVISION]
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == BEFORE


# ---- upgrading a database that stored the cache in the clear -------------------------


def test_upgrading_discards_the_plaintext_cache_and_makes_the_column_bytea(
    database: Engine,
):
    go_down(BEFORE)
    with database.begin() as conn:
        ana, bob = seed_tenant(conn, "ana"), seed_tenant(conn, "bob")
        seed_cached_response(conn, ana, "k-ana")
        seed_cached_response(conn, ana, "k-running", status=0)
        seed_cached_response(conn, bob, "k-bob")
    # The premise: the secret really is in the table, in a JSONB column.
    assert body_column(database) == ("JSONB", False)
    assert row_count(database) == 3
    assert leaked(everything_stored(database), SECRET.encode()) != []

    migrate.upgrade()

    assert row_count(database) == 0  # no backfill: nothing is carried over
    assert body_column(database) == ("BYTEA", False)
    assert leaked(everything_stored(database), SECRET.encode()) == []
    assert_table_otherwise_unchanged(database)
    with database.connect() as conn:
        version = conn.scalar(text("SELECT version_num FROM alembic_version"))
    assert version == REVISION


def test_upgrading_an_empty_table_changes_only_the_column(database: Engine):
    go_down(BEFORE)
    assert row_count(database) == 0

    migrate.upgrade()

    assert body_column(database) == ("BYTEA", False)
    assert_table_otherwise_unchanged(database)


def test_the_upgrade_replaces_the_tables_storage_so_no_dead_row_keeps_the_secret(
    database: Engine,
):
    # A DELETE leaves its rows in the heap until a vacuum; the table is rewritten.
    go_down(BEFORE)
    with database.begin() as conn:
        seed_cached_response(conn, seed_tenant(conn, "ana"), "k-ana")
    with database.connect() as conn:
        before = conn.scalar(text("SELECT pg_relation_filenode('idempotency_key')"))

    migrate.upgrade()

    with database.connect() as conn:
        after = conn.scalar(text("SELECT pg_relation_filenode('idempotency_key')"))
    assert after != before


# ---- going back ----------------------------------------------------------------------


def test_downgrading_restores_jsonb_and_discards_what_it_cannot_convert(
    database: Engine,
):
    with database.begin() as conn:  # at head: bytea that is not JSON
        ana = seed_tenant(conn, "ana")
        seed_sealed_row(conn, ana, "k-1")
        seed_sealed_row(conn, ana, "k-2")
    assert body_column(database) == ("BYTEA", False)

    go_down(BEFORE)

    assert row_count(database) == 0
    assert body_column(database) == ("JSONB", False)
    assert_table_otherwise_unchanged(database)
    with database.connect() as conn:
        version = conn.scalar(text("SELECT version_num FROM alembic_version"))
    assert version == BEFORE

    # And the 1a column works again, and goes forward again.
    with database.begin() as conn:
        seed_cached_response(conn, seed_tenant(conn, "bob"), "k-bob")
    migrate.upgrade()
    assert row_count(database) == 0
    assert body_column(database) == ("BYTEA", False)


# ---- the owner a managed server gives you --------------------------------------------

SCRATCH_DATABASE = "xolotl_b8_scratch"
SCRATCH_OWNER = "xolotl_b8_owner"


def drop_scratch(admin_engine: Engine) -> None:
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        _ = conn.execute(
            text(f"DROP DATABASE IF EXISTS {SCRATCH_DATABASE} WITH (FORCE)")
        )
        _ = conn.execute(text(f"DROP ROLE IF EXISTS {SCRATCH_OWNER}"))


@pytest.fixture
def bound_owner_database(
    admin_engine: Engine, admin_url: str, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[Engine, Engine]]:
    """Create a database owned by a role that row-level security binds.

    The session database belongs to a superuser, whom row-level security never binds,
    so a migration that deletes rows without naming a tenant passes there and finds none
    on a managed server (an owner with neither SUPERUSER nor BYPASSRLS, which is what
    ``bootstrap`` is documented to need). The migrations run against this one as that
    owner: the alembic environment takes its engine from ``db``.

    Yields:
        The owner's engine and a superuser's engine on the same database.

    """
    drop_scratch(admin_engine)  # whatever an interrupted run left behind
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        _ = conn.execute(
            text(
                f"""
                CREATE ROLE {SCRATCH_OWNER} LOGIN PASSWORD 'owner-password'
                NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
                """
            )
        )
        _ = conn.execute(
            text(f"CREATE DATABASE {SCRATCH_DATABASE} OWNER {SCRATCH_OWNER}")
        )
    root_url = make_url(admin_url).set(database=SCRATCH_DATABASE)
    owner_url = root_url.set(username=SCRATCH_OWNER, password="owner-password")
    owner = create_engine(owner_url, hide_parameters=True)
    root = create_engine(root_url, hide_parameters=True)
    monkeypatch.setattr(db, "get_admin_engine", lambda: owner)
    try:
        yield owner, root
    finally:
        owner.dispose()
        root.dispose()
        drop_scratch(admin_engine)


def test_an_owner_that_row_level_security_binds_still_empties_the_table(
    bound_owner_database: tuple[Engine, Engine],
):
    owner, root = bound_owner_database
    config = migrate.alembic_config()
    command.upgrade(config, BEFORE)
    with root.begin() as conn:
        ana, bob = seed_tenant(conn, "ana"), seed_tenant(conn, "bob")
        seed_cached_response(conn, ana, "k-ana")
        seed_cached_response(conn, bob, "k-bob")
    # The premise: this owner sees no tenant's rows, so a DELETE that names no tenant
    # would delete none of them, and the column would then be converted around them.
    assert row_count(owner) == 0
    assert row_count(root) == 2

    command.upgrade(config, "head")

    assert row_count(root) == 0
    assert body_column(root) == ("BYTEA", False)
    assert leaked(everything_stored(root), SECRET.encode()) == []
    assert_table_otherwise_unchanged(root)

    with root.begin() as conn:
        seed_sealed_row(conn, ana, "k-1")
    command.downgrade(config, BEFORE)

    assert row_count(root) == 0
    assert body_column(root) == ("JSONB", False)
    assert_table_otherwise_unchanged(root)
