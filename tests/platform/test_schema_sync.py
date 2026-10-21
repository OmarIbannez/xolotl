"""The ORM metadata and the migrated schema must agree (no drift)."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import (
    Engine,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    UniqueConstraint,
)
from sqlalchemy.sql.schema import SchemaItem

from xolotl.models import metadata
from xolotl.platform.partitions import is_partition_name, partitioned_tables
from xolotl.platform.tenancy.registry import SHARED_TABLES

# Tables keyed by a natural composite key carry no ``id``: nothing references them
# through ``(tenant_id, id)``, so they have no such unique. A partitioned table
# (``partitioned_tables()``) has an ``id`` but cannot carry ``UNIQUE (tenant_id,
# id)`` without its range column, so it is left out of the check too.
NATURALLY_KEYED = frozenset({
    "agg_bucket",
    "agg_declaration",
    "agg_distinct",
    "event_consumption",
    "idempotency_key",
    "private_list_entry_delta",
    "screening_list_set_member",
    "transaction_parked",
    "transaction_ref",
    "workflow_step",
})


def _include_name(name: str | None, type_: str, _parent_names: object) -> bool:
    if type_ == "table" and name is not None:
        # Partitions are created by ensure_partitions, not declared as models.
        return not (is_partition_name(name) or name == "alembic_version")
    return True


def _include_object(
    item: SchemaItem,
    _name: str | None,
    _type: str,
    reflected: bool,  # ruff: ignore[boolean-type-hint-positional-argument]
    _compare_to: SchemaItem | None,
) -> bool:
    # Postgres clones a foreign key to a partitioned table onto each partition.
    if reflected and isinstance(item, ForeignKeyConstraint):
        return not is_partition_name(item.referred_table.name)
    return True


def test_orm_metadata_matches_the_migrated_schema(admin_engine: Engine):
    with admin_engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "compare_type": True,
                "compare_server_default": True,
                "include_name": _include_name,
                "include_object": _include_object,
            },
        )
        diffs = compare_metadata(ctx, metadata())
    assert diffs == [], diffs


def test_every_table_with_tenant_id_uses_the_composite_unique():
    partitioned = {spec.table for spec in partitioned_tables()}
    assert {"audit_event", "transaction"} <= partitioned
    for table in metadata().sorted_tables:
        if (
            "tenant_id" not in table.c
            or table.name in SHARED_TABLES
            or table.name in partitioned
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
