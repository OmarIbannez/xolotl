"""The ORM metadata and the migrated schema must agree (no drift)."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, PrimaryKeyConstraint, UniqueConstraint

from xolotl.models import metadata
from xolotl.platform.tenancy.registry import SHARED_TABLES

# Tables keyed by a natural composite key carry no ``id``: nothing references them
# through ``(tenant_id, id)``, so they have no such unique.
NATURALLY_KEYED = frozenset({"event_consumption", "idempotency_key", "workflow_step"})


def _include_name(name: str | None, type_: str, _parent_names: object) -> bool:
    if type_ == "table" and name is not None:
        # Partitions are created by ensure_partitions, not declared as models.
        return not (name.startswith("audit_event_") or name == "alembic_version")
    return True


def test_orm_metadata_matches_the_migrated_schema(admin_engine: Engine):
    with admin_engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "compare_type": True,
                "compare_server_default": True,
                "include_name": _include_name,
            },
        )
        diffs = compare_metadata(ctx, metadata())
    assert diffs == [], diffs


def test_every_table_with_tenant_id_uses_the_composite_unique():
    for table in metadata().sorted_tables:
        if (
            "tenant_id" not in table.c
            or table.name in SHARED_TABLES
            or table.name == "audit_event"
        ):
            continue
        if table.name in NATURALLY_KEYED:
            assert "id" not in table.c, table.name
            continue
        unique_sets = [
            tuple(c.name for c in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint | PrimaryKeyConstraint)
        ]
        assert ("tenant_id", "id") in unique_sets, table.name
