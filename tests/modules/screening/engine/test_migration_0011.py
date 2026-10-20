"""Migration 0011: the ``(tenant_id, run_id)`` index on ``screening_hit``.

The run view, the replay and the purge handler read a tenant's hits by run, and 0010
left ``screening_hit`` with no index on that key (the composite foreign key to
``screening_run`` creates none), so each read scanned the tenant's hits. The round trip
moves the session database back to 0010 and forward again with real commits; the
``database`` fixture always brings it back to head afterwards.
"""

from collections.abc import Generator

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text

from tests.helpers import system_ctx
from tests.isolation import factories
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import migrate
from xolotl.platform.db import tenant_session
from xolotl.platform.ids import uuid7

REVISION, BEFORE = "0011_screening_hit_run_index", "0010_screening"
INDEX = "ix_screening_hit_run"


def go_down(revision: str) -> None:
    command.downgrade(migrate.alembic_config(), revision)


# The test database, which a test may move back in time; it always ends at head.
@pytest.fixture
def database(admin_engine: Engine) -> Generator[Engine]:
    try:
        yield admin_engine
    finally:
        migrate.upgrade()


def _index_definition(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return conn.scalar(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
            {"name": INDEX},
        )


def test_0011_follows_0010():
    script = ScriptDirectory.from_config(migrate.alembic_config())
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == BEFORE


def test_the_hit_run_index_exists(admin_engine: Engine):
    definition = _index_definition(admin_engine)
    assert definition is not None
    assert "ON public.screening_hit USING btree (tenant_id, run_id)" in definition


def test_downgrade_drops_the_index_and_upgrade_restores_it(database: Engine):
    go_down(BEFORE)
    assert _index_definition(database) is None
    migrate.upgrade()
    assert _index_definition(database) is not None


def test_the_run_view_reads_a_tenants_hits_through_the_index(
    engine: Engine, tenant: CreatedTenant
):
    # The app role's query names the run only; the row-level security policy adds the
    # tenant, and the planner must be able to serve both through the index. With a
    # sequential scan ruled out, any other plan would fall back to the primary key.
    tenant_id = tenant.tenant_id
    with tenant_session(system_ctx(tenant_id)) as s:
        factories.screening_hit(s, tenant_id)
        _ = s.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            s.scalars(
                text("EXPLAIN SELECT * FROM screening_hit WHERE run_id = :run_id"),
                {"run_id": uuid7()},
            ).all()
        )
    assert f"Index Scan using {INDEX} on screening_hit" in plan, plan
