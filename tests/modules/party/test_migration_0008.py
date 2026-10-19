"""Migration 0008: the ten party tables.

The round trip moves the session database back to 0007 and forward again with real
commits; the ``database`` fixture always brings it back to head afterwards, so the rest
of the session still sees a database at head. ``verification_check`` has no writer in
SP-02 besides the isolation factory, so its composite foreign keys are pinned here.
"""

from collections.abc import Generator

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.helpers import make_tenant, system_ctx
from xolotl.modules.party.enums import VerificationResult
from xolotl.modules.party.models import VerificationCheck
from xolotl.platform import migrate
from xolotl.platform.connectors import CheckType, ConnectorMode
from xolotl.platform.db import tenant_session
from xolotl.platform.ids import uuid7

REVISION = "0008_party"
BEFORE = "0007_connectors"
PARTY_TABLES = [
    "party",
    "person",
    "organization",
    "verification_check",
    "party_identifier",
    "party_contact",
    "party_address",
    "party_relationship",
    "document",
    "customer_relationship",
]


def go_down(revision: str) -> None:
    command.downgrade(migrate.alembic_config(), revision)


def table_names(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(inspect(conn).get_table_names())


# The test database, which a test may move back in time; it always ends at head.
@pytest.fixture
def database(admin_engine: Engine) -> Generator[Engine]:
    try:
        yield admin_engine
    finally:
        migrate.upgrade()


def test_0008_follows_0007():
    script = ScriptDirectory.from_config(migrate.alembic_config())
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == BEFORE


def test_0008_round_trips(database: Engine):
    go_down(BEFORE)
    assert not set(PARTY_TABLES) & table_names(database)
    migrate.upgrade()
    assert set(PARTY_TABLES) <= table_names(database)
    with database.connect() as conn:
        fks = {
            fk["name"] for fk in inspect(conn).get_foreign_keys("verification_check")
        }
        rows = conn.execute(
            text(
                """
                SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
                WHERE relname = ANY(:names)
                """
            ),
            {"names": PARTY_TABLES},
        ).all()
    assert {
        "fk_verification_check_policy_snapshot",
        "fk_verification_check_connector_call",
    } <= fks
    assert sorted(tuple(row) for row in rows) == sorted(
        (name, True, True) for name in PARTY_TABLES
    )


def test_a_check_with_an_unknown_connector_call_is_refused_by_the_foreign_key(
    engine: Engine,
):
    tenant_id = make_tenant("acme")
    with (
        pytest.raises(IntegrityError, match="fk_verification_check_connector_call"),
        tenant_session(system_ctx(tenant_id)) as s,
    ):
        s.add(
            VerificationCheck(
                tenant_id=tenant_id,
                check_type=CheckType.RFC_VALIDATION,
                provider="sat_rfc",
                mode=ConnectorMode.SANDBOX,
                result=VerificationResult.PASS,
                connector_call_id=uuid7(),
            )
        )
        s.flush()
