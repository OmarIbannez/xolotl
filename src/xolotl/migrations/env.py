from alembic import context

from xolotl.models import metadata
from xolotl.platform.db import get_admin_engine


def include_name(name: str | None, type_: str, _parent_names: object) -> bool:
    # Autogenerate only. The monthly partitions of audit_event are created by
    # ensure_partitions and no model declares them, so a comparison would propose to
    # drop them. tests/platform/test_schema_sync.py filters the same names.
    if type_ == "table" and name is not None:
        return not name.startswith("audit_event_")
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
    )
    with context.begin_transaction():
        context.run_migrations()
