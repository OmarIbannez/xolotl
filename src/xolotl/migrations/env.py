from alembic import context
from sqlalchemy import ForeignKeyConstraint
from sqlalchemy.sql.schema import SchemaItem

from xolotl.models import metadata
from xolotl.platform.db import get_admin_engine
from xolotl.platform.partitions import is_partition_name


def include_name(name: str | None, type_: str, _parent_names: object) -> bool:
    # Autogenerate only. The partitions of every registered partitioned table are
    # created by migrations and ensure_partitions and no model declares them, so a
    # comparison would propose to drop them. xolotl.models, imported above,
    # registers every such table. tests/platform/test_schema_sync.py filters the
    # same names.
    if type_ == "table" and name is not None:
        return not is_partition_name(name)
    return True


def include_object(
    item: SchemaItem,
    _name: str | None,
    _type: str,
    reflected: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - Alembic passes it positionally
    _compare_to: SchemaItem | None,
) -> bool:
    # Autogenerate only. Postgres clones a foreign key that references a partitioned
    # table (transaction_ref's to transaction) onto each of its partitions, and no
    # model declares the clones. tests/platform/test_schema_sync.py filters the same.
    if reflected and isinstance(item, ForeignKeyConstraint):
        return not is_partition_name(item.referred_table.name)
    return True


with get_admin_engine().connect() as connection:
    # compare_type and compare_server_default only matter to `alembic revision
    # --autogenerate` and `alembic check`: running a migration ignores them. Server
    # defaults are compared so that a changed `DEFAULT`, an identity column or a
    # sequence is proposed like any other change, the way the schema-sync test does.
    context.configure(
        connection=connection,
        target_metadata=metadata(),
        compare_type=True,
        compare_server_default=True,
        include_name=include_name,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()
