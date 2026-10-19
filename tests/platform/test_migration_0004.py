"""Migration 0004: the TOTP secrets of a database that predates data protection.

The session database runs every migration once, on an empty schema. These tests start
from a database as an earlier release left it, at revision 0002 (no tenant keys) or
0003 (keys, plaintext secrets), fill it through the owner's connection, and migrate
across 0004 in both directions with real commits. Whatever a test does, the database
is brought back to head afterwards (the ``database`` fixture), so these tests can run
in any order and leave the rest of the session a database at head.

What each test proves about the secrets is proved from the outside: the stored bytes
are opened with the primitives and the specified authenticated data, with a key that
the test unwraps itself from ``tenant_key``, and the application's own column type is
used to read them back through the owning tenant's session.
"""

import base64
import importlib
import logging
from collections.abc import Generator
from typing import cast
from uuid import UUID

import pytest
from alembic import command
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import (
    Connection,
    Engine,
    create_engine,
    inspect,
    make_url,
    select,
    text,
)
from typer.testing import CliRunner

from tests.helpers import data_key_of, everything_stored, leaked, system_ctx
from xolotl.cli import app as cli_app
from xolotl.modules.tenants.models import StaffCredential
from xolotl.platform import db, migrate
from xolotl.platform.auth.totp import new_secret
from xolotl.platform.config import get_settings
from xolotl.platform.crypto import primitives
from xolotl.platform.crypto.encrypted import STR_CODEC, Encrypted, encrypt
from xolotl.platform.crypto.errors import DecryptionError
from xolotl.platform.crypto.primitives import (
    HEADER_LEN,
    NONCE_LEN,
    TAG_LEN,
    aad_for,
    decrypt_value,
    parse_header,
)
from xolotl.platform.crypto.provider import get_key_provider
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import KeyPurpose
from xolotl.platform.ids import uuid7

DATA = KeyPurpose.DATA
BEFORE_KEYS = "0002_events_jobs_workflows"
WITH_KEYS = "0003_tenant_key"
MIGRATION_LOGGER = "xolotl.migrations.0004_encrypt_totp"
ALL_PURPOSES = ["BIOMETRIC", "DATA", "INDEX"]


# ---- moving the database around ------------------------------------------------------


def go_down(revision: str) -> None:
    command.downgrade(migrate.alembic_config(), revision)


# The test database, which a test may move back in time; it always ends at head.
@pytest.fixture
def database(admin_engine: Engine) -> Generator[Engine]:
    try:
        yield admin_engine
    finally:
        migrate.upgrade()


# ---- rows as an earlier release left them --------------------------------------------


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


def seed_credential(conn: Connection, tenant_id: UUID, secret: str | None) -> UUID:
    """Add a staff user whose credential holds ``secret`` the way 1a stored it.

    Returns:
        The id of the staff user, which is how the tests find the credential.

    """
    user_id = uuid7()
    _ = conn.execute(
        text(
            """
            INSERT INTO staff_user
                (tenant_id, id, email, display_name, roles, status)
            VALUES
                (:tenant_id, :id, :email, 'Staff', CAST(:roles AS VARCHAR(32)[]),
                 'ACTIVE')
            """
        ),
        {
            "tenant_id": tenant_id,
            "id": user_id,
            "email": f"{user_id.hex[-12:]}@seed.mx",
            "roles": ["ADMIN"],
        },
    )
    _ = conn.execute(
        text(
            """
            INSERT INTO staff_credential
                (tenant_id, id, staff_user_id, password_hash, totp_secret,
                 totp_enabled)
            VALUES
                (:tenant_id, :id, :user_id, 'x', :secret, :enabled)
            """
        ),
        {
            "tenant_id": tenant_id,
            "id": uuid7(),
            "user_id": user_id,
            "secret": None if secret is None else secret.encode(),
            "enabled": secret is not None,
        },
    )
    return user_id


# ---- what the database holds ---------------------------------------------------------


# The ``totp_secret`` column of every credential, as stored, by staff user.
def stored_secrets(engine: Engine) -> dict[UUID, bytes | None]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT staff_user_id, totp_secret FROM staff_credential")
        ).all()
    return {user_id: None if raw is None else bytes(raw) for user_id, raw in rows}


# Every ``tenant_key`` row as ``(tenant, purpose, version, wrapped key)``.
def key_rows(engine: Engine) -> list[tuple[UUID, str, int, bytes | None]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT tenant_id, purpose, version, wrapped_dek FROM tenant_key
                ORDER BY tenant_id, purpose, version
                """
            )
        ).all()
    return [
        (tenant_id, purpose, version, None if wrapped is None else bytes(wrapped))
        for tenant_id, purpose, version, wrapped in rows
    ]


def purposes_of(engine: Engine, tenant_id: UUID) -> list[str]:
    return [row[1] for row in key_rows(engine) if row[0] == tenant_id]


# The stored secret has the layout the column type reads and opens under the tenant's
# key and nothing else, to the plaintext it replaced.
def assert_sealed(
    engine: Engine, tenant_id: UUID, user_id: UUID, plain: str, *, version: int = 1
) -> None:
    stored = stored_secrets(engine)[user_id]
    assert stored is not None
    assert stored[:HEADER_LEN] == version.to_bytes(HEADER_LEN, "big")
    assert len(stored) == HEADER_LEN + NONCE_LEN + len(plain) + TAG_LEN
    assert plain.encode() not in stored
    dek = data_key_of(engine, tenant_id, version)
    opened = decrypt_value(dek, ciphertext=stored, aad=aad_for(tenant_id, DATA))
    assert opened == plain.encode()


# The credential's secret as the application reads it: loaded and revealed through the
# session of the tenant that owns it.
def revealed_by_owner(tenant_id: UUID, user_id: UUID) -> str | None:
    with tenant_session(system_ctx(tenant_id)) as s:
        credential = s.scalars(
            select(StaffCredential).where(StaffCredential.staff_user_id == user_id)
        ).one()
        value = credential.totp_secret
        assert value is None or isinstance(value, Encrypted)
        return None if value is None else value.reveal(session=s)


# The messages of the warnings the migration logged.
def migration_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == MIGRATION_LOGGER and record.levelno == logging.WARNING
    ]


# The file the credential table is stored in; a new one means the table was rewritten.
def table_file(engine: Engine) -> int:
    with engine.connect() as conn:
        return cast(
            "int", conn.scalar(text("SELECT pg_relation_filenode('staff_credential')"))
        )


# Another tenant can neither load the credential nor open its bytes.
def assert_unreadable_to(engine: Engine, other: UUID, user_id: UUID) -> None:
    with tenant_session(system_ctx(other)) as s:
        found = s.scalars(
            select(StaffCredential).where(StaffCredential.staff_user_id == user_id)
        ).all()
        assert found == []
    stored = stored_secrets(engine)[user_id]
    assert stored is not None
    foreign = Encrypted(
        ciphertext=stored,
        tenant_id=other,
        purpose=DATA,
        version=parse_header(stored),
        _codec=STR_CODEC,
    )
    with pytest.raises(DecryptionError):
        _ = foreign.reveal()


# ---- the revision chain --------------------------------------------------------------


def test_0004_follows_0003():
    script = ScriptDirectory.from_config(migrate.alembic_config())
    revision = script.get_revision("0004_encrypt_totp")
    assert revision is not None
    assert revision.down_revision == "0003_tenant_key"


# ---- upgrading a database that predates data protection ------------------------------


def test_every_tenant_gets_keys_and_every_secret_is_sealed_under_its_own_tenant(
    database: Engine, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger=MIGRATION_LOGGER)
    plain = [new_secret() for _ in range(3)]
    go_down(BEFORE_KEYS)
    with database.begin() as conn:
        ana, bob, quiet = (seed_tenant(conn, slug) for slug in ("ana", "bob", "quiet"))
        ana_1 = seed_credential(conn, ana, plain[0])
        ana_2 = seed_credential(conn, ana, plain[1])
        ana_none = seed_credential(conn, ana, None)
        bob_1 = seed_credential(conn, bob, plain[2])
        quiet_none = seed_credential(conn, quiet, None)
    assert "tenant_key" not in inspect(database).get_table_names()  # a 1a database

    migrate.upgrade()

    for tenant_id in (ana, bob, quiet):  # even the tenant with nothing to seal
        assert purposes_of(database, tenant_id) == ALL_PURPOSES
    assert_sealed(database, ana, ana_1, plain[0])
    assert_sealed(database, ana, ana_2, plain[1])
    assert_sealed(database, bob, bob_1, plain[2])
    stored = stored_secrets(database)
    assert stored[ana_none] is None
    assert stored[quiet_none] is None
    # Each opens through its owner's session, as the application reads it ...
    assert revealed_by_owner(ana, ana_1) == plain[0]
    assert revealed_by_owner(ana, ana_2) == plain[1]
    assert revealed_by_owner(bob, bob_1) == plain[2]
    assert revealed_by_owner(ana, ana_none) is None
    # ... and for nobody else: a secret sealed under the wrong tenant's key, such as
    # the key of the tenant the migration touched last, would fail the checks above.
    assert_unreadable_to(database, bob, ana_1)
    assert_unreadable_to(database, ana, bob_1)
    assert_unreadable_to(database, quiet, ana_2)


# What this proves is what SQL and the logs can see. That the dropped column leaves no
# bytes in the table's pages is the next test's business.
def test_the_plaintext_is_no_longer_visible_in_the_schema_the_rows_or_the_logs(
    database: Engine, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="sqlalchemy.engine")
    plain = [new_secret() for _ in range(2)]
    go_down(BEFORE_KEYS)
    with database.begin() as conn:
        tenant_id = seed_tenant(conn, "ana")
        _ = seed_credential(conn, tenant_id, plain[0])
        _ = seed_credential(conn, tenant_id, plain[1])
    caplog.clear()

    migrate.upgrade()

    columns = {c["name"]: c for c in inspect(database).get_columns("staff_credential")}
    assert "totp_secret_enc" not in columns  # the working column is renamed away
    assert str(columns["totp_secret"]["type"]) == "BYTEA"
    assert columns["totp_secret"]["nullable"] is True
    assert leaked(everything_stored(database), *(s.encode() for s in plain)) == []
    # The migration runs with SQL logging at its most talkative, and says what it did.
    assert "created keys for BIOMETRIC, DATA, INDEX" in caplog.text
    assert "Row (" in caplog.text
    assert leaked(caplog.text, *(s.encode() for s in plain)) == []


# Dropping a column only hides it: its bytes stay in the table's pages, in the live rows
# and in the dead versions the sealing updates leave, for a backup or a replica to
# carry. Only a rewrite of the table drops them, and a rewrite gives the table a new
# file. (The bytes themselves were looked for in the pages with pageinspect, before and
# after the fix, by a probe that is not part of the suite: it needs a superuser and an
# extension.)
def test_the_upgrade_rewrites_the_table_that_held_the_plaintext_column(
    database: Engine,
):
    go_down(BEFORE_KEYS)
    with database.begin() as conn:
        tenant_id = seed_tenant(conn, "ana")
        _ = seed_credential(conn, tenant_id, new_secret())
        _ = seed_credential(conn, tenant_id, new_secret())
    before = table_file(database)

    migrate.upgrade()

    assert table_file(database) != before


# ---- going back and forth ------------------------------------------------------------


def test_downgrading_restores_the_plaintext_and_upgrading_seals_it_again(
    database: Engine, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger=MIGRATION_LOGGER)
    plain = [new_secret() for _ in range(3)]
    go_down(BEFORE_KEYS)
    with database.begin() as conn:
        ana, bob = seed_tenant(conn, "ana"), seed_tenant(conn, "bob")
        users = {
            plain[0]: (ana, seed_credential(conn, ana, plain[0])),
            plain[1]: (ana, seed_credential(conn, ana, plain[1])),
            plain[2]: (bob, seed_credential(conn, bob, plain[2])),
        }
        no_secret = seed_credential(conn, bob, None)
    migrate.upgrade()
    keys_before = key_rows(database)

    go_down(WITH_KEYS)

    # The 1a column, byte for byte; the keys stay because 0003 still owns them.
    restored = stored_secrets(database)
    for secret, (_, user_id) in users.items():
        assert restored[user_id] == secret.encode()
    assert restored[no_secret] is None
    columns = {c["name"]: c for c in inspect(database).get_columns("staff_credential")}
    assert "totp_secret_plain" not in columns
    assert str(columns["totp_secret"]["type"]) == "BYTEA"
    assert key_rows(database) == keys_before

    caplog.clear()
    migrate.upgrade()

    for secret, (tenant_id, user_id) in users.items():
        assert_sealed(database, tenant_id, user_id, secret)
        assert revealed_by_owner(tenant_id, user_id) == secret
    assert key_rows(database) == keys_before  # the same keys, none added
    assert caplog.text.count("already has keys") == 2  # one line per tenant
    assert "created keys for" not in caplog.text


def test_a_secret_is_sealed_under_the_current_key_and_any_version_opens_on_the_way_back(
    database: Engine,
):
    older, newer = new_secret(), new_secret()
    go_down(WITH_KEYS)
    provider = get_key_provider()
    with database.begin() as conn:
        tenant_id = seed_tenant(conn, "rotated")
    provider.create_tenant_keys(tenant_id)
    assert provider.rotate(tenant_id, DATA) == 2
    with database.begin() as conn:
        first = seed_credential(conn, tenant_id, older)
        second = seed_credential(conn, tenant_id, newer)
    migrate.upgrade()
    assert_sealed(database, tenant_id, first, older, version=2)  # the current version
    assert revealed_by_owner(tenant_id, first) == older

    # A later rotation leaves one secret on version 2 and puts a new one on version 3.
    assert provider.rotate(tenant_id, DATA) == 3
    with tenant_session(system_ctx(tenant_id)) as s:
        credential = s.scalars(
            select(StaffCredential).where(StaffCredential.staff_user_id == second)
        ).one()
        credential.totp_secret = encrypt(newer, tenant_id=tenant_id, session=s)
    assert_sealed(database, tenant_id, second, newer, version=3)

    go_down(WITH_KEYS)
    restored = stored_secrets(database)
    assert (restored[first], restored[second]) == (older.encode(), newer.encode())


# ---- tenants the migration must treat with care --------------------------------------


def test_a_tenant_with_destroyed_keys_gets_no_new_keys_and_loses_its_secrets(
    database: Engine, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger=MIGRATION_LOGGER)
    doomed_secret, kept_secret = new_secret(), new_secret()
    go_down(WITH_KEYS)
    provider = get_key_provider()
    with database.begin() as conn:
        doomed, kept = seed_tenant(conn, "doomed"), seed_tenant(conn, "kept")
    provider.create_tenant_keys(doomed)
    provider.create_tenant_keys(kept)
    provider.destroy(doomed)
    with database.begin() as conn:
        doomed_user = seed_credential(conn, doomed, doomed_secret)
        kept_user = seed_credential(conn, kept, kept_secret)
    destroyed_before = [row for row in key_rows(database) if row[0] == doomed]

    migrate.upgrade()  # must not stop at the tenant it cannot serve

    # Destruction is final: no key came back, and a secret nobody can open is not kept
    # (nor left behind in the clear in a column that is dropped anyway).
    assert [row for row in key_rows(database) if row[0] == doomed] == destroyed_before
    assert all(row[3] is None for row in destroyed_before)
    assert stored_secrets(database)[doomed_user] is None
    assert leaked(everything_stored(database), doomed_secret.encode()) == []
    (warning,) = migration_warnings(caplog)
    assert str(doomed) in warning
    assert "destroyed" in warning
    assert doomed_secret not in caplog.text
    # The other tenant is served as usual.
    assert_sealed(database, kept, kept_user, kept_secret)
    assert revealed_by_owner(kept, kept_user) == kept_secret

    go_down(WITH_KEYS)
    restored = stored_secrets(database)
    assert restored[doomed_user] is None  # shredded stays shredded
    assert restored[kept_user] == kept_secret.encode()


def test_a_downgrade_leaves_null_the_secrets_of_a_tenant_shredded_since_the_upgrade(
    database: Engine, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger=MIGRATION_LOGGER)
    doomed_secret, kept_secret = new_secret(), new_secret()
    go_down(BEFORE_KEYS)
    with database.begin() as conn:
        doomed, kept = seed_tenant(conn, "doomed"), seed_tenant(conn, "kept")
        doomed_user = seed_credential(conn, doomed, doomed_secret)
        kept_user = seed_credential(conn, kept, kept_secret)
    migrate.upgrade()
    get_key_provider().destroy(doomed)  # shredded after its secret was sealed
    assert stored_secrets(database)[doomed_user] is not None  # the ciphertext remains
    caplog.clear()

    go_down(WITH_KEYS)  # must not stop at the tenant it can no longer open

    restored = stored_secrets(database)
    assert restored[doomed_user] is None
    assert restored[kept_user] == kept_secret.encode()
    (warning,) = migration_warnings(caplog)
    assert str(doomed) in warning
    assert "destroyed" in warning
    assert doomed_secret not in caplog.text


def test_keys_that_exist_are_kept_and_missing_purposes_are_added(
    database: Engine, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger=MIGRATION_LOGGER)
    plain = new_secret()
    go_down(WITH_KEYS)
    with database.begin() as conn:
        tenant_id = seed_tenant(conn, "partial")
        user_id = seed_credential(conn, tenant_id, plain)
    get_key_provider().create_tenant_keys(tenant_id)
    with database.begin() as conn:  # a tenant that never got its BIOMETRIC key
        _ = conn.execute(
            text("DELETE FROM tenant_key WHERE purpose = 'BIOMETRIC'"),
        )
    before = {row[1]: row for row in key_rows(database)}

    migrate.upgrade()

    after = {row[1]: row for row in key_rows(database)}
    assert sorted(after) == ALL_PURPOSES
    assert after["DATA"] == before["DATA"]  # the keys it had are untouched
    assert after["INDEX"] == before["INDEX"]
    assert_sealed(database, tenant_id, user_id, plain)
    assert "created keys for BIOMETRIC" in caplog.text
    assert "already has keys" not in caplog.text


# ---- seen from the command line ------------------------------------------------------


# configure_logging replaces the root handlers with one on the stream of the moment, and
# raises a couple of other loggers: put it all back, as the next test found it.
@pytest.fixture
def untouched_logging() -> Generator[None]:
    root = logging.getLogger()
    engine_logger = logging.getLogger("sqlalchemy.engine")
    access_logger = logging.getLogger("uvicorn.access")
    saved = (root.handlers[:], root.level, engine_logger.level, access_logger.disabled)
    yield
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])
    engine_logger.setLevel(saved[2])
    access_logger.disabled = saved[3]


# ``db upgrade`` is the documented way to migrate, and the log is where the migration
# says which tenants it gave keys and how many secrets it sealed or dropped.
def test_db_upgrade_shows_what_the_migration_did(
    database: Engine, untouched_logging: None
):
    go_down(WITH_KEYS)
    with database.begin() as conn:
        tenant_id = seed_tenant(conn, "ana")
        _ = seed_credential(conn, tenant_id, new_secret())

    result = CliRunner().invoke(cli_app, ["db", "upgrade"])

    assert result.exit_code == 0, result.output
    assert (
        f"tenant {tenant_id}: created keys for BIOMETRIC, DATA, INDEX" in result.output
    )
    assert f"tenant {tenant_id}: sealed 1 TOTP secret(s)" in result.output


# ---- when it cannot finish -----------------------------------------------------------


def test_a_downgrade_with_the_wrong_master_key_changes_nothing(
    database: Engine, monkeypatch: pytest.MonkeyPatch
):
    plain = new_secret()
    go_down(BEFORE_KEYS)
    with database.begin() as conn:
        tenant_id = seed_tenant(conn, "ana")
        user_id = seed_credential(conn, tenant_id, plain)
    migrate.upgrade()
    sealed = stored_secrets(database)[user_id]
    other = base64.b64encode(bytes(range(100, 132))).decode()
    monkeypatch.setenv("XOLOTL_MASTER_KEY", other)
    get_settings.cache_clear()
    try:
        with pytest.raises(DecryptionError):
            go_down(WITH_KEYS)
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()

    # Rolled back as a whole: still sealed, no half-built column, still at head.
    assert stored_secrets(database)[user_id] == sealed
    columns = {c["name"] for c in inspect(database).get_columns("staff_credential")}
    assert "totp_secret_plain" not in columns
    assert revealed_by_owner(tenant_id, user_id) == plain
    with database.connect() as conn:
        version = conn.scalar(text("SELECT version_num FROM alembic_version"))
    head = ScriptDirectory.from_config(migrate.alembic_config()).get_current_head()
    assert version == head


def test_the_migration_leaves_no_tenant_context_in_the_transaction_it_shares(
    database: Engine,
):
    # Migrations of one run share a transaction, and the settings that scope a session
    # to a tenant are transaction-local: the last tenant's would be left for the next.
    go_down(WITH_KEYS)
    with database.begin() as conn:
        _ = seed_credential(conn, seed_tenant(conn, "ana"), new_secret())
        _ = seed_credential(conn, seed_tenant(conn, "bob"), new_secret())
    migration = importlib.import_module("xolotl.migrations.versions.0004_encrypt_totp")
    with database.connect() as conn:
        transaction = conn.begin()
        try:
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            left = conn.execute(
                text(
                    """
                    SELECT current_setting('app.tenant_id', true),
                           current_setting('app.actor_type', true),
                           current_setting('app.actor_id', true),
                           current_setting('app.roles', true)
                    """
                )
            ).one()
        finally:
            transaction.rollback()  # the schema stays as it was: at revision 0003
    assert tuple(left) == ("", "", "", "")


def test_a_secret_that_was_not_sealed_stops_the_migration_with_the_plaintext_intact(
    database: Engine, monkeypatch: pytest.MonkeyPatch
):
    plain = new_secret()
    go_down(WITH_KEYS)
    with database.begin() as conn:
        tenant_id = seed_tenant(conn, "ana")
        user_id = seed_credential(conn, tenant_id, plain)

    # A sealing that stores nothing, as a defect in it might: the plaintext column must
    # not be dropped then, because it would be the only copy left.
    def seals_nothing(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(primitives, "encrypt_value", seals_nothing)
    with pytest.raises(RuntimeError, match="were not sealed"):
        migrate.upgrade()
    monkeypatch.undo()

    assert_refused_with_the_plaintext_intact(database, user_id, plain)


# The state a refused upgrade leaves: still at 0003, the plaintext column as it was.
def assert_refused_with_the_plaintext_intact(
    database: Engine, user_id: UUID, plain: str
) -> None:
    assert stored_secrets(database)[user_id] == plain.encode()
    columns = {c["name"] for c in inspect(database).get_columns("staff_credential")}
    assert "totp_secret_enc" not in columns
    with database.connect() as conn:
        version = conn.scalar(text("SELECT version_num FROM alembic_version"))
    assert version == "0003_tenant_key"


# A sealed value is stored, which is not the same as being recoverable: the plaintext
# column goes next, and a secret that cannot be opened again would be lost with it.
@pytest.mark.parametrize("defect", ["wrong_aad", "another_plaintext"])
def test_a_sealed_secret_that_cannot_be_opened_again_stops_the_migration(
    database: Engine, monkeypatch: pytest.MonkeyPatch, defect: str
):
    plain = new_secret()
    go_down(WITH_KEYS)
    with database.begin() as conn:
        tenant_id = seed_tenant(conn, "ana")
        user_id = seed_credential(conn, tenant_id, plain)
    real = primitives.encrypt_value

    def seals_for_nobody(
        dek: bytes, *, version: int, plaintext: bytes, aad: bytes
    ) -> bytes:
        if defect == "wrong_aad":
            return real(dek, version=version, plaintext=plaintext, aad=b"someone else")
        return real(dek, version=version, plaintext=b"not the secret", aad=aad)

    monkeypatch.setattr(primitives, "encrypt_value", seals_for_nobody)
    with pytest.raises(RuntimeError, match="cannot be opened again") as refused:
        migrate.upgrade()
    monkeypatch.undo()

    assert str(tenant_id) in str(refused.value)
    assert plain not in str(refused.value)
    assert_refused_with_the_plaintext_intact(database, user_id, plain)


# ---- the owner a managed server gives you --------------------------------------------

SCRATCH_DATABASE = "xolotl_b4_scratch"
SCRATCH_OWNER = "xolotl_b4_owner"


def drop_scratch(admin_engine: Engine) -> None:
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        _ = conn.execute(text("DROP DATABASE IF EXISTS xolotl_b4_scratch WITH (FORCE)"))
        _ = conn.execute(text("DROP ROLE IF EXISTS xolotl_b4_owner"))


def server_admin_may_create(admin_engine: Engine) -> bool:
    with admin_engine.connect() as conn:
        return bool(
            conn.scalar(
                text(
                    """
                    SELECT rolsuper OR (rolcreaterole AND rolcreatedb)
                    FROM pg_roles WHERE rolname = current_user
                    """
                )
            )
        )


# The test below is the only guard that the migration scopes its statements to a tenant,
# so a server it cannot run on is a failure of the run and not a skipped test: point
# XOLOTL_TEST_DATABASE_ADMIN_URL at a role that can create roles and databases, or leave
# it unset for the container.
def require_server_admin(*, allowed: bool) -> None:
    if not allowed:
        pytest.fail(
            "admin role cannot create roles or databases: the scoping test needs both"
        )


def test_the_scoping_test_fails_the_run_when_the_server_cannot_run_it():
    require_server_admin(allowed=True)
    # Broad on purpose: a skip is an outcome exception too, and must not pass for this.
    with pytest.raises(BaseException, match="the scoping test needs both") as refused:
        require_server_admin(allowed=False)
    assert refused.type is pytest.fail.Exception


@pytest.fixture
def bound_owner_database(
    admin_engine: Engine, admin_url: str, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[Engine, Engine]]:
    """Create a database owned by a role that row-level security binds.

    The session database belongs to a superuser, whom row-level security never binds,
    so a migration that forgets to scope its statements to a tenant passes there and
    finds no row on a managed server (an owner with neither SUPERUSER nor BYPASSRLS,
    which is what ``bootstrap`` is documented to need). The migrations run against this
    one as that owner: the alembic environment takes its engine from ``db``.

    Yields:
        The owner's engine and a superuser's engine on the same database.

    """
    require_server_admin(allowed=server_admin_may_create(admin_engine))
    drop_scratch(admin_engine)  # whatever an interrupted run left behind
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        _ = conn.execute(
            text(
                """
                CREATE ROLE xolotl_b4_owner LOGIN PASSWORD 'owner-password'
                NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
                """
            )
        )
        _ = conn.execute(
            text("CREATE DATABASE xolotl_b4_scratch OWNER xolotl_b4_owner")
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


def test_the_migration_scopes_every_statement_to_a_tenant_for_an_owner_that_rls_binds(
    bound_owner_database: tuple[Engine, Engine],
):
    owner, root = bound_owner_database
    config = migrate.alembic_config()
    command.upgrade(config, WITH_KEYS)
    plain = [new_secret() for _ in range(2)]
    with root.begin() as conn:
        ana, bob = seed_tenant(conn, "ana"), seed_tenant(conn, "bob")
        ana_user = seed_credential(conn, ana, plain[0])
        bob_user = seed_credential(conn, bob, plain[1])
    with owner.connect() as conn:  # the premise: this owner sees no tenant at all
        assert conn.scalar(text("SELECT count(*) FROM tenant")) == 0
    with root.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM tenant")) == 2

    command.upgrade(config, "head")

    for tenant_id in (ana, bob):
        assert purposes_of(root, tenant_id) == ALL_PURPOSES
    assert_sealed(root, ana, ana_user, plain[0])
    assert_sealed(root, bob, bob_user, plain[1])

    command.downgrade(config, WITH_KEYS)

    restored = stored_secrets(root)
    assert (restored[ana_user], restored[bob_user]) == (
        plain[0].encode(),
        plain[1].encode(),
    )
